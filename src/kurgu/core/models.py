"""ModelManager: ağır modelleri tembel yükler ve bellek bütçesini korur.

32 GB paylaşımlı bellekte Whisper, görsel üretim ve video modelleri aynı anda tutulamaz.
Bu yüzden aynı anda en fazla `memory.max_heavy_models` model bellekte kalır; yeni bir model
istendiğinde en eski olan boşaltılır. Ollama modelleri ayrı süreçte çalışır ve
`unload_ollama()` ile boşaltılır.
"""

from __future__ import annotations

import gc
import threading
from collections import OrderedDict
from typing import Any, Callable

import httpx

from .config import get_config, require_model


class ModelManager:
    def __init__(self, max_heavy: int):
        self.max_heavy = max(1, max_heavy)
        self._models: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str, loader: Callable[[], Any], registry_name: str | None = None) -> Any:
        """Model önbellekte yoksa `loader` ile yükler. Lisans kontrolü kayıt adıyla yapılır."""
        with self._lock:
            if key in self._models:
                self._models.move_to_end(key)
                return self._models[key]
            if registry_name:
                require_model(registry_name)
            while len(self._models) >= self.max_heavy:
                self._evict_oldest()
            unload_ollama()  # yerel modeller için Ollama'nın tuttuğu belleği boşalt
            model = loader()
            self._models[key] = model
            return model

    def _evict_oldest(self):
        key, model = self._models.popitem(last=False)
        close = getattr(model, "close", None) or getattr(model, "unload", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        del model
        gc.collect()

    def unload_all(self):
        with self._lock:
            while self._models:
                self._evict_oldest()

    def loaded(self) -> list[str]:
        return list(self._models)


_manager: ModelManager | None = None


def manager() -> ModelManager:
    global _manager
    if _manager is None:
        _manager = ModelManager(get_config().memory.max_heavy_models)
    return _manager


def unload_ollama():
    """Ollama'da yüklü modelleri bellekten çıkarır (keep_alive=0)."""
    cfg = get_config().ollama
    try:
        ps = httpx.get(f"{cfg.host}/api/ps", timeout=3).json()
        for m in ps.get("models", []):
            httpx.post(
                f"{cfg.host}/api/generate",
                json={"model": m["name"], "keep_alive": 0},
                timeout=30,
            )
    except Exception:
        pass
