"""Durağan görseli videoya çevirme: Ken Burns (yakınlaşma/uzaklaşma/kaydırma).

zoompan doğrudan çıktı boyutunda çalışınca alt-piksel yuvarlamasından titreme olur;
bu yüzden görsel önce büyük ölçekte hazırlanır, hareket orada yapılır, sonra küçültülür.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..core import ffmpeg as ff

MOVES = ["zoom_in", "zoom_out", "pan_right", "pan_left", "zoom_in_up"]


def pick_move(seed_text: str) -> str:
    h = int(hashlib.md5(seed_text.encode("utf-8")).hexdigest(), 16)
    return MOVES[h % len(MOVES)]


def _expr(move: str, frames: int, strength: float) -> tuple[str, str, str]:
    """(zoom, x, y) zoompan ifadeleri. 'on' = çıktı kare numarası."""
    p = f"(on/{max(frames - 1, 1)})"
    ease = f"(0.5-0.5*cos(PI*{p}))"  # yumuşak başlangıç/bitiş
    s = strength
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if move == "zoom_in":
        return f"1+{s:.3f}*{ease}", cx, cy
    if move == "zoom_out":
        return f"{1 + s:.3f}-{s:.3f}*{ease}", cx, cy
    if move == "zoom_in_up":
        return f"1+{s:.3f}*{ease}", cx, f"(ih-ih/zoom)*(0.5-0.3*{ease})"
    if move == "pan_right":
        return f"{1 + s:.3f}", f"(iw-iw/zoom)*{ease}", cy
    # pan_left
    return f"{1 + s:.3f}", f"(iw-iw/zoom)*(1-{ease})", cy


def ken_burns(
    image: Path,
    out: Path,
    duration: float,
    width: int,
    height: int,
    fps: float = 30,
    move: str | None = None,
    strength: float = 0.12,
) -> Path:
    move = move or pick_move(str(image))
    frames = max(int(round(duration * fps)), 2)
    z, x, y = _expr(move, frames, strength)
    # Çalışma çözünürlüğü: çıktının 2 katı (titremeyi azaltır), önce kadraja oturt
    ww, wh = width * 2, height * 2
    vf = (
        f"scale={ww}:{wh}:force_original_aspect_ratio=increase:flags=lanczos,crop={ww}:{wh},setsar=1,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={ww}x{wh}:fps={fps},"
        f"scale={width}:{height}:flags=lanczos,format=yuv420p"
    )
    ff.run_ffmpeg([
        "-loop", "1", "-i", str(image), "-vf", vf, "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(out),
    ])
    return out


def fit_clip(video: Path, out: Path, duration: float, width: int, height: int, fps: float = 30) -> Path:
    """Stok/üretilmiş videoyu hedef boyuta kırpar, süreyi ayarlar (kısaysa yavaşlatır)."""
    info = ff.probe(video)
    src_dur = float(info.get("format", {}).get("duration") or duration)
    speed = ""
    if src_dur < duration:
        factor = duration / max(src_dur, 0.1)
        speed = f"setpts={min(factor, 3.0):.4f}*PTS,"
    start = max(0.0, (src_dur - duration) / 3) if src_dur > duration + 1 else 0.0
    vf = (
        f"{speed}scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p"
    )
    ff.run_ffmpeg([
        "-ss", f"{start:.2f}", "-i", str(video), "-t", f"{duration:.3f}", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-an", str(out),
    ])
    return out
