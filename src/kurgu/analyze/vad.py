"""Konuşma bölgesi tespiti: Silero VAD (MIT), faster-whisper'ın içindeki ONNX sürümü.

Torch gerektirmez; onnxruntime ile CPU'da hızlı çalışır.
"""

from __future__ import annotations

from pathlib import Path

SR = 16000


def speech_regions(
    audio: Path,
    min_silence_ms: int = 150,
    speech_pad_ms: int = 30,
    threshold: float = 0.5,
) -> list[tuple[float, float]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    from .audio_io import load_wav16k

    wav = load_wav16k(audio)
    opts = VadOptions(
        threshold=threshold,
        min_silence_duration_ms=min_silence_ms,
        speech_pad_ms=speech_pad_ms,
        min_speech_duration_ms=80,
    )
    stamps = get_speech_timestamps(wav, opts)
    return [(round(s["start"] / SR, 3), round(s["end"] / SR, 3)) for s in stamps]
