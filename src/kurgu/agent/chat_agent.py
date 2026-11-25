"""Sohbet ajanı: kullanıcının doğal dildeki kurgu isteğini araç çağrılarına çevirir.

Ollama'nın yerleşik araç çağırma (tool calling) özelliği kullanılır. Ajan en fazla
MAX_STEPS tur araç çağırır; değişiklik olduysa timeline yeni sürüm olarak kaydedilir
ve yeni eklenen b-roll'lar üretilir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from ..core.config import get_config, require_model
from ..core.project_store import Project
from .llm import _post_chat, _strip_think
from .planner import format_sentences, kept_sentences
from .tools import TOOLS, ToolContext, dispatch

MAX_STEPS = 8

SYSTEM = """Sen bir video kurgu asistanısın. Kullanıcının isteğini araçları kullanarak timeline üzerinde uygula.
- Zamanlar ÇIKTI videosuna göredir (kullanıcı "ilk 5 saniye", "0:20" der). 0:20 = 20 saniye.
- Bir ifadenin yerini bilmiyorsan önce find_text kullan.
- B-roll tarifleri ("visual") İngilizce, somut ve sinematik olmalı.
- Gereksiz araç çağırma; istek bittiğinde kullanıcıya ne yaptığını Türkçe ve kısaca anlat.
- İsteği yerine getiremiyorsan nedenini söyle."""


@dataclass
class ChatResult:
    reply: str
    actions: list[str] = field(default_factory=list)
    version: int | None = None
    broll_report: dict | None = None
    history: list[dict] = field(default_factory=list)


def _context_message(ctx: ToolContext) -> str:
    from .tools import t_overview, NoArgs

    ov = t_overview(ctx, NoArgs())
    lines = [f"Mevcut durum: {json.dumps(ov, ensure_ascii=False)}"]
    if ctx.tr:
        sents = kept_sentences(ctx.tr, ctx.tl.time_map())
        text = format_sentences(sents)
        if len(text) > 9000:
            text = text[:9000] + "\n..."
        lines.append("Transkript ([cümle no] (çıktı saniyesi) metin):\n" + text)
    return "\n\n".join(lines)


def chat(
    project: Project,
    message: str,
    history: list[dict] | None = None,
    progress: Callable[[str, float, str], None] | None = None,
    generate_assets: bool = True,
) -> ChatResult:
    cfg = get_config()
    model = cfg.models.llm
    require_model(model)
    tl = project.timeline()
    try:
        tr = project.transcript()
    except FileNotFoundError:
        tr = None
    ctx = ToolContext(project, tl.model_copy(deep=True), tr)

    msgs: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {"role": "system", "content": _context_message(ctx)},
        *(history or [])[-10:],
        {"role": "user", "content": message},
    ]
    reply = ""
    for step in range(MAX_STEPS):
        if progress:
            progress("agent", step / MAX_STEPS, "düşünüyor")
        data = _post_chat({
            "model": model,
            "messages": msgs,
            "tools": [t.schema() for t in TOOLS],
            "stream": False,
            "think": False,
            "keep_alive": cfg.ollama.keep_alive,
            "options": {"temperature": 0.2, "num_ctx": 16384},
        })
        msg = data.get("message", {})
        calls = msg.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls} if calls
                    else {"role": "assistant", "content": msg.get("content", "")})
        if not calls:
            reply = _strip_think(msg.get("content", ""))
            break
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            result = dispatch(ctx, name, fn.get("arguments"))
            if progress:
                progress("agent", (step + 0.5) / MAX_STEPS, f"{name}: {result[:50]}")
            msgs.append({"role": "tool", "content": result, "tool_name": name})
    else:
        reply = "İşlem adım sınırına ulaştı; yapılanlar: " + "; ".join(ctx.actions)

    result = ChatResult(reply=reply or "Tamam.", actions=ctx.actions)
    if ctx.changed:
        if generate_assets and ctx.broll_dirty:
            from ..generate.router import fill_brolls

            rep = fill_brolls(project, ctx.tl, progress, ids=ctx.broll_dirty)
            result.broll_report = {"filled": rep.filled, "failed": rep.failed}
        result.version = project.save_timeline(ctx.tl, note="Sohbet: " + "; ".join(ctx.actions)[:200])
    result.history = [*(history or []), {"role": "user", "content": message},
                      {"role": "assistant", "content": result.reply}]
    return result
