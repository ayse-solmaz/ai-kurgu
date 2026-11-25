"""16 kHz mono WAV'ı numpy dizisine okur (PyAV'ye bağımlı olmadan)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 16000


def load_wav16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        if wf.getframerate() != SR or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(f"Beklenen 16 kHz mono 16-bit WAV, gelen: {wf.getframerate()} Hz, "
                             f"{wf.getnchannels()} kanal, {wf.getsampwidth() * 8} bit")
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
