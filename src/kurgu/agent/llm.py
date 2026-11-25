"""Ollama istemcisi: yapılandırılmış (JSON şemalı) çıktı, düz metin ve görsel anlama.

Küçük yerel modeller serbest biçimli araç çağrılarında hata yapabilir. Bu yüzden planlar
JSON şemasıyla istenir (Ollama `format`), Pydantic ile doğrulanır; geçersizse hata mesajı
modele geri verilip yeniden denenir.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..core.config import get_config, require_model

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def _prompt_tokens(payload: dict) -> int:
    """Kaba token tahmini (Türkçe metinde ~2.5 karakter/token)."""
    chars = sum(len(str(m.get("content", ""))) for m in payload.get("messages", []))
    chars += len(json.dumps(payload.get("format") or payload.get("tools") or ""))
    return int(chars / 2.5) + 64


def _loaded_ctx(model: str) -> int | None:
    """Ollama'nın modeli gerçekte kaç token bağlamla yüklediği (iGPU'da belleğe göre küçültülebilir)."""
    cfg = get_config().ollama
    try:
        for m in httpx.get(f"{cfg.host}/api/ps", timeout=3).json().get("models", []):
            if m.get("name") in (model, f"{model}:latest") and m.get("context_length"):
                return int(m["context_length"])
    except Exception:
        pass
    return None


def _ctx_for(payload: dict, cap: int = 16384) -> int:
    """Bağlam = prompt + çıktı payı. Küçük KV önbelleği = daha hızlı, daha az bellek."""
    out = int(payload.get("options", {}).get("num_predict") or 2048)
    need = _prompt_tokens(payload) + out + 256
    return min(cap, max(4096, ((need + 2047) // 2048) * 2048))


def _fit_output(payload: dict) -> None:
    """Prompt + çıktı modelin GERÇEK bağlamına sığmazsa Ollama 'context shift' yapar: talimatlar
    silinir ve model kontrolsüzce üretmeye devam eder. Bunu önlemek için çıktı sınırını kısalt."""
    ctx = _loaded_ctx(payload.get("model", ""))
    if not ctx:
        return
    opts = payload["options"]
    room = ctx - _prompt_tokens(payload) - 128
    if opts.get("num_predict", 0) > room:
        opts["num_predict"] = max(384, room)


FALLBACK_LLM = "qwen3:8b"  # varsayılan model indirilmemişse kullanılır


def _post_chat(payload: dict) -> dict:
    from ..core.ollama_server import ensure_ollama

    cfg = get_config().ollama
    opts = payload.setdefault("options", {})
    opts["num_ctx"] = _ctx_for(payload, opts.get("num_ctx", 16384))
    if payload.get("model") in cfg.cpu_models:
        opts.setdefault("num_gpu", 0)
    _fit_output(payload)
    # Tek bir çağrı sonsuza kadar sürmesin (bellek darboğazında dakikalarca takılabiliyor)
    timeout = httpx.Timeout(cfg.timeout, connect=10.0)
    try:
        r = httpx.post(f"{cfg.host}/api/chat", json=payload, timeout=timeout)
    except httpx.ConnectError:
        if not ensure_ollama():
            raise LLMError(f"Ollama'ya ulaşılamadı ({cfg.host}). Ollama kurulu mu?")
        r = httpx.post(f"{cfg.host}/api/chat", json=payload, timeout=timeout)
    except httpx.TimeoutException as e:
        raise LLMError(f"LLM {cfg.timeout:.0f} sn içinde yanıt vermedi (bellek yetersiz olabilir). "
                       "Diğer ağır işlemleri kapatıp tekrar deneyin.") from e
    except httpx.HTTPError as e:
        raise LLMError(f"Ollama hatası ({cfg.host}): {e}") from e
    if r.status_code == 400 and "think" in payload and "think" in r.text.lower():
        payload = {k: v for k, v in payload.items() if k != "think"}  # düşünme modu desteklenmiyor
        r = httpx.post(f"{cfg.host}/api/chat", json=payload, timeout=timeout)
    if r.status_code == 404 and payload.get("model") != FALLBACK_LLM:
        # büyük model indirilmemişse (ör. qwen3:30b-a3b, ~18 GB) küçük modelle devam et
        payload = {**payload, "model": FALLBACK_LLM, "options": {k: v for k, v in opts.items() if k != "num_gpu"}}
        r = httpx.post(f"{cfg.host}/api/chat", json=payload, timeout=timeout)
    if r.status_code == 404:
        raise LLMError(f"Model yok: {payload['model']}. Kurmak için: ollama pull {payload['model']}")
    if r.status_code >= 400:
        raise LLMError(f"Ollama hatası {r.status_code}: {r.text[:300]}")
    return r.json()


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def chat_json(
    messages: list[dict],
    schema: type[T],
    model: str | None = None,
    temperature: float = 0.3,
    num_ctx: int = 16384,
    retries: int = 2,
    max_tokens: int = 1800,
    think: bool = False,
) -> T:
    cfg = get_config()
    model = model or cfg.models.llm
    require_model(model)
    msgs = list(messages)
    last_err: Exception | None = None
    if think:
        max_tokens += 2500  # akıl yürütme token'ları da num_predict'e sayılır
    for _ in range(retries + 1):
        data = _post_chat({
            "model": model,
            "messages": msgs,
            "format": schema.model_json_schema(),
            "stream": False,
            "think": think,
            "keep_alive": cfg.ollama.keep_alive,
            # num_predict: kontrolsüz uzun çıktıyı (iGPU'da ~10 token/sn) sınırlar
            "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": max_tokens},
        })
        content = _strip_think(data.get("message", {}).get("content", ""))
        try:
            return schema.model_validate_json(content)
        except ValidationError as e:
            last_err = e
            if data.get("done_reason") == "length":
                # Çıktı uzunluk sınırında kesildi: aynı isteği tekrarlamak aynı sonucu verir.
                # Bozuk çıktıyı geri göndermeden, daha kısa yazmasını iste.
                msgs = list(messages) + [{"role": "user", "content": (
                    "ÖNEMLİ: Önceki yanıtın çok uzun olduğu için kesildi. Listelerde daha az öğe ver, "
                    "her alanı kısa tut ve sadece geçerli JSON döndür.")}]
            else:
                msgs = list(messages) + [
                    {"role": "assistant", "content": content[:1200]},
                    {"role": "user", "content": f"Çıktın şemaya uymuyor:\n{str(e)[:600]}\nSadece geçerli JSON döndür."},
                ]
    raise LLMError(f"Model geçerli JSON üretemedi: {str(last_err)[:300]}")


def chat_text(messages: list[dict], model: str | None = None, temperature: float = 0.5, num_ctx: int = 8192) -> str:
    cfg = get_config()
    model = model or cfg.models.llm
    require_model(model)
    data = _post_chat({
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": cfg.ollama.keep_alive,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    })
    return _strip_think(data.get("message", {}).get("content", ""))


def describe_images(images: list[Path], prompt: str, model: str | None = None, schema: type[T] | None = None):
    """Görsel dil modeliyle (Qwen3-VL) bir veya birkaç kareyi yorumlar."""
    cfg = get_config()
    model = model or cfg.models.vlm
    require_model(model)
    b64 = [base64.b64encode(Path(p).read_bytes()).decode() for p in images]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": b64}],
        "stream": False,
        "think": False,
        "keep_alive": cfg.ollama.keep_alive,
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    if schema:
        payload["format"] = schema.model_json_schema()
    data = _post_chat(payload)
    content = _strip_think(data.get("message", {}).get("content", ""))
    if schema:
        try:
            return schema.model_validate_json(content)
        except ValidationError:
            m = re.search(r"\{.*\}", content, re.S)
            if m:
                return schema.model_validate(json.loads(m.group(0)))
            raise
    return content


def available_models() -> set[str]:
    from ..core.ollama_server import ensure_ollama

    cfg = get_config().ollama
    ensure_ollama()
    try:
        tags = httpx.get(f"{cfg.host}/api/tags", timeout=3).json()
        return {m["name"] for m in tags.get("models", [])}
    except Exception:
        return set()


def has_model(name: str) -> bool:
    names = available_models()
    return name in names or f"{name}:latest" in names
