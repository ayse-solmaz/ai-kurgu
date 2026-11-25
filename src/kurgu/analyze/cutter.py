"""Transkript + VAD'dan otomatik temizleme kesimleri üretir."""

from __future__ import annotations

from ..core.config import CleanupCfg
from ..core.timeline import (
    Cut,
    CutReason,
    Transcript,
    complement,
    intersect,
    merge_ranges,
)


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def build_cleanup_cuts(
    tr: Transcript,
    duration: float,
    cfg: CleanupCfg,
    vad: list[tuple[float, float]] | None = None,
) -> list[Cut]:
    """Tutulacak konuşmayı belirler, geri kalanı gerekçesiyle birlikte kesim olarak döndürür.

    1. Tutulacak kelimeler: dolgu/kekeleme/retake olmayanlar (ayarlara göre).
    2. Whisper kelime sınırları sessizliğe taşabilir; VAD varsa kelime aralıkları VAD konuşma
       bölgeleriyle (biraz genişletilmiş) kesiştirilir.
    3. Konuşmanın etrafına `padding` eklenir, `silence_min`'den kısa boşluklar kapatılır
       (doğal nefes payı korunur).
    4. Kalan her şey kesimdir; nedeni içeriğine göre etiketlenir.
    """
    def removable(w) -> bool:
        return (
            (cfg.remove_fillers and w.is_filler)
            or (cfg.remove_stutters and w.is_stutter)
            or (cfg.remove_retakes and w.is_retake)
        )

    keep_words = [(w.start, w.end) for w in tr.words if not removable(w)]
    keep = merge_ranges(keep_words)

    if vad:
        vad_wide = merge_ranges([(a - 0.12, b + 0.12) for a, b in vad])
        clipped = intersect(keep, vad_wide)
        # VAD bir kelimeyi tamamen kaçırdıysa kelimeyi yine de koru
        lost = [r for r in keep if not any(_overlap(r, c) > 0 for c in clipped)]
        keep = merge_ranges(clipped + lost)
    protected = list(keep)  # kesimlerin asla dokunmayacağı konuşma
    if vad and not cfg.remove_unworded_speech:
        keep = merge_ranges(keep + list(vad))

    pad = cfg.padding
    keep = merge_ranges([(max(0.0, a - pad), min(duration, b + pad)) for a, b in keep])
    keep = merge_ranges(keep, gap=cfg.silence_min)

    # Çıkarılan kelimeler kısa bir boşlukla tutulan kısma yapıştıysa yukarıdaki birleştirme onları
    # geri getirebilir; bu yüzden çıkarılacak kelimeleri açıkça tekrar kes.
    removed = [(w.start, w.end, w) for w in tr.words if removable(w)]
    cut_ranges = complement(keep, 0.0, duration)
    for s, e, _ in removed:
        cut_ranges.append((s, e))
    cut_ranges = merge_ranges(cut_ranges)

    # Kesimlerin tutulan konuşmayı ısırmadığından emin ol (komşu kelime çakışmaları)
    final: list[tuple[float, float]] = []
    for c in cut_ranges:
        pieces = complement(protected, c[0], c[1])
        final.extend(p for p in pieces if p[1] - p[0] >= 0.04)

    def word_reason(w) -> CutReason:
        return CutReason.retake if w.is_retake else CutReason.stutter if w.is_stutter else CutReason.filler

    cuts: list[Cut] = []
    for a, b in final:
        # aralığı: çıkarılan kelimelerin kapladığı parçalar + aradaki sessizlik olarak böl
        inside = sorted(((max(s, a), min(e, b), w) for s, e, w in removed if e > a and s < b), key=lambda x: (x[0], x[1]))
        pieces: list[tuple[float, float, CutReason]] = []
        t = a
        for s, e, w in inside:
            if s > t + 0.02:
                pieces.append((t, s, CutReason.silence))
            pieces.append((max(s, t), e, word_reason(w)))
            t = max(t, e)
        if b > t + 0.02:
            pieces.append((t, b, CutReason.silence))
        for s, e, reason in pieces:
            if e - s < 0.01:
                continue
            if reason == CutReason.silence:
                if vad and sum(_overlap((s, e), r) for r in vad) > 0.25:
                    reason = CutReason.unworded
                elif s <= 0.001 or e >= duration - 0.001:
                    reason = CutReason.trim
            cuts.append(Cut(start=round(s, 3), end=round(e, 3), reason=reason))
    return cuts


def summarize_cuts(cuts: list[Cut]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in cuts:
        out[c.reason.value] = round(out.get(c.reason.value, 0.0) + (c.end - c.start), 2)
    return out
