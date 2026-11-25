"""Bulut GPU worker istemcisi.

Worker (worker_cloud/worker_app.py) Kaggle/Colab'da (kişisel test) veya ücretli bir GPU
sağlayıcısında (ürün) çalışır ve cloudflared tüneliyle bir adres verir. Bu adres
config.yaml'da assets.cloud_worker_url'e, paylaşılan gizli anahtar .env'de KURGU_WORKER_TOKEN'a yazılır.

Protokol:
  GET  /health                 -> {"models": {"image": [...], "video": [...], "music": [...]}}
  POST /jobs {kind, model, prompt, width, height, duration, seed} -> {"id": ...}
  GET  /jobs/{id}              -> {"status": "queued|running|done|failed", "error": ...}
  GET  /jobs/{id}/result       -> dosya (png / mp4 / wav)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from ..core.config import get_config, require_model
from .base import AssetRequest, AssetResult, ProviderUnavailable
from .motion import fit_clip, ken_burns


def _headers() -> dict:
    from .stock import _load_dotenv

    _load_dotenv()
    token = os.environ.get("KURGU_WORKER_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


class CloudWorkerProvider:
    name = "cloud_worker"

    def __init__(self):
        self._health: dict | None = None

    @property
    def url(self) -> str:
        url = get_config().assets.cloud_worker_url
        if not url:
            raise ProviderUnavailable("assets.cloud_worker_url ayarlanmamış (config.yaml)")
        return url.rstrip("/")

    def check(self):
        try:
            r = httpx.get(f"{self.url}/health", headers=_headers(), timeout=8)
            r.raise_for_status()
            self._health = r.json()
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"Bulut worker'a ulaşılamadı: {e}") from e

    def _models(self, kind: str) -> list[str]:
        if self._health is None:
            self.check()
        return list((self._health or {}).get("models", {}).get(kind, []))

    def run_job(self, payload: dict, dst: Path, timeout: float = 1800) -> Path:
        r = httpx.post(f"{self.url}/jobs", json=payload, headers=_headers(), timeout=30)
        r.raise_for_status()
        job = r.json()["id"]
        t0 = time.time()
        while True:
            s = httpx.get(f"{self.url}/jobs/{job}", headers=_headers(), timeout=30).json()
            if s["status"] == "done":
                break
            if s["status"] == "failed":
                raise RuntimeError(f"Worker işi başarısız: {s.get('error')}")
            if time.time() - t0 > timeout:
                raise TimeoutError("Worker işi zaman aşımına uğradı")
            time.sleep(3)
        with httpx.stream("GET", f"{self.url}/jobs/{job}/result", headers=_headers(), timeout=300) as resp:
            resp.raise_for_status()
            with open(dst, "wb") as f:
                for chunk in resp.iter_bytes(1 << 16):
                    f.write(chunk)
        return dst

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        cfg = get_config().assets
        key = req.cache_key(self.name)
        want_video = req.kind in ("video", "any") and cfg.cloud_video_model in self._models("video")
        if want_video:
            require_model(cfg.cloud_video_model, "video")
            raw = out_dir / f"cw_vid_{key}.mp4"
            if not raw.exists():
                self.run_job({
                    "kind": "video", "model": cfg.cloud_video_model, "prompt": req.full_prompt(),
                    "width": req.width, "height": req.height, "duration": min(req.duration, 5.0), "seed": req.seed,
                }, raw)
            clip = out_dir / f"cw_vid_{key}_{req.width}x{req.height}_{req.duration:.1f}.mp4"
            fit_clip(raw, clip, req.duration, req.width, req.height)
            return AssetResult(clip, f"{self.name}:{cfg.cloud_video_model}", "video")

        if cfg.cloud_image_model not in self._models("image"):
            raise ProviderUnavailable(f"Worker'da {cfg.cloud_image_model} yok")
        require_model(cfg.cloud_image_model, "image")
        img = out_dir / f"cw_img_{key}.png"
        if not img.exists():
            self.run_job({
                "kind": "image", "model": cfg.cloud_image_model, "prompt": req.full_prompt(),
                "width": req.width, "height": req.height, "seed": req.seed,
            }, img)
        clip = out_dir / f"cw_kb_{key}_{req.width}x{req.height}_{req.duration:.1f}.mp4"
        ken_burns(img, clip, req.duration, req.width, req.height)
        return AssetResult(clip, f"{self.name}:{cfg.cloud_image_model}", "image", None, img)
