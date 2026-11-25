"""Uygulama yapılandırması ve presetler."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


def project_root() -> Path:
    """config.yaml dosyasının bulunduğu kök klasör."""
    env = os.environ.get("KURGU_ROOT")
    if env:
        return Path(env).resolve()
    for base in (Path.cwd(), *Path.cwd().parents, Path(__file__).resolve().parents[3]):
        if (base / "config.yaml").exists():
            return base
    return Path.cwd()


# --------------------------------------------------------------------------- config.yaml


class ModelsCfg(BaseModel):
    asr: str = "whisper-large-v3-turbo"
    llm: str = "qwen3:8b"
    vlm: str = "qwen3-vl:4b"
    image: str = "flux-schnell-ov-int4"
    depth: str = "depth-anything-v2-small"
    video: str = "wan2.1-t2v-1.3b"
    tts: str = "chatterbox-multilingual"


class DevicesCfg(BaseModel):
    asr: str = "cpu"
    asr_compute_type: str = "int8"
    openvino: str = "GPU"


class OllamaCfg(BaseModel):
    host: str = "http://127.0.0.1:11435"
    manage: bool = True
    igpu: bool = True
    timeout: float = 600
    keep_alive: str = "5m"
    vision_frames: int = 2
    # iGPU belleğine sığmayan modeller işlemcide çalışır (qwen3:30b-a3b: CPU 12 tok/sn, yarı iGPU 8.8)
    cpu_models: list[str] = ["qwen3:30b-a3b"]


class RenderCfg(BaseModel):
    encoder: str = "auto"
    quality: int = 21
    audio_bitrate: str = "192k"
    preview_height: int = 540
    fonts_dir: str = "assets/fonts"


class AssetsCfg(BaseModel):
    providers: list[str] = ["local_image_motion", "cloud_worker", "stock", "local_video"]
    cloud_worker_url: str | None = None
    cloud_image_model: str = "flux-schnell"
    cloud_video_model: str = "wan2.1-t2v-1.3b"
    pexels_api_key_env: str = "PEXELS_API_KEY"
    pixabay_api_key_env: str = "PIXABAY_API_KEY"


class MemoryCfg(BaseModel):
    max_heavy_models: int = 1


class AppConfig(BaseModel):
    commercial_mode: bool = True
    projects_dir: str = "projects"
    models_cache: str = "models_cache"
    language: str | None = "tr"
    models: ModelsCfg = ModelsCfg()
    devices: DevicesCfg = DevicesCfg()
    ollama: OllamaCfg = OllamaCfg()
    render: RenderCfg = RenderCfg()
    assets: AssetsCfg = AssetsCfg()
    memory: MemoryCfg = MemoryCfg()

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else project_root() / p


@lru_cache
def get_config() -> AppConfig:
    f = project_root() / "config.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8")) if f.exists() else {}
    return AppConfig.model_validate(data or {})


# --------------------------------------------------------------------------- presetler


class OutputCfg(BaseModel):
    width: int
    height: int
    fps: float = 30


class CleanupCfg(BaseModel):
    silence_min: float = 0.5
    padding: float = 0.1
    remove_fillers: bool = True
    remove_stutters: bool = True
    remove_retakes: bool = True
    remove_unworded_speech: bool = False


class CaptionCfg(BaseModel):
    enabled: bool = True
    style: Literal["karaoke", "classic", "pop"] = "classic"
    position: Literal["bottom", "center", "top"] = "bottom"
    max_words: int = 6
    max_chars: int = 36
    uppercase: bool = False
    font: str = "Arial"
    size: int = 56
    primary: str = "#FFFFFF"
    highlight: str = "#FFD60A"
    outline_color: str = "#000000"
    outline: int = 3
    shadow: int = 1
    margin_v: int = 70
    weight: int = 700  # font kalınlığı (libass 100-900)
    highlight_mode: Literal["color", "pill", "glow", "none"] = "color"
    theme: str = ""  # caption_themes.py: apple | bold | neon | minimal ("" = preset değerleri)
    outline_alpha: int = 0  # kenarlık saydamlığı 0 (opak) - 255
    blur: float = 0.0  # kenarlık/gölge yumuşatma (\blur)


class ReframeCfg(BaseModel):
    enabled: bool = False
    track_faces: bool = False
    fill: Literal["crop", "blur"] = "blur"


class ZoomCfg(BaseModel):
    enabled: bool = False
    every_min: float = 4.0
    every_max: float = 8.0
    scale: float = 1.1
    cut_punch: float = 0.0  # >1: kesimlerde dönüşümlü yakınlaştırma (jump-cut gizleme), ör. 1.08


class BRollCfg(BaseModel):
    enabled: bool = False
    every_min: float = 8.0
    every_max: float = 15.0
    min_len: float = 2.0
    max_len: float = 4.0
    mode: Literal["full", "pip"] = "full"


class HookCfg(BaseModel):
    enabled: bool = False
    duration: float = 3.0


class AudioCfg(BaseModel):
    denoise: bool = True
    highpass: int = 70
    loudness: float = -14
    true_peak: float = -1.5
    music_db: float = -24
    sfx: bool = True  # b-roll girişinde whoosh, ekran yazısında pop
    sfx_db: float = -17


class Preset(BaseModel):
    name: str
    output: OutputCfg
    max_duration: float | None = None
    cleanup: CleanupCfg = CleanupCfg()
    captions: CaptionCfg = CaptionCfg()
    reframe: ReframeCfg = ReframeCfg()
    zoom: ZoomCfg = ZoomCfg()
    broll: BRollCfg = BRollCfg()
    hook: HookCfg = HookCfg()
    audio: AudioCfg = AudioCfg()


def presets_dir() -> Path:
    return project_root() / "presets"


def list_presets() -> list[str]:
    return sorted(p.stem for p in presets_dir().glob("*.yaml"))


def load_preset(name: str) -> Preset:
    f = presets_dir() / f"{name}.yaml"
    if not f.exists():
        raise ValueError(f"Preset bulunamadı: {name}. Mevcut: {', '.join(list_presets())}")
    return Preset.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- model kaydı


class ModelInfo(BaseModel):
    name: str = ""
    kind: str
    source: str
    license: str
    commercial_ok: bool
    remote_only: bool = False  # yalnızca bulut GPU worker'da çalıştırılabilir
    repo: str | None = None  # indirilecek (dönüştürülmüş) model deposu
    steps: int | None = None  # varsayılan çıkarım adımı
    note: str | None = None


@lru_cache
def model_registry() -> dict[str, ModelInfo]:
    f = project_root() / "models_registry.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return {k: ModelInfo.model_validate({**v, "name": k}) for k, v in data.items()}


def agents_path() -> Path:
    return project_root() / "agents.yaml"


def load_agents() -> dict:
    """Yönetmen/alt ajan tanımları (sistem prompt'ları, modeller, stil şablonları)."""
    f = agents_path()
    return yaml.safe_load(f.read_text(encoding="utf-8")) if f.exists() else {"agents": {}}


def save_agents(data: dict) -> None:
    agents_path().write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8"
    )


class LicenseError(RuntimeError):
    pass


def require_model(name: str, kind: str | None = None) -> ModelInfo:
    """Modeli kayıttan getirir; ticari modda lisansı uygun değilse hata verir."""
    reg = model_registry()
    info = reg.get(name)
    if info is None:
        raise ValueError(f"'{name}' models_registry.yaml içinde tanımlı değil.")
    if kind and info.kind != kind:
        raise ValueError(f"'{name}' bir {kind} modeli değil ({info.kind}).")
    if get_config().commercial_mode and not info.commercial_ok:
        raise LicenseError(
            f"'{name}' modelinin lisansı ({info.license}) ticari kullanıma uygun değil. "
            "config.yaml'da commercial_mode: false yaparak yalnızca kişisel kullanımda açabilirsiniz."
        )
    return info
