"""Yerel metinden-videoya üretim (opsiyonel, Intel iGPU'da yavaş).

OpenVINO GenAI'nin Text2VideoPipeline desteği olan sürümlerde ve model önceden
`models_cache/video/<ad>` altına OpenVINO biçiminde konduysa çalışır. Diğer durumlarda
sağlayıcı kendini "kullanılamaz" bildirir ve yönlendirici bir sonrakine geçer.
"""

from __future__ import annotations

from pathlib import Path

from ..core.config import get_config, require_model
from ..core.models import manager
from .base import AssetRequest, AssetResult, ProviderUnavailable
from .motion import fit_clip


def model_dir(name: str) -> Path:
    cfg = get_config()
    return cfg.path(cfg.models_cache) / "video" / name


class LocalVideoProvider:
    name = "local_video"

    def check(self):
        cfg = get_config()
        info = require_model(cfg.models.video, "video")
        if info.remote_only:
            raise ProviderUnavailable(f"{cfg.models.video} yalnızca bulut worker'da çalışır")
        try:
            import openvino_genai as g
        except ImportError as e:
            raise ProviderUnavailable("openvino-genai kurulu değil") from e
        if not hasattr(g, "Text2VideoPipeline"):
            raise ProviderUnavailable("Bu OpenVINO GenAI sürümünde Text2VideoPipeline yok")
        if not (model_dir(cfg.models.video) / "model_index.json").exists():
            raise ProviderUnavailable(f"Video modeli yok: {model_dir(cfg.models.video)} (OpenVINO biçiminde)")

    def _load(self, name: str):
        import openvino_genai as g

        return g.Text2VideoPipeline(str(model_dir(name)), get_config().devices.openvino)

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        import numpy as np

        from ..core import ffmpeg as ff

        cfg = get_config()
        name = cfg.models.video
        info = require_model(name, "video")
        pipe = manager().get(f"video:{name}", lambda: self._load(name), name)
        key = req.cache_key(self.name)
        w, h = (832, 480) if req.width >= req.height else (480, 832)
        fps = 16
        frames = int(min(req.duration, 5.0) * fps) // 4 * 4 + 1
        result = pipe.generate(req.full_prompt(), width=w, height=h, num_frames=frames,
                               num_inference_steps=info.steps or 30)
        video = np.asarray(result.video.data if hasattr(result, "video") else result.data)[0]
        frames_dir = out_dir / f"lv_{key}"
        frames_dir.mkdir(exist_ok=True)
        from PIL import Image

        for i, frame in enumerate(video):
            Image.fromarray(frame).save(frames_dir / f"{i:04d}.png")
        raw = out_dir / f"lv_{key}.mp4"
        ff.run_ffmpeg(["-framerate", str(fps), "-i", str(frames_dir / "%04d.png"), "-c:v", "libx264",
                       "-pix_fmt", "yuv420p", "-crf", "18", str(raw)])
        clip = out_dir / f"lv_{key}_{req.width}x{req.height}_{req.duration:.1f}.mp4"
        fit_clip(raw, clip, req.duration, req.width, req.height)
        return AssetResult(clip, f"{self.name}:{name}", "video")
