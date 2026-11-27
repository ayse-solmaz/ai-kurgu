"""Akıllı planlayıcı: LLM videoyu anlar ve kurgu öğelerini önerir.

Model cümle numarası + cümleden birebir alınmış kısa bir ifade (phrase) döndürür; ifade
kelime ID'lerine eşlenir. Böylece küçük modeller bile zaman damgası uydurmadan doğru yeri
işaretleyebilir. Yoğunluk, aralık ve süre kuralları kodla uygulanır (modelin takdirine kalmaz).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Literal

from pydantic import BaseModel, Field, field_validator

from ..analyze.fillers import normalize
from ..core.config import Preset, get_config, load_preset
from ..core.project_store import Project
from ..core.timeline import (
    Anchor,
    BRoll,
    Chapter,
    CutReason,
    Meta,
    Music,
    TextOverlay,
    Timeline,
    TimeMap,
    Transcript,
    Zoom,
)
from .llm import LLMError, chat_json, has_model

Progress = Callable[[str, float, str], None]

# --------------------------------------------------------------------------- LLM şemaları


def _limit_words(v: str, n: int, label: str) -> str:
    """Kelime sınırı: kelime sınırında kısaltır.

    Şemadaki maxLength modeli kelime ortasında keser; doğrulama hatası vermek ise tüm planın
    (dakikalar süren) yeniden üretilmesine yol açar. Bu yüzden sessizce kısaltılır.
    """
    words = v.strip().split()
    if len(words) <= n:
        return v.strip()
    return " ".join(words[:n]).rstrip(",;:-")


class PlanBRoll(BaseModel):
    sentence_id: int
    phrase: str = Field(description="Cümleden BİREBİR kopyalanmış 2-6 kelime: görüntünün başlayacağı an")
    visual: str = Field(description="İngilizce, somut, fotoğraf gibi tek cümlelik görsel sahne tarifi")
    keywords: list[str] = Field(description="Stok video araması için 1-3 İngilizce anahtar kelime")


class PlanText(BaseModel):
    sentence_id: int
    phrase: str = Field(description="Cümleden BİREBİR kopyalanmış 1-5 kelime")
    text: str = Field(description="Ekranda çıkacak kısa vurgu yazısı, en fazla 4 kelime, isteğe bağlı 1 emoji")

    @field_validator("text")
    @classmethod
    def _short(cls, v: str) -> str:
        return v.strip()  # uzunsa kesilmez (anlamsızlaşır); kurallar uzun yazıları eler


class PlanZoom(BaseModel):
    sentence_id: int
    phrase: str = Field(description="Vurgulanan, cümleden BİREBİR kopyalanmış 1-5 kelime")


class PlanChapter(BaseModel):
    sentence_id: int
    title: str = Field(description="Bölüm başlığı, en fazla 5 kelime")


class ChunkPlan(BaseModel):
    summary: str = Field(description="Bu bölümün 1-2 cümlelik Türkçe özeti")
    chapters: list[PlanChapter] = Field(default_factory=list, max_length=10)
    brolls: list[PlanBRoll] = Field(default_factory=list, max_length=8)  # maxItems: kontrolsüz uzun çıktıyı engeller
    texts: list[PlanText] = Field(default_factory=list, max_length=8)  # maxItems: kontrolsüz uzun çıktıyı engeller
    zooms: list[PlanZoom] = Field(default_factory=list, max_length=8)  # maxItems: kontrolsüz uzun çıktıyı engeller


class GlobalMeta(BaseModel):
    title: str = Field(description="Dikkat çekici video başlığı, en fazla 70 karakter")
    hook: str = Field(description="İlk saniyelerde ekranda görünecek merak uyandırıcı kısa cümle, en fazla 6 kelime")

    description: str = Field(description="2-4 cümlelik video açıklaması")
    hashtags: list[str] = Field(description="3-6 hashtag, # işaretiyle")
    music_mood: Literal["calm", "upbeat", "inspiring", "dramatic", "corporate", "none"]


class FullPlan(ChunkPlan, GlobalMeta):
    """Tek parçalık (kısa) videolarda plan ve meta tek LLM çağrısında istenir."""


class Highlight(BaseModel):
    start_sentence: int
    end_sentence: int
    reason: str


class StoryPick(BaseModel):
    """Kısa video için seçilen cümleler: ardışık olmak zorunda değil, hikâyenin tamamı (giriş-sorun-çözüm-sonuç)."""

    sentence_ids: list[int] = Field(description="Kısa videoya girecek cümle numaraları, kronolojik sırada")
    reason: str = Field(description="Bu cümlelerin nasıl bir hikâye kurduğu, tek cümle")


# --------------------------------------------------------------------------- transkript görünümü


@dataclass
class KeptSentence:
    id: int
    word_ids: list[int]  # yalnızca kesilmeyen kelimeler
    text: str
    out_start: float
    out_end: float


def kept_sentences(tr: Transcript, tm: TimeMap) -> list[KeptSentence]:
    out = []
    for s in tr.sentences:
        ids = [i for i in s.word_ids if tm.to_out((tr.words[i].start + tr.words[i].end) / 2, "strict") is not None]
        if not ids:
            continue
        a = tm.to_out(tr.words[ids[0]].start, "next") or 0.0
        b = tm.to_out(tr.words[ids[-1]].end, "prev") or a
        out.append(KeptSentence(s.id, ids, " ".join(tr.words[i].text for i in ids), a, b))
    return out


def format_sentences(sents: list[KeptSentence]) -> str:
    return "\n".join(f"[{s.id}] ({s.out_start:.1f}s) {s.text}" for s in sents)


def chunk_sentences(sents: list[KeptSentence], max_words: int = 900) -> list[list[KeptSentence]]:
    chunks, cur, n = [], [], 0
    for s in sents:
        w = len(s.word_ids)
        if cur and n + w > max_words:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(s)
        n += w
    if cur:
        chunks.append(cur)
    return chunks


def _phrase_score(tr: Transcript, sent: KeptSentence, phrase: str) -> tuple[float, int, int]:
    """(benzerlik, başlangıç kelime ID, bitiş kelime ID): ifadenin cümledeki en iyi eşleşmesi."""
    target = [normalize(t) for t in phrase.split() if normalize(t)]
    words = [normalize(tr.words[i].text) for i in sent.word_ids]
    if not target or not words:
        return 0.0, sent.word_ids[0], sent.word_ids[0]
    n = min(len(target), len(words))
    best, best_i = -1.0, 0
    for i in range(0, len(words) - n + 1):
        score = SequenceMatcher(None, words[i:i + n], target[:n]).ratio()
        if score > best:
            best, best_i = score, i
    return best, sent.word_ids[best_i], sent.word_ids[best_i + n - 1]


def locate_phrase_global(tr: Transcript, sents: list[KeptSentence], sent: KeptSentence, phrase: str) -> tuple[int, int]:
    """İfade verilen cümlede yoksa (model yanlış cümle numarası verdiyse) tüm transkriptte arar.
    Görsel, ifadenin GERÇEKTEN söylendiği ana yerleşir."""
    score, a, b = _phrase_score(tr, sent, phrase)
    if score >= 0.6 or len(phrase.split()) < 2:
        return locate_phrase(tr, sent, phrase)
    best = max(((_phrase_score(tr, x, phrase), x) for x in sents), key=lambda t: t[0][0], default=None)
    if best and best[0][0] >= 0.6:
        return best[0][1], best[0][2]
    return locate_phrase(tr, sent, phrase)


def locate_phrase(tr: Transcript, sent: KeptSentence, phrase: str) -> tuple[int, int]:
    """İfadeyi cümle içinde bulur, (ilk kelime ID, son kelime ID) döndürür."""
    target = [normalize(t) for t in phrase.split() if normalize(t)]
    words = [normalize(tr.words[i].text) for i in sent.word_ids]
    if not target or not words:
        return sent.word_ids[0], sent.word_ids[min(len(sent.word_ids), 4) - 1]
    n = min(len(target), len(words))
    best, best_i = -1.0, 0
    for i in range(0, len(words) - n + 1):
        score = SequenceMatcher(None, words[i:i + n], target[:n]).ratio()
        if score > best:
            best, best_i = score, i
    if best < 0.34:
        best_i, n = 0, min(4, len(words))
    return sent.word_ids[best_i], sent.word_ids[best_i + n - 1]


# --------------------------------------------------------------------------- istemler

SYSTEM = """Sen deneyimli bir video editörüsün. Sana videonun transkripti cümle numaralarıyla \
([numara] (çıktıdaki saniye) metin) veriliyor. Görevin kurgu planı çıkarmak.

Kurallar:
- "phrase" alanına MUTLAKA ilgili cümleden birebir kopyalanmış kelimeler yaz.
- B-roll, konuşmacının O ANDA SÖYLEDİĞİ KONUYU görselleştirir. Örnek: "uzay ve galaksiler" \
denildiğinde -> "a vast spiral galaxy glowing in deep space". Mevcut videonun görüntüsünü veya ekranı tarif ETME.
- B-roll sadece görselleştirilebilir anlara konur: somut nesneler, yerler, eylemler, kavramlar. \
"abone olun", "merhaba" gibi kişisel/samimi anlara b-roll koyma.
- "visual" İngilizce olmalı; somut, sinematik ve fotoğraf gibi bir sahne tarif et. Yazı, logo, \
marka veya gerçek kişi ismi kullanma. Örnek: "aerial view of a glowing futuristic city at night, cinematic".
- Ekran yazıları ("texts") en önemli fikirleri, sayıları veya vurucu cümleleri kısa biçimde vurgular.
- Ekran yazıları, hook, başlık, özet ve açıklama VİDEONUN DİLİNDE (Türkçe konuşuluyorsa Türkçe) olmalı. \
Sadece "visual" ve "keywords" İngilizce olur.
- Zoom ("zooms") vurgu, şaka veya kritik cümlelere konur.
- Bölüm başlıkları ("chapters") konu değiştiğinde konur (uzun videolar için).
Sadece istenen JSON'u döndür."""


def _chunk_prompt(chunk: list[KeptSentence], preset: Preset, context: str, n_broll: int, n_text: int,
                  n_zoom: int, want_chapters: bool) -> str:
    dur = chunk[-1].out_end - chunk[0].out_start
    parts = [
        f"Video türü/preset: {preset.name}. Bu bölüm yaklaşık {dur:.0f} saniye.",
        f"Videonun mevcut görüntüsü (yalnızca stil uyumu için; b-roll'un konusu bu DEĞİL): {context}" if context else "",
        f"Yaklaşık {n_broll} b-roll, {n_text} ekran yazısı ve {n_zoom} zoom öner.",
        "Bölüm başlıkları da öner." if want_chapters else "Bölüm başlığı önerme (chapters boş liste).",
        "",
        "Transkript:",
        format_sentences(chunk),
    ]
    return "\n".join(p for p in parts if p is not None)


# --------------------------------------------------------------------------- kurallar


def _anchor_span(tr: Transcript, tm: TimeMap, ws: int, we: int) -> tuple[float, float] | None:
    a, b = tr.words[ws].start, tr.words[we].end
    return tm.range_to_out(a, b)


def _extend_to(tr: Transcript, tm: TimeMap, ws: int, we: int, min_len: float, max_len: float) -> int:
    """Kelime aralığını çıktıda min_len..max_len saniye olacak şekilde sağa uzatır/kısaltır."""
    kept = [w.id for w in tr.words if tm.to_out((w.start + w.end) / 2, "strict") is not None]
    if ws not in kept:
        return we
    start = tm.to_out(tr.words[ws].start, "next") or 0.0
    idx = kept.index(we) if we in kept else kept.index(ws)
    # uzat
    while idx + 1 < len(kept):
        end = tm.to_out(tr.words[kept[idx]].end, "prev") or start
        if end - start >= min_len:
            break
        idx += 1
    # kısalt
    while idx > kept.index(ws):
        end = tm.to_out(tr.words[kept[idx]].end, "prev") or start
        if end - start <= max_len:
            break
        idx -= 1
    return kept[idx]


def _overlaps(span, spans, margin=0.0) -> bool:
    return any(span[0] < b + margin and a - margin < span[1] for a, b in spans)


def assemble(
    tl: Timeline,
    tr: Transcript,
    preset: Preset,
    sents: list[KeptSentence],
    plans: list[ChunkPlan],
    meta: GlobalMeta | None,
    summary: str,
    text_gap: float = 8.0,
) -> Timeline:
    tm = tl.time_map()
    by_id = {s.id: s for s in sents}
    tl = tl.model_copy(deep=True)
    tl.brolls, tl.texts, tl.zooms = [], [], []
    total = tm.duration

    # --- b-roll: aralık ve süre kuralları
    if preset.broll.enabled:
        cands = []
        for p in plans:
            for b in p.brolls:
                s = by_id.get(b.sentence_id)
                if not s:
                    continue
                ws, we = locate_phrase_global(tr, sents, s, b.phrase)
                we = _extend_to(tr, tm, ws, we, preset.broll.min_len, preset.broll.max_len)
                span = _anchor_span(tr, tm, ws, we)
                if span:
                    cands.append((span, ws, we, b))
        cands.sort(key=lambda c: c[0][0])
        last_end = -1e9
        start_guard = preset.hook.duration if preset.hook.enabled else 1.5
        for span, ws, we, b in cands:
            if span[0] < start_guard or span[1] > total - 0.5:
                continue
            if span[0] - last_end < preset.broll.every_min:
                continue
            tl.brolls.append(BRoll(
                id=tl.next_id("b"), anchor=Anchor(word_start=ws, word_end=we),
                prompt=b.visual, keywords=b.keywords[:3], mode=preset.broll.mode,
            ))
            last_end = span[1]

    broll_spans = [s for s in (_anchor_span(tr, tm, x.anchor.word_start, x.anchor.word_end) for x in tl.brolls) if s]

    # --- ekran yazıları
    vertical = preset.output.height > preset.output.width
    text_style = "pop" if vertical else "lower_third"
    last = -1e9
    for p in plans:
        for t in p.texts:
            s = by_id.get(t.sentence_id)
            if not s or len(t.text.split()) > 5:  # uzun yazı ekranı boğar; kesmek yerine ele
                continue
            ws, we = locate_phrase(tr, s, t.phrase)
            we = _extend_to(tr, tm, ws, we, 1.2, 2.5)
            span = _anchor_span(tr, tm, ws, we)
            if not span or span[0] - last < text_gap or span[0] < (preset.hook.duration if preset.hook.enabled else 0):
                continue
            tl.texts.append(TextOverlay(id=tl.next_id("t"), anchor=Anchor(word_start=ws, word_end=we),
                                        text=t.text[:40], style=text_style, position="top"))
            last = span[0]

    # --- zoom: LLM önerileri + boşlukları doldur (b-roll ile çakışmasın)
    if preset.zoom.enabled:
        zoom_spans: list[tuple[float, float]] = []

        def add_zoom(ws: int, we: int):
            we2 = _extend_to(tr, tm, ws, we, 0.7, 2.2)
            span = _anchor_span(tr, tm, ws, we2)
            if not span or _overlaps(span, broll_spans, 0.3) or _overlaps(span, zoom_spans, preset.zoom.every_min / 2):
                return
            tl.zooms.append(Zoom(id=tl.next_id("z"), anchor=Anchor(word_start=ws, word_end=we2), scale=preset.zoom.scale))
            zoom_spans.append(span)

        for p in plans:
            for z in p.zooms:
                s = by_id.get(z.sentence_id)
                if s:
                    add_zoom(*locate_phrase(tr, s, z.phrase))
        # kural tabanlı doldurma: every_max'tan uzun boşluklarda cümle başına zoom
        for s in sents:
            covered = sorted(zoom_spans + broll_spans)
            prev_end = max([b for a, b in covered if b <= s.out_start], default=0.0)
            if s.out_start - prev_end >= preset.zoom.every_max:
                add_zoom(s.word_ids[0], s.word_ids[min(2, len(s.word_ids) - 1)])

    # --- hook yazısı
    if preset.hook.enabled and meta and meta.hook and sents:
        first = sents[0].word_ids[0]
        last_w = first
        for s in sents:
            for i in s.word_ids:
                if (tm.to_out(tr.words[i].end, "prev") or 0) <= preset.hook.duration:
                    last_w = i
        tl.texts.insert(0, TextOverlay(id=tl.next_id("t"), anchor=Anchor(word_start=first, word_end=last_w),
                                       text=meta.hook, style="hook", position="top"))

    # --- meta
    chapters = []
    if not vertical:  # dikey videoda bölüm başlığı kalabalık eder
        seen = set()
        for p in plans:
            for c in p.chapters:
                s = by_id.get(c.sentence_id)
                if s and c.sentence_id not in seen:
                    seen.add(c.sentence_id)
                    chapters.append(Chapter(anchor=Anchor(word_start=s.word_ids[0]), title=c.title))
    tl.meta = Meta(
        title=meta.title if meta else None,
        summary=summary,
        description=meta.description if meta else None,
        hook=meta.hook if meta else None,
        hashtags=meta.hashtags if meta else [],
        chapters=chapters,
    )
    if meta and meta.music_mood != "none":
        tl.music = Music(mood=meta.music_mood, volume_db=preset.audio.music_db)
    return tl


# --------------------------------------------------------------------------- ana akış


STORY_SYSTEM = """Sen kısa video (Shorts/Reels) editörüsün. Uzun bir konuşmadan, tek başına izlendiğinde \
anlamlı ve merak uyandıran bir hikâye çıkaracaksın. Cümleler ARDIŞIK OLMAK ZORUNDA DEĞİL: aradaki ayrıntıları \
atlayabilirsin ama seçtiğin cümleler kronolojik sırada okununca akıcı bir anlatı olmalı.

Yapı:
1. Açılış (hook): merak uyandıran bir soru veya iddia.
2. Sorun / bağlam: neden önemli.
3. Çözüm / gelişme: ne yapıldı, nasıl.
4. Sonuç / vurucu kapanış: ortaya çıkan sonuç, sayı veya etki. Videoyu ASLA bir hazırlık cümlesinde \
("... bir model geliştirdi.", "şimdi anlatacağım") bitirme; sonucu veya etkiyi gösteren cümleyle bitir.

Kurallar:
- Toplam süre verilen sınırı aşmamalı (her cümlenin süresi parantez içinde).
- "Onları", "bu yüzden", "ama" gibi önceki cümleye bağlı başlayan bir cümleyi seçiyorsan bağlı olduğu cümleyi de seç.
- Sadece JSON döndür."""


def _story_keep(tr: Transcript, chosen: list[KeptSentence]) -> list[tuple[float, float]]:
    """Seçilen cümlelerin tutulacak aralıkları. Nefes payı komşu (seçilmemiş) cümlenin kelimesine taşmaz;
    yoksa önceki cümlenin son hecesi ("...değil.") videoya sızar."""
    from ..core.timeline import merge_ranges

    out = []
    for x in chosen:
        first, last = x.word_ids[0], x.word_ids[-1]
        a = tr.words[first].start - 0.15
        b = tr.words[last].end + 0.25
        if first > 0:
            a = max(a, tr.words[first - 1].end + 0.01)
        if last + 1 < len(tr.words):
            b = min(b, tr.words[last + 1].start - 0.01)
        out.append((a, max(b, tr.words[last].end)))
    return merge_ranges(out)


# önceki cümleye bağlı başlayan cümleler: tek başına seçilirlerse anlam kopar
_DEPENDENT_START = re.compile(
    r"^(onlar\w*|onu|ona|bunu|buna|bunlar\w*|bu\b|şu\b|aynı zamanda|ayrıca|ama|ancak|fakat|çünkü|bu yüzden|"
    r"bu nedenle|yani|ise|de\b|da\b|değil|ve\b)", re.I)


def _needs_previous(x: KeptSentence) -> bool:
    t = x.text.strip()
    return bool(t) and (t[0].islower() or bool(_DEPENDENT_START.match(t)))


def _fix_dependencies(chosen: list[KeptSentence], sents: list[KeptSentence], dur, max_s: float) -> list[KeptSentence]:
    """Bağlı başlayan bir cümlenin öncülü seçilmemişse: süre yetiyorsa öncülü ekle, yetmiyorsa cümleyi çıkar."""
    order = {x.id: i for i, x in enumerate(sents)}
    chosen = sorted(chosen, key=lambda x: order[x.id])
    banned: set[int] = set()  # çıkarılan cümle tekrar eklenmez (ekle-çıkar döngüsü olmaz)
    changed, rounds = True, 0
    while changed and rounds < 100:
        changed, rounds = False, rounds + 1
        ids = {x.id for x in chosen}
        for x in list(chosen):
            if x is chosen[0] and order[x.id] == 0:
                continue
            i = order[x.id]
            # yarım kalan cümle (ASR cümleyi ortadan bölmüş): devamı eklenir ya da cümle çıkarılır
            nxt = sents[i + 1] if i + 1 < len(sents) else None
            if not re.search(r"[.!?…]\W*$", x.text.strip()) and nxt and nxt.id not in ids:
                if nxt.id not in banned and dur(chosen + [nxt]) <= max_s:
                    chosen = sorted(chosen + [nxt], key=lambda y: order[y.id])
                elif len(chosen) > 2:
                    chosen.remove(x)
                    banned.add(x.id)
                else:
                    continue
                changed = True
                break
            prev = sents[i - 1] if i > 0 else None
            if not _needs_previous(x) or (prev and prev.id in ids):
                continue
            if prev and prev.id not in banned and dur(chosen + [prev]) <= max_s:
                chosen = sorted(chosen + [prev], key=lambda y: order[y.id])
            elif len(chosen) > 2:
                chosen.remove(x)
                banned.add(x.id)
            else:
                continue
            changed = True
            break
    return chosen


def _story_highlight(tl: Timeline, tr: Transcript, sents: list[KeptSentence], max_s: float) -> list[KeptSentence] | None:
    """LLM hikâyeyi oluşturan cümleleri seçer (ardışık olmak zorunda değil); kod süreyi ve bütünlüğü denetler."""
    lines = "\n".join(f"[{x.id}] ({x.out_end - x.out_start:.1f} sn) {x.text}" for x in sents)
    pick = chat_json([{"role": "system", "content": STORY_SYSTEM},
                      {"role": "user", "content": f"Süre sınırı: {max_s:.0f} saniye (en az {max_s * 0.7:.0f}).\n\n{lines}"}],
                     StoryPick, max_tokens=600)
    by_id = {x.id: x for x in sents}
    chosen = [by_id[i] for i in sorted(set(pick.sentence_ids)) if i in by_id]
    if len(chosen) < 2:
        return None
    dur = lambda cs: sum(x.out_end - x.out_start for x in cs) + 0.3 * (len(cs) - 1)
    # sınır aşılırsa: açılış ve kapanış korunur, aradaki en uzun cümleler çıkarılır
    while dur(chosen) > max_s and len(chosen) > 2:
        mid = max(chosen[1:-1], key=lambda x: x.out_end - x.out_start)
        chosen.remove(mid)
    chosen = _fix_dependencies(chosen, sents, dur, max_s)
    if dur(chosen) > max_s or dur(chosen) < max_s * 0.45:
        return None
    return chosen


def _apply_highlight(tl: Timeline, tr: Transcript, sents: list[KeptSentence], preset: Preset) -> list[KeptSentence]:
    """Shorts süresini aşan videolarda hikâyeyi kuran cümleleri seçtirir, kalanı keser."""
    if not preset.max_duration or tl.output_duration() <= preset.max_duration:
        return sents
    try:
        chosen = _story_highlight(tl, tr, sents, preset.max_duration)
    except Exception:
        chosen = None
    if chosen:
        from ..core.timeline import complement

        for a, b in complement(_story_keep(tr, chosen), 0.0, tl.source.duration):
            tl.add_cut(a, b, CutReason.trim, "kısa video hikâyesi dışında")
        return kept_sentences(tr, tl.time_map())
    # yedek: tek ardışık aralık
    prompt = (
        f"Bu video {tl.output_duration():.0f} saniye; {preset.max_duration:.0f} saniyelik dikey kısa video "
        "için tek başına anlamlı, en ilgi çekici ardışık cümle aralığını seç (başlangıç ve bitiş cümle numarası).\n\n"
        + format_sentences(sents)
    )
    hl = chat_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}], Highlight)
    ids = [s.id for s in sents]
    a = min(max(hl.start_sentence, ids[0]), ids[-1])
    b = min(max(hl.end_sentence, a), ids[-1])
    chosen = [s for s in sents if a <= s.id <= b]
    while chosen and chosen[-1].out_end - chosen[0].out_start > preset.max_duration and len(chosen) > 1:
        chosen.pop()
    if not chosen:
        return sents
    start = tr.words[chosen[0].word_ids[0]].start - 0.15
    end = tr.words[chosen[-1].word_ids[-1]].end + 0.25
    tl.add_cut(0, max(0.0, start), CutReason.trim, "öne çıkan bölüm dışı")
    tl.add_cut(end, tl.source.duration, CutReason.trim, "öne çıkan bölüm dışı")
    return kept_sentences(tr, tl.time_map())


# --------------------------------------------------------------------------- LLM ile tekrar çekim tespiti


class RetakeItem(BaseModel):
    sentence_id: int
    phrase: str = Field(description="KESİLECEK yarım/hatalı ilk denemenin, cümleden BİREBİR kopyası")
    reason: str = Field(description="Kısa gerekçe")


class RetakeList(BaseModel):
    retakes: list[RetakeItem] = Field(default_factory=list, max_length=10)


RETAKE_SYSTEM = """Sen dikkatli bir video editörüsün. Konuşmacı bazen bir ifadeyi yarıda keser ve hemen \
ardından baştan ya da düzelterek tekrar söyler (tekrar çekim). Görevin bu YARIM veya HATALI İLK DENEMELERİ \
bulmak; düzeltilmiş son hali korunur.

Örnekler:
- "[12] kendi enerjisini yakarak, [13] enerjisini içeriden yakarak başlatır." -> sentence_id 12, \
phrase "kendi enerjisini yakarak,"
- "[30] bağışıklığın önemli bir, bağışıklığın etkili bir silahını alır." -> sentence_id 30, \
phrase "bağışıklığın önemli bir,"

Kurallar:
- Sadece hemen ardından tekrar edilen veya düzeltilen ifadeleri seç. Vurgu amaçlı bilinçli tekrarları \
ve anlamlı içeriği seçme. Emin değilsen listeye ekleme.
- "phrase" cümleden birebir kopyalanmalı ve yalnızca atılacak kısmı içermeli.
Sadece JSON döndür."""


def _strict_locate(tr: Transcript, sent: KeptSentence, phrase: str, min_score: float = 0.75) -> tuple[int, int] | None:
    """İfadeyi cümlede bulur; yeterince benzer değilse None (yanlış yeri kesmemek için)."""
    target = [normalize(t) for t in phrase.split() if normalize(t)]
    words = [normalize(tr.words[i].text) for i in sent.word_ids]
    n = len(target)
    if not n or n > len(words):
        return None
    best, best_i = 0.0, -1
    for i in range(len(words) - n + 1):
        score = SequenceMatcher(None, words[i:i + n], target).ratio()
        if score > best:
            best, best_i = score, i
    if best < min_score:
        return None
    return sent.word_ids[best_i], sent.word_ids[best_i + n - 1]


class RetakeVerdict(BaseModel):
    keep_both: bool = Field(description="İki ifade de anlamlı ve ayrı bilgiler veriyorsa true (kesme)")


RETAKE_VERIFY = """Sen dikkatli bir video editörüsün. Konuşmacı bazen bir ifadeyi yarıda bırakır ve hemen \
ardından düzelterek tekrar söyler. Sana iki seçenek veriliyor: A (orijinal konuşma) ve B (köşeli \
parantezdeki kısım kesilmiş hali). B hâlâ doğal ve eksiksiz bir cümleyse ve kesilen kısım gerçekten \
tekrarlanan bir ilk denemeyse keep_both=false de. Kesilen kısım yeni bir bilgi taşıyorsa keep_both=true de."""


def detect_retakes_llm(tl: Timeline, tr: Transcript, sents: list[KeptSentence],
                       progress: Progress | None = None) -> list[str]:
    """Tekrar çekimler: kod aday bulur, LLM her adayı kısa bir soruyla onaylar ve kesimin başlangıcını seçer.

    Tüm transkripti tek seferde küçük bir modele okutmak hem kaçırma hem uydurma (doğru cümleyi
    kesme) üretiyordu; aday + onay yöntemi küçük modellerde çok daha güvenilir.
    """
    from ..analyze.fillers import restart_candidates

    kept = [i for s in sents for i in s.word_ids]
    cands = restart_candidates(tr, kept)
    found: list[str] = []
    for k, c in enumerate(cands):
        if progress:
            progress("plan", 0.05 + 0.05 * k / max(len(cands), 1), f"tekrar adayı {k + 1}/{len(cands)} kontrol ediliyor")
        before = " ".join(tr.words[i].text for i in c["before"])
        cut = " ".join(tr.words[i].text for i in c["cut"])
        after = " ".join(tr.words[i].text for i in c["after"])
        # Kesim yeri kodla (hizalamayla) belirlendi; LLM yalnızca veto edebilir.
        try:
            v = chat_json([{"role": "system", "content": RETAKE_VERIFY},
                           {"role": "user", "content": f"A: {before} {cut} {after}\nB: {before} [{cut}] {after}"}],
                          RetakeVerdict, temperature=0.0, max_tokens=40, retries=1)
            if v.keep_both and c["score"] < 0.75:  # çok güçlü hizalamada veto dikkate alınmaz
                continue
        except Exception:
            pass
        ids = c["cut"]
        for wid in ids:
            tr.words[wid].is_retake = True
        tl.add_cut(tr.words[ids[0]].start - 0.03, tr.words[ids[-1]].end + 0.03, CutReason.retake, "tekrar çekim")
        found.append(" ".join(tr.words[w].text for w in ids))
    return found


def smart_plan(project: Project, progress: Progress | None = None, instruction: str | None = None) -> Timeline:
    """Temizlik planı üzerine LLM ile anlama + kurgu öğeleri ekler, yeni sürüm kaydeder."""
    rep = progress or (lambda *a: None)
    preset = load_preset(project.info.preset)
    tl = project.timeline()
    tr = project.transcript()

    cfg = get_config()
    if not has_model(cfg.models.llm):
        raise LLMError(f"LLM modeli Ollama'da yok: {cfg.models.llm}. `kurgu doctor` ile kontrol edin.")

    context = ""
    vis = project.load_analysis("vision")
    if vis is None and cfg.ollama.vision_frames > 0:
        if has_model(cfg.models.vlm):
            try:
                rep("plan", 0.02, "kareler görsel modelle inceleniyor")
                from ..analyze.vision import describe_video

                vis = describe_video(project, tl.source.duration, cfg.ollama.vision_frames).model_dump()
            except Exception as e:  # görsel bağlam opsiyonel
                rep("plan", 0.05, f"görsel analiz atlandı: {e}"[:60])
    if vis:
        context = f"{vis.get('setting', '')}; tür: {vis.get('video_type', '')}; stil: {vis.get('visual_style', '')}"

    sents = kept_sentences(tr, tl.time_map())
    if sents and preset.cleanup.remove_retakes:
        retakes = detect_retakes_llm(tl, tr, sents, rep)
        if retakes:
            project.save_transcript(tr)
            project.save_analysis("retakes_llm", retakes)
            sents = kept_sentences(tr, tl.time_map())
    if not sents:
        return tl
    rep("plan", 0.1, "en iyi bölüm seçiliyor" if preset.max_duration else "plan hazırlanıyor")
    sents = _apply_highlight(tl, tr, sents, preset)

    plans: list[ChunkPlan] = []
    chunks = chunk_sentences(sents)
    avg_broll = (preset.broll.every_min + preset.broll.every_max) / 2
    avg_zoom = (preset.zoom.every_min + preset.zoom.every_max) / 2
    single = len(chunks) == 1
    meta: GlobalMeta | None = None
    for i, chunk in enumerate(chunks):
        dur = max(chunk[-1].out_end - chunk[0].out_start, 1.0)
        user = _chunk_prompt(
            chunk, preset, context,
            # Az ama isabetli öneri: çıktı kısa kalır (hız), zoom boşluklarını kural doldurur
            n_broll=min(8, max(1, round(dur / avg_broll))) if preset.broll.enabled else 0,
            n_text=min(8, max(1, round(dur / 20))),
            n_zoom=min(6, max(1, round(dur / (avg_zoom * 3)))) if preset.zoom.enabled else 0,
            want_chapters=preset.output.width > preset.output.height and dur > 60,
        )
        if single:
            user += "\n\nAyrıca videonun başlığını, hook cümlesini, açıklamasını, hashtag'lerini ve müzik ruh halini üret."
        if instruction:
            user += f"\n\nKullanıcının isteği: {instruction}"
        rep("plan", 0.1 + 0.75 * i / len(chunks), f"bölüm {i + 1}/{len(chunks)} planlanıyor")
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        if single:
            full = chat_json(msgs, FullPlan)
            plans.append(full)
            meta = GlobalMeta(**full.model_dump(include=set(GlobalMeta.model_fields)))
        else:
            plans.append(chat_json(msgs, ChunkPlan))

    summary = " ".join(p.summary for p in plans)
    if meta is not None and len(meta.hook.split()) > 7:
        # sadece hook'u kısalttır (tüm planı yeniden üretmekten çok daha ucuz)
        class Short(BaseModel):
            hook: str

        try:
            short = chat_json([{"role": "user", "content": (
                f'Bu video hook cümlesini anlamını koruyarak en fazla 6 kelimeye kısalt: "{meta.hook}"')}],
                Short, max_tokens=60, retries=0)
            meta.hook = _limit_words(short.hook, 7, "Hook")
        except Exception:
            meta.hook = _limit_words(meta.hook, 7, "Hook")
    if meta is None:
        rep("plan", 0.88, "başlık ve açıklama yazılıyor")
        meta_prompt = (
            f"Video özeti: {summary}\n\nVideonun başı:\n{format_sentences(sents[:12])}\n\n"
            f"Preset: {preset.name}. Başlık, hook, açıklama, hashtag ve müzik ruh halini üret."
        )
        if instruction:
            meta_prompt += f"\nKullanıcının isteği: {instruction}"
        meta = chat_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": meta_prompt}], GlobalMeta)

    # Ham LLM çıktısı: hata ayıklama ve kural filtrelerinin etkisini görmek için
    project.save_analysis("plan_raw", {"plans": [p.model_dump() for p in plans],
                                       "meta": meta.model_dump() if meta else None})
    tl = assemble(tl, tr, preset, sents, plans, meta, summary)
    project.save_timeline(
        tl, note=f"Akıllı plan: {len(tl.brolls)} b-roll, {len(tl.texts)} yazı, {len(tl.zooms)} zoom"
    )
    rep("plan", 1.0, f"{len(tl.brolls)} b-roll, {len(tl.texts)} yazı, {len(tl.zooms)} zoom")
    return tl
