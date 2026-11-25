"""Görsel anlama: videodan örnek kareler alıp Qwen3-VL ile sahneyi tarif eder.

Planlayıcıya "ne görünüyor" bağlamı verir (ör. konuşan kafa mı, ekran kaydı mı, dış çekim mi)
ve öz-denetimde render edilmiş kareleri kontrol etmek için kullanılır.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..core.project_store import Project
from ..ingest.probe import extract_frame


class SceneDescription(BaseModel):
    setting: str = Field(description="Mekan ve ortamın kısa tarifi (Türkçe)")
    video_type: Literal["talking_head", "screen_recording", "vlog", "tutorial", "presentation", "other"]
    people: int = Field(description="Görünen kişi sayısı")
    speaker_position: Literal["left", "center", "right", "none"] = "center"
    on_screen_text: bool = Field(description="Karede okunması gereken önemli yazı/ekran içeriği var mı")
    visual_style: str = Field(description="Işık, renk, çekim tarzı (Türkçe, kısa)")


def sample_times(duration: float, n: int = 4) -> list[float]:
    n = max(1, min(n, int(duration // 3) or 1))
    return [duration * (i + 0.5) / n for i in range(n)]


def describe_video(project: Project, duration: float, n_frames: int = 4) -> SceneDescription:
    from ..agent.llm import describe_images

    src = project.proxy if project.proxy.exists() else project.source
    frames_dir = project.dir / "analysis" / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for i, t in enumerate(sample_times(duration, n_frames)):
        frames.append(extract_frame(src, t, frames_dir / f"sample_{i}.jpg", width=640))
    prompt = (
        "Bu kareler aynı videodan alınmıştır. Videoyu bir kurgu editörü için tarif et: "
        "mekan, video türü, kişi sayısı, konuşmacının kadrajdaki yeri, ekranda okunması gereken "
        "yazı olup olmadığı ve görsel tarz. Sadece JSON döndür."
    )
    desc = describe_images(frames, prompt, schema=SceneDescription)
    project.save_analysis("vision", desc)
    return desc
