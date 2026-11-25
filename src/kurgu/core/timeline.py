"""Kurgunun tek gerçek kaynağı: Timeline.

Tüm zamanlar KAYNAK videonun saniyesi cinsindendir. Öğeler (altyazı, yazı, b-roll, zoom)
mümkünse transkript kelime ID'lerine bağlanır (Anchor). Kesimler değiştiğinde çıktı zamanı
TimeMap ile yeniden hesaplanır; böylece hiçbir öğe kaymaz.
"""

from __future__ import annotations

import bisect
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .config import (
    AudioCfg,
    CaptionCfg,
    OutputCfg,
    Preset,
    ReframeCfg,
)

EPS = 1e-6

# --------------------------------------------------------------------------- transkript


class Word(BaseModel):
    id: int
    text: str
    start: float
    end: float
    prob: float = 1.0
    is_filler: bool = False
    is_stutter: bool = False
    is_retake: bool = False

    @property
    def removable(self) -> bool:
        return self.is_filler or self.is_stutter or self.is_retake


class Sentence(BaseModel):
    id: int
    word_ids: list[int]
    text: str
    start: float
    end: float


class Transcript(BaseModel):
    language: str | None = None
    duration: float = 0.0
    words: list[Word] = []
    sentences: list[Sentence] = []

    def word_map(self) -> dict[int, Word]:
        return {w.id: w for w in self.words}

    def text_between(self, start: float, end: float) -> str:
        return " ".join(w.text for w in self.words if w.end > start and w.start < end)


# --------------------------------------------------------------------------- yardımcı tipler


class SourceInfo(BaseModel):
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool = True
    rotation: int = 0


class CutReason(str, Enum):
    silence = "silence"
    filler = "filler"
    stutter = "stutter"
    retake = "retake"
    unworded = "unworded"
    manual = "manual"
    trim = "trim"


class Cut(BaseModel):
    start: float
    end: float
    reason: CutReason = CutReason.manual
    note: str | None = None


class Anchor(BaseModel):
    """Bir öğenin zamanı. Kelime ID'leri verilmişse onlar önceliklidir."""

    word_start: int | None = None
    word_end: int | None = None
    start: float | None = None
    end: float | None = None

    def resolve(self, words: dict[int, Word]) -> tuple[float, float]:
        """Kaynak zaman aralığını döndürür."""
        s = e = None
        if self.word_start is not None and self.word_start in words:
            s = words[self.word_start].start
        if self.word_end is not None and self.word_end in words:
            e = words[self.word_end].end
        elif self.word_end is None and self.end is None and self.word_start in words:
            e = words[self.word_start].end  # yalnızca başlangıç kelimesine bağlı öğe
        if s is None:
            s = self.start
        if e is None:
            e = self.end
        if s is None or e is None:
            raise ValueError(f"Anchor çözümlenemedi: {self}")
        return (min(s, e), max(s, e))


# --------------------------------------------------------------------------- kurgu öğeleri


class TextOverlay(BaseModel):
    id: str
    anchor: Anchor
    text: str
    style: Literal["hook", "title", "lower_third", "pop", "chapter"] = "pop"
    position: Literal["top", "center", "bottom"] = "top"


class Zoom(BaseModel):
    id: str
    anchor: Anchor
    scale: float = 1.12
    ease: float = 0.12  # sn, giriş/çıkış yumuşatma


class BRoll(BaseModel):
    id: str
    anchor: Anchor
    prompt: str = Field(description="Görsel/video üretim prompt'u (İngilizce)")
    keywords: list[str] = []
    kind: Literal["image", "video", "any"] = "any"
    motion: Literal["kenburns", "parallax", "none"] = "kenburns"
    mode: Literal["full", "pip"] = "full"
    asset: str | None = None  # proje klasörüne göre yol
    provider: str | None = None
    attribution: str | None = None


class MotionItem(BaseModel):
    """Hareketli grafik (motion/templates/<template>): kelimeye bağlı başlar, şablon süresi kadar sürer."""

    id: str
    anchor: Anchor
    template: str
    params: dict = {}
    duration: float | None = None  # boşsa şablonun varsayılanı
    layout: str = "card"  # card: konuşmacının üstünde kart, full: tam ekran anlatım
    asset: str | None = None  # alfa kanallı .mov (render için)
    preview: str | None = None  # alfa kanallı .webm (arayüz önizlemesi)
    icons: list[str] = []  # kullanılan ikonlar (lisans takibi)
    theme: str | None = None  # asset'in render edildiği tema (timeline teması değişince yeniden render)


class Voiceover(BaseModel):
    id: str
    anchor: Anchor
    text: str
    asset: str | None = None
    volume_db: float = 0.0
    duck_db: float = -14.0


class Music(BaseModel):
    asset: str | None = None
    mood: str | None = None
    volume_db: float = -24.0


class ReframeKey(BaseModel):
    t: float  # kaynak zaman
    cx: float  # yatay merkez, 0..1
    cy: float = 0.5


class Reframe(ReframeCfg):
    keys: list[ReframeKey] = []


class Chapter(BaseModel):
    anchor: Anchor
    title: str


class Meta(BaseModel):
    title: str | None = None
    summary: str | None = None
    description: str | None = None
    hook: str | None = None
    hashtags: list[str] = []
    chapters: list[Chapter] = []


# --------------------------------------------------------------------------- zaman eşleme


def merge_ranges(ranges: list[tuple[float, float]], gap: float = 0.0) -> list[tuple[float, float]]:
    """Örtüşen (veya aralarında `gap`'ten az boşluk olan) aralıkları birleştirir."""
    out: list[list[float]] = []
    for a, b in sorted((min(a, b), max(a, b)) for a, b in ranges):
        if b - a <= EPS:
            continue
        if out and a <= out[-1][1] + gap + EPS:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def complement(ranges: list[tuple[float, float]], start: float, end: float) -> list[tuple[float, float]]:
    out = []
    t = start
    for a, b in merge_ranges(ranges):
        a, b = max(a, start), min(b, end)
        if b <= a:
            continue
        if a > t + EPS:
            out.append((t, a))
        t = max(t, b)
    if end > t + EPS:
        out.append((t, end))
    return out


def intersect(a_list: list[tuple[float, float]], b_list: list[tuple[float, float]]) -> list[tuple[float, float]]:
    a_list, b_list = merge_ranges(a_list), merge_ranges(b_list)
    i = j = 0
    out = []
    while i < len(a_list) and j < len(b_list):
        lo = max(a_list[i][0], b_list[j][0])
        hi = min(a_list[i][1], b_list[j][1])
        if hi > lo + EPS:
            out.append((lo, hi))
        if a_list[i][1] < b_list[j][1]:
            i += 1
        else:
            j += 1
    return out


class TimeMap:
    """Kaynak zaman <-> çıktı zamanı dönüşümü."""

    def __init__(self, keep: list[tuple[float, float]]):
        self.spans: list[tuple[float, float, float]] = []  # (src_a, src_b, out_a)
        t = 0.0
        for a, b in keep:
            self.spans.append((a, b, t))
            t += b - a
        self.duration = t
        self._starts = [s[0] for s in self.spans]

    def to_out(self, t: float, snap: Literal["strict", "next", "prev"] = "strict") -> float | None:
        """Kaynak zamanı çıktı zamanına çevirir.

        strict: kesilmiş bölgedeyse None. next/prev: en yakın sonraki/önceki tutulan ana yapışır.
        """
        if not self.spans:
            return None
        i = bisect.bisect_right(self._starts, t + EPS) - 1
        if i >= 0:
            a, b, o = self.spans[i]
            if a - EPS <= t <= b + EPS:
                return o + min(max(t - a, 0.0), b - a)
        if snap == "strict":
            return None
        if snap == "next":
            j = i + 1
            return self.spans[j][2] if j < len(self.spans) else None
        # prev
        if i < 0:
            return None
        a, b, o = self.spans[i]
        return o + (b - a)

    def range_to_out(self, a: float, b: float) -> tuple[float, float] | None:
        oa = self.to_out(a, "next")
        ob = self.to_out(b, "prev")
        if oa is None or ob is None or ob - oa <= EPS:
            return None
        return (oa, ob)

    def to_src(self, t_out: float) -> float:
        for a, b, o in self.spans:
            if t_out <= o + (b - a) + EPS:
                return a + max(0.0, t_out - o)
        return self.spans[-1][1] if self.spans else 0.0


# --------------------------------------------------------------------------- timeline


class Timeline(BaseModel):
    version: int = 0
    preset: str
    source: SourceInfo
    output: OutputCfg
    cuts: list[Cut] = []
    captions: CaptionCfg = CaptionCfg()
    texts: list[TextOverlay] = []
    zooms: list[Zoom] = []
    brolls: list[BRoll] = []
    motions: list[MotionItem] = []
    voiceovers: list[Voiceover] = []
    music: Music | None = None
    reframe: Reframe = Reframe()
    audio: AudioCfg = AudioCfg()
    meta: Meta = Meta()
    cut_punch: float = 0.0  # kesimlerde dönüşümlü zoom ölçeği (0 = kapalı)
    theme: str = "apple"  # hareketli grafik teması (motion/base.css [data-theme])
    note: str | None = Field(default=None, description="Bu sürümü oluşturan değişikliğin açıklaması")

    @classmethod
    def from_preset(cls, preset: Preset, source: SourceInfo) -> "Timeline":
        from ..render.caption_themes import apply_caption_theme

        caps = preset.captions.model_copy()
        if caps.theme:
            caps = apply_caption_theme(caps, caps.theme, preset.output.width, preset.output.height)
        return cls(
            preset=preset.name,
            source=source,
            output=preset.output.model_copy(),
            captions=caps,
            theme=caps.theme or "apple",
            reframe=Reframe(**preset.reframe.model_dump()),
            audio=preset.audio.model_copy(),
            cut_punch=preset.zoom.cut_punch,
        )

    def keep_ranges(self, min_keep: float = 0.08) -> list[tuple[float, float]]:
        """Kesimler dışında kalan, çıktıya girecek kaynak aralıkları."""
        cuts = [(c.start, c.end) for c in self.cuts]
        keep = complement(cuts, 0.0, self.source.duration)
        return [(a, b) for a, b in keep if b - a >= min_keep]

    def time_map(self) -> TimeMap:
        return TimeMap(self.keep_ranges())

    def output_duration(self) -> float:
        return self.time_map().duration

    # --- düzenleme yardımcıları (ajan araçları bunları kullanır)

    def add_cut(self, start: float, end: float, reason: CutReason = CutReason.manual, note: str | None = None):
        start, end = max(0.0, start), min(self.source.duration, end)
        if end - start > EPS:
            self.cuts.append(Cut(start=start, end=end, reason=reason, note=note))
            self.cuts.sort(key=lambda c: c.start)

    def restore(self, start: float, end: float):
        """[start, end] aralığını yeniden görünür yapar (kesimleri böler/kısaltır)."""
        new: list[Cut] = []
        for c in self.cuts:
            if c.end <= start or c.start >= end:
                new.append(c)
                continue
            if c.start < start:
                new.append(c.model_copy(update={"end": start}))
            if c.end > end:
                new.append(c.model_copy(update={"start": end}))
        self.cuts = sorted(new, key=lambda c: c.start)

    def next_id(self, prefix: str) -> str:
        existing = {o.id for o in (*self.texts, *self.zooms, *self.brolls, *self.voiceovers, *self.motions)}
        i = 1
        while f"{prefix}{i}" in existing:
            i += 1
        return f"{prefix}{i}"
