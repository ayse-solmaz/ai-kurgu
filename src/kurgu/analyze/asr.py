"""Konuşma tanıma: faster-whisper ile kelime zaman damgalı transkript."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..core.config import get_config, model_registry, require_model
from ..core.models import manager
from ..core.timeline import Sentence, Transcript, Word

# Whisper dolgu sözcüklerini yazmamaya meyillidir. Prompt'ta örnek göstermek,
# "ııı", "eee" gibi sesleri de transkripte geçirmesini sağlar (bilinen bir hile).
FILLER_PROMPTS = {
    "tr": "Eee, şey, ııı, yani... Hmm, ee, tamam. Ben de, ıı, şöyle düşündüm.",
    "en": "Umm, let me think like, hmm... Okay, uh, here's what I, I think.",
}


def _load_model():
    cfg = get_config()
    info = require_model(cfg.models.asr, "asr")
    from faster_whisper import WhisperModel

    return WhisperModel(
        info.source,
        device=cfg.devices.asr,
        compute_type=cfg.devices.asr_compute_type,
        download_root=str(cfg.path(cfg.models_cache) / "whisper"),
    )


def transcribe(
    audio: Path,
    language: str | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> Transcript:
    cfg = get_config()
    language = language if language is not None else cfg.language
    model = manager().get(f"asr:{cfg.models.asr}", _load_model, cfg.models.asr)

    from .audio_io import load_wav16k

    segments, info = model.transcribe(
        load_wav16k(audio),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 200},
        initial_prompt=FILLER_PROMPTS.get(language or "", None),
        condition_on_previous_text=False,
        beam_size=5,
        temperature=[0.0, 0.2, 0.4],
    )

    words: list[Word] = []
    total = float(info.duration or 0) or 1.0
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            words.append(Word(
                id=len(words),
                text=text,
                start=round(float(w.start), 3),
                end=round(float(max(w.end, w.start + 0.02)), 3),
                prob=round(float(w.probability), 3),
            ))
        if progress:
            progress(min(seg.end / total, 1.0), seg.text.strip()[:60])

    sentences = build_sentences(words)
    return Transcript(language=info.language, duration=float(info.duration or 0), words=words, sentences=sentences)


_SENT_END = (".", "?", "!", "…")


def build_sentences(words: list[Word], max_pause: float = 1.2, max_words: int = 30) -> list[Sentence]:
    """Kelimeleri noktalama, uzun duraklama ve uzunluk sınırına göre cümlelere böler.

    Whisper segmentleri (özellikle VAD açıkken) çok uzun olabildiği için segment yerine bu kullanılır.
    """
    sentences: list[Sentence] = []
    cur: list[Word] = []

    def flush():
        if cur:
            sentences.append(Sentence(id=len(sentences), word_ids=[w.id for w in cur],
                                      text=" ".join(w.text for w in cur), start=cur[0].start, end=cur[-1].end))

    for w in words:
        if cur and (w.start - cur[-1].end > max_pause or len(cur) >= max_words):
            flush()
            cur = []
        cur.append(w)
        if w.text.endswith(_SENT_END):
            flush()
            cur = []
    flush()
    return sentences


def asr_model_label() -> str:
    cfg = get_config()
    info = model_registry().get(cfg.models.asr)
    return f"{cfg.models.asr} ({info.license if info else '?'})"
