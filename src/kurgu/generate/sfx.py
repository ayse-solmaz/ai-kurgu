"""Ses efektleri: ffmpeg ile sentezlenir (indirme/lisans gerektirmez).

whoosh: b-roll geçişlerinde yumuşak hava sesi (filtrelenmiş pembe gürültü, iniş-çıkış zarfı)
pop:    ekran yazısı belirirken kısa, tok bir tık (hızla sönen sinüs)
"""

from __future__ import annotations

from pathlib import Path

from ..core import ffmpeg as ff
from ..core.config import project_root

SFX = {
    "whoosh": (
        "anoisesrc=color=pink:amplitude=0.6:duration=0.55:seed=7,"
        "highpass=f=350,lowpass=f=4200,"
        "afade=t=in:st=0:d=0.32:curve=qsin,afade=t=out:st=0.32:d=0.23:curve=exp,"
        "aecho=0.6:0.4:40:0.25"
    ),
    "pop": (
        "sine=f=880:duration=0.09:sample_rate=48000,"
        "volume=0.9,afade=t=out:st=0.01:d=0.08:curve=exp,"
        "lowpass=f=3000"
    ),
}


def sfx_path(name: str) -> Path:
    """Efekt dosyasını (gerekirse üreterek) döndürür."""
    d = project_root() / "assets" / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.wav"
    if not p.exists():
        ff.run_ffmpeg(["-f", "lavfi", "-i", SFX[name], "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(p)])
    return p
