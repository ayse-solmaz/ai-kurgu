"""Yerel görsel üretimi: OpenVINO GenAI (Intel CPU/iGPU/NPU) ile FLUX.1-schnell veya SDXL.

Model dosyaları büyük olduğu için render sırasında sessizce indirilmez;
`kurgu models pull image` ile önceden indirilir.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..core.config import get_config, require_model
from ..core.models import manager
from .base import AssetRequest, AssetResult, ProviderUnavailable
from .motion import ken_burns


def model_dir(name: str) -> Path:
    cfg = get_config()
    return cfg.path(cfg.models_cache) / "image" / name


def is_downloaded(name: str) -> bool:
    d = model_dir(name)
    return (d / ".kurgu_complete").exists()  # indirme tamamlanınca yazılır


def pull(name: str | None = None, progress=None) -> Path:
    """Model dosyalarını Hugging Face'ten indirir."""
    from huggingface_hub import snapshot_download

    name = name or get_config().models.image
    info = require_model(name, "image")
    if info.remote_only or not info.repo:
        raise ProviderUnavailable(f"{name} yerelde çalıştırılamaz (yalnızca bulut worker).")
    d = model_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    snapshot_download(info.repo, local_dir=str(d))
    (d / ".kurgu_complete").write_text(info.repo, encoding="utf-8")
    return d


def _sizes(req: AssetRequest, long_side: int = 1024) -> tuple[int, int]:
    """Hedef en-boy oranına uygun, 16'nın katı üretim boyutu."""
    ar = req.width / req.height
    if ar >= 1:
        w, h = long_side, long_side / ar
    else:
        w, h = long_side * ar, long_side
    return int(round(w / 16)) * 16, int(round(h / 16)) * 16


def _load_pipeline(name: str):
    import openvino_genai as ov_genai

    device = get_config().devices.openvino
    try:
        return ov_genai.Text2ImagePipeline(str(model_dir(name)), device)
    except RuntimeError:
        if device != "CPU":
            return ov_genai.Text2ImagePipeline(str(model_dir(name)), "CPU")
        raise


def generate_image(req: AssetRequest, out: Path, name: str | None = None) -> Path:
    from PIL import Image

    name = name or get_config().models.image
    info = require_model(name, "image")
    pipe = manager().get(f"image:{name}", lambda: _load_pipeline(name), name)
    w, h = _sizes(req)
    kwargs = {"width": w, "height": h, "num_inference_steps": info.steps or 4}
    if req.seed is not None:
        kwargs["rng_seed"] = int(req.seed) % (2**31)
    tensor = pipe.generate(req.full_prompt(), **kwargs)
    Image.fromarray(tensor.data[0]).save(out, quality=95)
    return out


class LocalImageMotion:
    """Görsel üret + Ken Burns hareketiyle video klibe çevir."""

    name = "local_image_motion"

    def check(self):
        cfg = get_config()
        info = require_model(cfg.models.image, "image")
        if info.remote_only:
            raise ProviderUnavailable(f"{cfg.models.image} yalnızca bulut worker'da çalışır")
        try:
            import openvino_genai  # noqa: F401
        except ImportError as e:
            raise ProviderUnavailable("openvino-genai kurulu değil: uv sync --extra gen") from e
        if not is_downloaded(cfg.models.image):
            raise ProviderUnavailable(
                f"Görsel modeli indirilmemiş ({cfg.models.image}). İndirmek için: kurgu models pull image"
            )

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        key = req.cache_key(self.name)
        img = out_dir / f"img_{key}.jpg"
        if not img.exists():
            t0 = time.time()
            generate_image(req, img)
            (out_dir / f"img_{key}.txt").write_text(
                f"{req.full_prompt()}\n{time.time() - t0:.1f}s", encoding="utf-8"
            )
        clip = out_dir / f"kb_{key}_{req.width}x{req.height}_{req.duration:.1f}.mp4"
        if not clip.exists():
            ken_burns(img, clip, req.duration, req.width, req.height)
        return AssetResult(clip, self.name, "image", None, img)
