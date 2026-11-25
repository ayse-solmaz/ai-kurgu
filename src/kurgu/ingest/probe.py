"""Kaynak video bilgisi, analiz sesi ve önizleme proxy'si."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from ..core import ffmpeg as ff
from ..core.timeline import SourceInfo


def _fps(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = stream.get(key) or "0/0"
        try:
            f = Fraction(val)
            if f > 0:
                return float(f)
        except (ValueError, ZeroDivisionError):
            continue
    return 30.0


def _rotation(stream: dict) -> int:
    rot = 0
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if not rot:
        rot = int(float((stream.get("tags") or {}).get("rotate", 0) or 0))
    return rot % 360


def probe_source(path: Path) -> SourceInfo:
    info = ff.probe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise ValueError(f"Videoda görüntü akışı yok: {path}")
    duration = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    w, h = int(v["width"]), int(v["height"])
    rot = _rotation(v)
    if rot in (90, 270):  # telefon videoları: ffmpeg otomatik döndürür, boyutları değiştir
        w, h = h, w
    return SourceInfo(
        path=str(path),
        duration=duration,
        width=w,
        height=h,
        fps=round(_fps(v), 3),
        has_audio=a is not None,
        rotation=rot,
    )


def extract_audio(src: Path, dst: Path, progress=None, duration: float | None = None) -> Path:
    """Analiz için 16 kHz mono WAV."""
    ff.run_ffmpeg(
        ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
        duration=duration,
        progress=progress,
    )
    return dst


def make_proxy(src: Path, dst: Path, height: int = 540, progress=None, duration: float | None = None) -> Path:
    """Arayüz önizlemesi ve VLM için hafif kopya (sık anahtar kare = hızlı ileri/geri sarma)."""
    ff.run_ffmpeg(
        [
            "-i", str(src),
            "-vf", f"scale=-2:{height}:flags=bicubic,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-g", "30",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
            str(dst),
        ],
        duration=duration,
        progress=progress,
    )
    return dst


def extract_frame(src: Path, t: float, dst: Path, width: int = 768) -> Path:
    ff.run([
        ff.ffmpeg(), "-hide_banner", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src),
        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(dst),
    ])
    return dst
