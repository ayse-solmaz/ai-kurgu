"""Dolgu sözcükleri, kekelemeler ve tekrar çekimlerin (retake) kurala dayalı tespiti.

LLM tabanlı daha akıllı retake tespiti Faz 2'de planlayıcıda yapılır; buradaki kurallar
hızlı ve deterministik ilk geçiştir.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from ..core.timeline import Transcript

# Her zaman dolgu sayılan sesler (düzenli ifadeyle tekrarlanan harfler de yakalanır).
FILLER_PATTERNS = [
    r"ı+",
    r"ı+m+",
    r"e+",
    r"e+h+",
    r"e+m+",
    r"a+h*",
    r"h+m+",
    r"m+h*m+",
    r"hı+m+",
    r"ö+h*",
    r"u+m+",
    r"u+h+",
    r"e+r+m+",
]
FILLER_RE = re.compile(rf"^(?:{'|'.join(FILLER_PATTERNS)})$")

# Bağlama göre dolgu olan kelimeler: "bir şey" gibi anlamlı kullanımları korumak için
# önceki kelimeye bakılır.
SOFT_FILLERS = {"şey"}
SOFT_KEEP_AFTER = {"bir", "her", "hiçbir", "o", "bu", "şu", "ne", "başka", "aynı", "hiç", "öyle", "böyle"}

# Bu söylem belirteçleri anlamı değiştirebileceği için varsayılan olarak kesilmez.
DISCOURSE_MARKERS = {"yani", "hani", "işte", "like", "basically"}

_TR_LOWER = str.maketrans({"I": "ı", "İ": "i"})


def normalize(text: str) -> str:
    text = text.translate(_TR_LOWER).lower()
    return re.sub(r"[^\wçğıöşü]+", "", text)


def is_filler_token(norm: str, prev_norm: str | None) -> bool:
    if not norm:
        return False
    if FILLER_RE.match(norm):
        # "e" ve "a" Türkçede tek başına anlamlı olabilir ("e mi?", "a evet") ama transkriptte
        # tek başına durduklarında çoğunlukla dolgudur; 'o' ve 'u' zamir olduğu için kalıpta yok.
        return True
    if norm in SOFT_FILLERS and (prev_norm or "") not in SOFT_KEEP_AFTER:
        return True
    return False


def mark_fillers(tr: Transcript) -> int:
    count = 0
    prev = None
    for w in tr.words:
        n = normalize(w.text)
        w.is_filler = is_filler_token(n, prev)
        count += w.is_filler
        prev = n
    return count


# Kekeleme olarak tekrarlanması muhtemel kısa işlev sözcükleri. Türkçede "yavaş yavaş",
# "kelime kelime" gibi ikilemeler doğal olduğundan diğer kelimelerde duraklama şartı aranır.
FUNCTION_WORDS = {
    "ben", "sen", "o", "biz", "siz", "onlar", "bu", "şu", "ve", "ama", "fakat", "yani", "bir", "de", "da",
    "ki", "ne", "nasıl", "çünkü", "eğer", "şey", "ile", "için", "gibi", "the", "a", "i", "and", "to",
}


def mark_stutters(tr: Transcript, max_gap: float = 0.8, pause: float = 0.25) -> int:
    """Art arda tekrarlanan kelimelerde ilkini işaretler: "ben ben gittim" -> ilk "ben".

    İkilemeleri ("yavaş yavaş") korumak için: tekrar ya bir işlev sözcüğünde olmalı ya da
    iki kelime arasında duraklama / virgül / tire bulunmalı.
    """
    count = 0
    words = tr.words
    for a, b in zip(words, words[1:]):
        na, nb = normalize(a.text), normalize(b.text)
        gap = b.start - a.end
        if not na or a.is_filler:
            continue
        broken = a.text.rstrip().endswith(("-", ",", "…", "..."))
        if na == nb and gap <= max_gap and (na in FUNCTION_WORDS or gap >= pause or broken):
            a.is_stutter = True
            count += 1
        # yarım kalan kelime: "gel- geliyorum", "video... videonuzu"
        elif len(na) >= 2 and len(nb) > len(na) and nb.startswith(na) and (broken or gap >= pause):
            a.is_stutter = True
            count += 1
    return count


def _norm_words(tr: Transcript, ids: list[int]) -> list[str]:
    out = []
    for i in ids:
        w = tr.words[i]
        n = normalize(w.text)
        if n and not w.is_filler:
            out.append(n)
    return out


# Konuşmacının kayıt sırasında kendine/kurgucuya söylediği "baştan al" işaretleri
STRONG_CUES = {"daha", "tekrar", "baştan", "yeniden", "pardon", "kes"}
CUE_WORDS = STRONG_CUES | {"bir", "bi", "alıyorum", "alalım", "alayım", "dur", "durun", "tamam", "hadi", "olmadı", "yine"}
# tek başına bir "cümle" olarak kalınca anlamsız olan kelimeler ("İşte." / "Yani.")
ISOLATED_DROP = DISCOURSE_MARKERS | {"bir", "bi", "daha", "tekrar", "baştan", "pardon", "dur"}


def _strip_cue(words: list[str]) -> tuple[list[str], bool]:
    """Sondaki "..., bir daha" gibi işaret kelimelerini ayırır. (kalan kelimeler, işaret var mıydı)"""
    k = len(words)
    while k > 0 and words[k - 1] in CUE_WORDS:
        k -= 1
    tail = words[k:]
    if tail and any(w in STRONG_CUES for w in tail):
        return words[:k], True
    return words, False


def _is_cue_only(words: list[str]) -> bool:
    if not words:
        return False
    if len(words) == 1:
        return words[0] in ISOLATED_DROP
    return all(w in CUE_WORDS for w in words) and any(w in STRONG_CUES for w in words)


def _close(x: str, y: str) -> bool:
    """Aynı kök ya da transkripsiyon farkı: 'bileşen' ~ 'birleşen', 'aşıları' ~ 'aşılar'."""
    return _similar(x, y) or (len(x) >= 5 and len(y) >= 5 and SequenceMatcher(None, x, y).ratio() >= 0.8)


def _repeats(core: list[str], wb: list[str], min_ratio: float) -> bool:
    """core, wb'nin başında (yaklaşık) yeniden söyleniyor mu?"""
    if len(core) < 2 or len(wb) < 2:
        return False
    head = wb[: len(core) + 2]
    ratio = SequenceMatcher(None, core, head).ratio()
    prefix = 0
    for x, y in zip(core, wb):
        if not _close(x, y):
            break
        prefix += 1
    # aynı 4 kelimeyle başlamak uzun cümlelerde de güçlü işarettir (sonrası farklı ifade edilebilir)
    is_prefix = (prefix >= min(3, len(core)) and prefix / len(core) >= 0.5) or prefix >= 4
    # başlangıç örtüşmesi (sıra/ek farklarına dayanıklı): yalnızca içerik kelimeleri sayılır ve ilk içerik
    # kelimesi wb'nin ilk birkaç kelimesinde geçmelidir ("bu ... yapay zeka" gibi ortak kelimeler yetmez)
    cc = [x for x in core if len(x) >= 4][:6]
    head_overlap = (len(cc) >= 3 and any(_close(cc[0], y) for y in wb[:3])
                    and sum(1 for x in cc if any(_close(x, y) for y in wb[: len(cc) + 3])) / len(cc) >= 0.66)
    return (ratio >= min_ratio or is_prefix or head_overlap) and len(core) <= len(wb) + 2


def _shared(core: list[str], wb: list[str]) -> int:
    return sum(1 for x in set(core) if len(x) >= 4 and any(_close(x, y) for y in wb))


def mark_retakes(tr: Transcript, min_ratio: float = 0.72, max_gap: float = 25.0, look: int = 3) -> int:
    """Konuşmacı bir cümleyi yarıda kesip baştan söylediyse önceki denemeleri işaretler; son deneme tutulur.

    - "Bir daha." / "Tekrar." gibi yalnızca işaret kelimelerinden oluşan cümleler ve tek başına kalan
      "İşte." / "Yani." kesilir.
    - Bir cümle, sonraki `look` asıl cümleden biri (aradaki işaret cümleleri atlanarak) onunla aynı
      başlıyorsa tekrar çekimdir (1., 2. deneme ... son deneme tutulur).
    - "..., bir daha." ile biten ve ardından içeriği tekrar edilen cümle de başarısız denemedir.
    """
    sents = tr.sentences
    W = [_norm_words(tr, s.word_ids) for s in sents]
    cue_only = [_is_cue_only(w) for w in W]
    count = 0
    for k, a in enumerate(sents):
        if not W[k]:
            continue
        hit = cue_only[k]
        if not hit:
            core, had_cue = _strip_cue(W[k])
            nxt = []
            for j in range(k + 1, len(sents)):
                if sents[j].start - a.end > max_gap:
                    break
                if cue_only[j] or len(W[j]) < 2:
                    continue
                nxt.append(j)
                if len(nxt) >= look:
                    break
            # hemen ardından "Bir daha." geliyorsa (konuşmacı denemeyi kendisi iptal etti) ve sonraki
            # asıl cümle içeriği tekrar ediyorsa bu cümle başarısız denemedir
            cue_next = (k + 1 < len(sents) and cue_only[k + 1] and any(w in STRONG_CUES for w in W[k + 1])
                        and sents[k + 1].start - a.end <= 4.0)
            if cue_next and nxt and _shared(core, W[nxt[0]]) >= 2:
                hit = True
            for j in nxt:
                if hit:
                    break
                if _repeats(core, W[j], min_ratio) or (had_cue and _shared(core, W[j]) >= 2):
                    hit = True
        if hit:
            for i in a.word_ids:
                tr.words[i].is_retake = True
            count += 1
    return count


def _same(x: str, y: str) -> bool:
    return x == y or (len(x) >= 3 and y.startswith(x))


def mark_restarts(tr: Transcript, min_match: int = 3, max_len: int = 14, max_gap: float = 3.0) -> int:
    """Cümle içi baştan başlamalar: "bu uygulama videonuzu izliyor ve, bu uygulama videonuzu izliyor ve gereksiz..."

    Bir kelime dizisi (en az `min_match` kelime) hemen ardından büyük ölçüde aynen tekrar
    ediliyorsa ilk deneme işaretlenir.
    """
    toks = [(w.id, normalize(w.text)) for w in tr.words
            if not (w.is_filler or w.is_stutter or w.is_retake) and normalize(w.text)]
    count = 0
    i, n = 0, len(toks)
    while i < n:
        hit = False
        for L in range(min(max_len, (n - i) // 2), min_match - 1, -1):
            a = [t for _, t in toks[i:i + L]]
            b = [t for _, t in toks[i + L:i + 2 * L]]
            if not all(_same(x, y) for x, y in zip(a[:min_match], b[:min_match])):
                continue
            gap = tr.words[toks[i + L][0]].start - tr.words[toks[i + L - 1][0]].end
            if gap > max_gap or SequenceMatcher(None, a, b).ratio() < 0.8:
                continue
            for wid, _ in toks[i:i + L]:
                tr.words[wid].is_retake = True
            count += 1
            i += L
            hit = True
            break
        if not hit:
            i += 1
    return count


def _similar(a: str, b: str) -> bool:
    """Aynı kelime ya da aynı kökten (ilk 5 harf): 'enerjisini' ~ 'enerjisi'."""
    return a == b or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5])


def restart_candidates(tr: Transcript, kept_ids: list[int], max_back: int = 10, max_span: float = 8.0) -> list[dict]:
    """Konuşmacının bir ifadeyi düzelterek yeniden başlattığı olası yerler.

    Kural: j konumundaki kelime, son `max_back` kelime içinde (bitişik olmayan) bir kelimeyle aynıysa
    ve ardından gelen kelimeler önceki parçayla en az 2 içerik kelimesi paylaşıyorsa (veya arada
    duraklama varsa) aday sayılır. Kesin karar LLM onayıyla verilir; bu yüzden aday listesi geniş tutulur.
    """
    toks = [(i, normalize(tr.words[i].text)) for i in kept_ids]
    toks = [(i, n) for i, n in toks if n]
    cands: list[dict] = []
    skip_until = -1
    for j in range(2, len(toks)):
        if j <= skip_until or len(toks[j][1]) < 3:
            continue
        for i in range(max(0, j - max_back), j - 1):
            if not _similar(toks[i][1], toks[j][1]):
                continue
            first, restart = tr.words[toks[i][0]], tr.words[toks[j][0]]
            if restart.start - first.start > max_span:
                continue
            a = [t for _, t in toks[i:j]]
            b = [t for _, t in toks[j:j + len(a) + 2]]
            matches = sum(1 for x in a if len(x) >= 3 and any(_similar(x, y) for y in b))
            pause = restart.start - tr.words[toks[j - 1][0]].end
            if not (matches >= 2 or (matches >= 1 and pause >= 0.35)):
                continue
            # Kesilecek parça hizalamayla seçilir: ilk deneme (toks[s:j]) yeniden başlayan kısmın
            # başıyla örtüşmeli. "enerjisini yakarak ‖ enerjisini içeriden yakarak" -> yüksek puan,
            # "üretirler ancak bu ‖ üretim bağışıklık" -> düşük puan (anlamlı içerik, kesilmez).
            best_s, best_score = None, 0.0
            for s in range(max(0, i - 2), i + 1):
                first_try = [t for _, t in toks[s:j]]
                if len(first_try) > 6:
                    continue
                again = [t for _, t in toks[j:j + len(first_try) + 1]]
                same_cnt = sum(1 for x in first_try if any(_similar(x, y) for y in again))
                score = 2 * same_cnt / (len(first_try) + len(again))
                if score > best_score:
                    best_s, best_score = s, score
            if best_s is None or best_score < 0.55:
                continue
            cands.append({
                "cut": [toks[k][0] for k in range(best_s, j)],
                "before": [toks[k][0] for k in range(max(0, best_s - 6), best_s)],
                "after": [toks[k][0] for k in range(j, min(len(toks), j + 7))],
                "score": round(best_score, 2),
            })
            skip_until = j + 2
            break
    return cands


def analyze_disfluencies(tr: Transcript, fillers=True, stutters=True, retakes=True) -> dict:
    for w in tr.words:
        w.is_filler = w.is_stutter = w.is_retake = False
    stats = {"fillers": 0, "stutters": 0, "retakes": 0}
    if fillers:
        stats["fillers"] = mark_fillers(tr)
    if stutters:
        stats["stutters"] = mark_stutters(tr)
    if retakes:
        stats["retakes"] = mark_retakes(tr) + mark_restarts(tr)
    return stats
