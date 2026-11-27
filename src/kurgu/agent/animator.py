"""Animasyon tasarımcısı ajanı: konuşmadaki kavramlara uygun hareketli grafik şablonlarını seçer ve doldurur.

Küçük modeller için güvenilir olsun diye:
  - video kodla zaman dilimlerine bölünür, her dilim için en fazla bir animasyon istenir
  - sayı içeren cümleler işaretlenir (büyük sayı / termometre şablonları için ipucu)
  - parametreler şablon şemasına göre doğrulanır; hatalı öneriler sessizce elenir
Şablon kataloğu motion/templates/ klasöründen otomatik okunur: yeni bir şablon eklemek,
ajanın onu kullanmaya başlaması için yeterlidir.
"""

from __future__ import annotations

import re
from typing import Callable

from pydantic import BaseModel, Field

from ..core.config import get_config, load_agents
from ..core.project_store import Project
from ..core.timeline import Anchor, MotionItem, Timeline, Transcript
from ..generate.motion_gfx import MotionError, catalog_for_llm, list_templates, motion_span, validate_params
from .llm import chat_json
from .planner import format_sentences, kept_sentences, locate_phrase

Progress = Callable[[str, float, str], None]

DEFAULT_SYSTEM = """Sen bir hareketli grafik (motion graphics) tasarımcısısın. Konuşmayı izleyiciye daha iyi \
anlatmak için uygun anlara animasyon şablonları yerleştirirsin.
Kurallar:
- Her dilim için EN FAZLA bir animasyon öner; uygun bir an yoksa o dilimi atla.
- Şablonu içeriğe göre seç: sayı/istatistik -> big_number; eşik/sıcaklık -> gauge; NASIL çalıştığı adım adım \
anlatılıyorsa -> process_steps; birkaç şey sıralanıyorsa -> bullet_list; tarih/evrim -> timeline; uyarı veya \
önemli sonuç -> callout; soru veya vurucu cümle -> word_slam; somut nesne/canlı -> icon_pop.
- Tam ekran şablonları (process_steps, timeline) videoda en fazla 2 kez kullan.
- Metinler videonun dilinde, kısa ve konuşulanla birebir uyumlu olsun. İkon alanlarına İNGİLİZCE tek-iki \
kelimelik anahtar kelime yaz (ör. 'brain', 'thermometer', 'microbe').
- 'phrase' alanı cümleden birebir kopyalanır; animasyon o kelimelerde başlar."""


class MotionPick(BaseModel):
    sentence_id: int
    phrase: str = Field(description="Cümleden BİREBİR kopyalanmış 1-5 kelime: animasyonun başlayacağı an")
    template: str = Field(description="Şablon kimliği (katalogdan)")
    params: dict = Field(description="Şablonun parametreleri (katalogdaki şemaya ve örneğe uygun)")


class MotionPlan(BaseModel):
    motions: list[MotionPick] = Field(default_factory=list, max_length=8)


_NUM = re.compile(r"(\d|%|yüzde|milyon|milyar|bin\b|derece)", re.I)


def _system() -> str:
    a = (load_agents().get("agents") or {}).get("animator") or {}
    s = (a.get("system") or "").strip()
    return DEFAULT_SYSTEM if not s or s == "builtin" else s


def _model() -> str:
    a = (load_agents().get("agents") or {}).get("animator") or {}
    return (a.get("model") or "").strip() or get_config().models.llm


def _overlaps(a, b, margin=0.6) -> bool:
    return a[0] < b[1] + margin and b[0] < a[1] + margin


def plan_motions(project: Project, tl: Timeline, tr: Transcript, progress: Progress | None = None,
                 every: float = 14.0, notes: str = "", report: dict | None = None) -> list[MotionItem]:
    """Animasyonları planlar ve timeline'a ekler (render etmez). Eklenen öğeleri döndürür."""
    from .director import format_slots, make_slots

    tpls = list_templates()
    if not tpls:
        return []
    tm = tl.time_map()
    sents = kept_sentences(tr, tm)
    start_guard = 3.0 if any(t.style == "hook" for t in tl.texts) else 0.5
    slots = make_slots(sents, every, start_guard)[:8]
    if not slots:
        return []
    for slot in slots:  # sayı ipucu
        for s in slot:
            if _NUM.search(s.text) and "(sayı)" not in s.text:
                s.text = f"{s.text} (sayı)"
    if progress:
        progress("plan", 0.6, f"Animasyon tasarımcısı çalışıyor ({len(slots)} dilim)")
    plan = chat_json(
        [{"role": "system", "content": _system() + (f"\n\n{notes}" if notes else "")},
         {"role": "user", "content": (
             f"Şablon kataloğu:\n{catalog_for_llm(tpls)}\n\n"
             f"Aşağıda {len(slots)} zaman dilimi var. Her dilim için en fazla bir animasyon öner.\n\n"
             f"{format_slots(slots)}")}],
        MotionPlan, model=_model(), max_tokens=2200,
    )
    # Anlatım garantisi: tam ekran açıklayıcı (süreç/zaman çizelgesi) seçilmediyse odaklı ikinci soru
    full_ids = {t.id for t in tpls.values() if t.layout == "full"}
    if full_ids and not any(p.template in full_ids for p in plan.motions) and \
            not any(m.layout == "full" for m in tl.motions):
        if progress:
            progress("plan", 0.7, "Anlatım animasyonu aranıyor")
        try:
            extra = chat_json(
                [{"role": "system", "content": _system()},
                 {"role": "user", "content": (
                     "Bu videoda bir şeyin NASIL çalıştığı adım adım ya da zaman içinde nasıl geliştiği anlatılıyor mu? "
                     "Evetse en uygun yer için TAM OLARAK BİR animasyon öner (yalnızca aşağıdaki şablonlardan). "
                     "Adımlar konuşulanı izlemeli; kısa ve videonun dilinde olmalı.\n\n"
                     f"Şablonlar:\n{catalog_for_llm({k: tpls[k] for k in full_ids})}\n\n{format_slots(slots)}")}],
                MotionPlan, model=_model(), max_tokens=900, retries=1,
            )
            plan.motions = [m for m in extra.motions if m.template in full_ids][:1] + plan.motions
        except Exception:
            pass

    return place_picks(tl, tr, sents, plan.motions, tpls, start_guard, report)


_TEMP = re.compile(r"derece|°|ısı|sıcaklı|ateş(?:i|imiz)? \d", re.I)


def _num_variants(v) -> list[str]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return []
    if f.is_integer():
        return [str(int(f))]
    s = f"{f:g}"
    return [s, s.replace(".", ",")]


def _has_numbers(text: str, nums: list) -> bool:
    for n in nums:
        vs = _num_variants(n)
        if vs and not any(re.search(rf"(?<![\d.,]){re.escape(v)}(?![\d])", text) for v in vs):
            return False
    return True


def ground_pick(pick: MotionPick, tpl, sents: list, tpls: dict) -> tuple[MotionPick | None, str]:
    """Ekranda çıkacak sayılar konuşmada geçmeli (LLM sayı uydurabilir). Sayı başka cümlede söyleniyorsa animasyon
    oraya taşınır; hiç söylenmiyorsa reddedilir. Termometre yalnızca sıcaklık için; değilse büyük sayı kartı."""
    specs = tpl.spec.get("params", {})
    # yalnızca ekranda büyük gösterilen değer doğrulanır (min/max/eşik gibi ölçek ayarları konuşmada geçmeyebilir)
    nums = [pick.params.get("value")] if specs.get("value", {}).get("type") == "number" and \
        pick.params.get("value") is not None else []
    if not nums:
        return pick, ""
    idx = next((i for i, s in enumerate(sents) if s.id == pick.sentence_id), None)
    order = ([idx] if idx is not None else []) + [i for d in (1, -1, 2) for i in ([idx + d] if idx is not None else [])
                                                   if 0 <= i < len(sents)] + list(range(len(sents)))
    hit = next((sents[i] for i in order if _has_numbers(sents[i].text, nums)), None)
    if hit is None:
        return None, f"{tpl.id}: sayı ({', '.join(map(str, nums))}) konuşmada geçmiyor"
    note = ""
    if hit.id != pick.sentence_id:
        v = _num_variants(nums[0])[0]
        tok = next((w for w in hit.text.split() if v in w), v)
        pick = pick.model_copy(update={"sentence_id": hit.id, "phrase": tok})
        note = f"{tpl.id}: sayının söylendiği cümleye taşındı ([{hit.id}])"
    if tpl.id == "gauge" and not _TEMP.search(hit.text) and "big_number" in tpls:
        p = dict(pick.params)
        val = p.get("value")
        unit = str(p.get("unit") or p.get("suffix") or "")
        pct = "%" in hit.text and "%" not in unit
        pick = pick.model_copy(update={"template": "big_number", "params": {
            "value": val, "prefix": "%" if pct else "", "suffix": "" if pct else unit,
            "label": p.get("label") or "", "icon": p.get("icon") or "warning", "position": "top"}})
        note = (note + "; " if note else "") + "termometre sıcaklık değil: büyük sayıya çevrildi"
    return pick, note


_DANGLING_END = {"bir", "ve", "ile", "için", "de", "da", "gibi", "bu", "şu", "o", "çok", "en", "ki", "veya", "ama",
                 "sonraki", "olan", "kadar", "daha"}
# adım şablonu yalnızca konuşmada gerçek bir sıra anlatılıyorsa ("süreç" gibi genel kelimeler yetmez)
_PROCESS_CUES = ("önce", "sonra", "ardından", "ilk olarak", "ikinci olarak", "üçüncü", "adım", "aşama", "birinci",
                 "son olarak", "daha sonra")



def _words(t: str) -> list[str]:
    return re.sub(r"[^\wçğıöşü ]", " ", str(t).replace("İ", "i").replace("I", "ı").lower()).split()


def junk_reason(tpl, params: dict, sents: list, sentence_id: int) -> str | None:
    """Küçük modelin ürettiği anlamsız animasyon metinlerini yakalar:
    - liste öğeleri cümlelerden kopyalanmış yarım parçalarsa ("Bilim insanları bir sonraki")
    - öğe bağlaç/yarım kelimeyle bitiyorsa
    - adım şablonu, konuşmada gerçekten bir süreç/sıra anlatılmıyorsa"""
    texts = [" ".join(_words(s.text)) for s in sents]
    items: list[str] = []
    for name, spec in tpl.spec.get("params", {}).items():
        v = params.get(name)
        if spec.get("type") == "list" and isinstance(v, list):
            for it in v:
                if isinstance(it, dict):
                    items.append(" ".join(str(it.get(k, "")) for k in ("when", "label", "text") if it.get(k)))
                else:
                    items.append(str(it))
    # tek metin alanları (başlık, açıklama): yarım cümle ile bitmemeli
    for name, spec in tpl.spec.get("params", {}).items():
        v = params.get(name)
        if spec.get("type") == "string" and isinstance(v, str) and v.strip():
            w = _words(v)
            if w and w[-1] in _DANGLING_END:
                return f"{tpl.id}: '{name}' yarım cümleyle bitiyor"
    if items:
        frag = 0
        for it in items:
            w = _words(it)
            if not w:
                frag += 1
                continue
            if w[-1] in _DANGLING_END:
                frag += 1
                continue
            j = " ".join(w)
            if len(w) >= 3 and any(j in t and not t.endswith(j) for t in texts):
                frag += 1  # cümlenin ortasından kopyalanmış parça: kendi başına bir adım/madde değil
        if frag * 2 >= len(items):
            return f"{tpl.id}: öğeler cümle parçası ({frag}/{len(items)})"
    if tpl.id == "process_steps":
        idx = next((i for i, s in enumerate(sents) if s.id == sentence_id), None)
        window = " ".join(s.text.lower() for s in sents[max(0, (idx or 0) - 1):(idx or 0) + 4])
        if not any(re.search(rf"(?<!\w){c}(?!\w)", window) for c in _PROCESS_CUES):  # tam kelime: 'sonraki' sayılmaz
            return "process_steps: konuşmada adım adım bir süreç anlatılmıyor"
    return None


def _stem(word: str) -> str:
    from ..analyze.fillers import normalize

    w = normalize(word)
    return w[:5] if len(w) > 5 else w  # Türkçe ekler: "kırılgandır" ~ "kırılgan"


def _card_texts(template: str, params: dict) -> list[str]:
    """Kartın ekranda yazdığı ve konuşmada geçmesi beklenen metinler."""
    out: list[str] = []
    if template == "word_slam":
        out += [w for w in params.get("words") or [] if isinstance(w, str)]
    for k in ("label", "title"):
        if isinstance(params.get(k), str):
            out.append(params[k])
    return out


def content_anchor(tr: Transcript, sents: list, texts: list[str]) -> tuple[object, int] | None:
    """Kartın metninin konuşmada GERÇEKTEN söylendiği yeri bulur: (cümle, kelime kimliği).
    Kart o kelimeyle birlikte belirir; metin konuşmada yoksa None."""
    stop = {"ve", "bir", "bu", "da", "de", "ile", "için", "çok", "en", "ne", "mi"}
    keys = [_stem(t) for txt in texts for t in txt.split() if _stem(t) and _stem(t) not in stop and len(_stem(t)) >= 3]
    if not keys:
        return None
    best, best_score = None, 0.0
    for sent in sents:
        stems = [_stem(tr.words[i].text) for i in sent.word_ids]
        hits = [j for j, st in enumerate(stems) if st in keys]
        score = len({stems[j] for j in hits}) / len(set(keys))
        if hits and score > best_score:
            best, best_score = (sent, sent.word_ids[hits[0]]), score
    return best if best_score >= 0.5 else None


def _short_label(*cands) -> str | None:
    """Kurtarma kartı için 1-3 kelimelik, yarım kalmamış etiket."""
    for c in cands:
        if not isinstance(c, str):
            continue
        words = re.sub(r"[^\wÇĞİÖŞÜçğıöşü'\- ]", " ", c).split()
        if 1 <= len(words) <= 3 and words[-1].lower() not in _DANGLING_END and len(" ".join(words)) <= 28:
            return " ".join(words)
    return None


def salvage_pick(pick: MotionPick, tpls: dict) -> MotionPick | None:
    """Kalite kapısından geçemeyen bir öneriyi (uydurma sayı, sırası olmayan süreç, parça liste) atmak yerine
    aynı andaki kavramı vurgulayan sade bir ikon kartına çevirir. İngilizce ikon anahtarı ve konuşmadan
    alınmış kısa bir etiket yoksa kurtarılmaz."""
    if "icon_pop" not in tpls or pick.template == "icon_pop":
        return None
    prm = pick.params or {}
    icon = prm.get("icon") if isinstance(prm.get("icon"), str) else None
    if not icon:  # liste şablonları (süreç, zaman çizelgesi, madde): ilk öğenin ikonu
        for v in prm.values():
            if isinstance(v, list):
                icon = next((it.get("icon") for it in v if isinstance(it, dict) and isinstance(it.get("icon"), str)), None)
                if icon:
                    break
    if not icon or not re.fullmatch(r"[a-z][a-z \-]{1,30}", icon.strip().lower()):
        return None
    label = _short_label(prm.get("label"), prm.get("title"), pick.phrase)
    if not label:
        return None
    return MotionPick(sentence_id=pick.sentence_id, phrase=pick.phrase, template="icon_pop",
                      params={"icon": icon.strip().lower(), "label": label})


def place_picks(tl: Timeline, tr: Transcript, sents: list, picks_in: list[MotionPick], tpls: dict,
                start_guard: float, report: dict | None = None, max_full: int = 2, max_same: int = 2,
                full_guard: float = 8.0) -> list[MotionItem]:
    """Önerilen animasyonları doğrular ve kurallara göre yerleştirir (sayı doğrulama, çakışma, tam ekran sınırı,
    aynı şablon sınırı, açılışta tam ekran yasağı, b-roll)."""
    tm = tl.time_map()
    words = tr.word_map()
    by_id = {s.id: s for s in sents}
    taken = [m for m in tl.motions]
    taken_spans = [sp for sp in (motion_span(m, tl, words, tm) for m in taken) if sp]
    def broll_spans():
        out = []
        for b in tl.brolls:
            try:
                r = tm.range_to_out(*b.anchor.resolve(words))
            except ValueError:
                r = None
            out.append((b, r))
        return out

    full_count = sum(1 for m in tl.motions if m.layout == "full")
    added: list[MotionItem] = []
    rejected = report.setdefault("rejected", []) if report is not None else []
    # Tam ekran anlatımlar önce yerleşir: en bilgilendirici öğeler onlar
    picks = sorted(picks_in, key=lambda p: 0 if getattr(tpls.get(p.template), "layout", "") == "full" else 1)
    used: dict[str, int] = {}
    for m in tl.motions:
        used[m.template] = used.get(m.template, 0) + 1
    queue = list(picks)
    depth: dict[int, int] = {}  # kurtarma/kaydırma derinliği (en fazla 2): sonsuz döngü olmaz

    def retry(new: MotionPick | None, why: str, parent: MotionPick):
        d = depth.get(id(parent), 0) + 1
        if new is not None and d <= 2:
            depth[id(new)] = d
            queue.append(new)
            report is not None and report.setdefault("salvaged", []).append(f"{why} -> {new.template}")

    while queue:
        pick = queue.pop(0)
        second = depth.get(id(pick), 0) >= 2
        tpl = tpls.get(pick.template)
        if not tpl or pick.sentence_id not in by_id:
            rejected.append(f"{pick.template}: bilinmeyen şablon veya cümle")
            continue
        orig = pick
        pick, note = ground_pick(pick, tpl, sents, tpls)
        if note:
            rejected.append(note) if pick is None else (report is not None and report.setdefault("grounded", []).append(note))
        if pick is None:
            if not second:
                retry(salvage_pick(orig, tpls), note or "doğrulanamadı", orig)
            continue
        tpl = tpls.get(pick.template)
        s = by_id.get(pick.sentence_id)
        same_limit = max_same + (1 if tpl.id == "icon_pop" else 0)  # hafif ikon kartı bir kez daha tekrarlanabilir
        if used.get(tpl.id, 0) >= same_limit:
            rejected.append(f"{tpl.id}: aynı şablon sınırı ({same_limit})")
            continue
        raw = dict(pick.params or {})
        if tpl.id == "big_number" and re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]", str(raw.get("prefix", ""))):
            # model birimi öne koymuş ("milyon" + 600): birim sona taşınır -> "600 milyon yıl"
            raw["suffix"] = f" {raw.pop('prefix')} {raw.get('suffix', '')}".rstrip()
        try:
            params = validate_params(tpl, raw)
        except MotionError as e:
            rejected.append(f"{tpl.id}: {e}")
            continue
        bad = junk_reason(tpl, params, sents, s.id)
        if bad:
            rejected.append(bad)
            if not second:
                retry(salvage_pick(pick, tpls), bad, pick)
            continue
        if tpl.layout == "full" and full_count >= max_full:
            rejected.append(f"{tpl.id}: tam ekran sınırı ({max_full})")
            if not second:
                retry(salvage_pick(pick, tpls), "tam ekran sınırı", pick)
            continue
        ws, _ = locate_phrase(tr, s, pick.phrase)
        # kart, yazdığı kelimelerin söylendiği ana bağlanır (model başka bir cümle vermiş olsa bile)
        hit = content_anchor(tr, sents, _card_texts(tpl.id, params))
        if hit:
            if hit[0].id != s.id and report is not None:
                report.setdefault("reanchored", []).append(f"{tpl.id}: cümle {s.id} -> {hit[0].id}")
            s, ws = hit
        item = MotionItem(id=tl.next_id("m"), anchor=Anchor(word_start=ws, word_end=ws), template=tpl.id,
                          params=params, layout=tpl.layout)
        span = motion_span(item, tl, words, tm)
        if not span or span[0] < start_guard:
            rejected.append(f"{tpl.id}: başlangıç bölgesinde/kesik")
            continue
        if tpl.layout == "full" and span[0] < full_guard:
            rejected.append(f"{tpl.id}: açılışta tam ekran (konuşmacı görünmeli)")
            if not second:  # açılışta konuşmacı görünmeli: tam ekran yerine küçük kart
                retry(salvage_pick(pick, tpls), "açılışta tam ekran", pick)
            continue
        if any(_overlaps(span, o) for o in taken_spans):
            rejected.append(f"{tpl.id}: başka animasyonla çakışıyor")
            continue
        clash = [b for b, r in broll_spans() if r and _overlaps(span, r, 0.2)]
        if clash:
            if tpl.layout == "full":  # anlatım animasyonu, aynı andaki b-roll'un yerini alır
                tl.brolls = [b for b in tl.brolls if b not in clash]
                report is not None and report.setdefault("replaced_brolls", []).extend(b.id for b in clash)
            else:
                rejected.append(f"{tpl.id}: b-roll ile çakışıyor")
                if not second:  # komşu cümleye kaydırmayı dene (b-roll'un hemen öncesi/sonrası)
                    for nb in (s.id + 1, s.id - 1):
                        if nb in by_id:
                            phrase = " ".join(by_id[nb].text.split()[:3])
                            retry(pick.model_copy(update={"sentence_id": nb, "phrase": phrase}), "b-roll çakışması", pick)
                            break
                continue
        tl.motions.append(item)
        taken_spans.append(span)
        added.append(item)
        full_count += tpl.layout == "full"
        used[tpl.id] = used.get(tpl.id, 0) + 1

    # animasyonla çakışan ekran yazıları (aynı bölgede iki öğe kalabalık eder) kaldırılır
    if added:
        keep = []
        for t in tl.texts:
            try:
                r = tm.range_to_out(*t.anchor.resolve(words))
            except ValueError:
                r = None
            if t.style != "hook" and r and any(_overlaps(r, sp, 0.3) for sp in taken_spans):
                continue
            keep.append(t)
        tl.texts = keep
    return added


def animate(project: Project, progress: Progress | None = None, render_assets: bool = True) -> tuple[Timeline, dict]:
    """Mevcut kurguya animasyon ekler, render eder ve yeni sürüm kaydeder."""
    tl = project.timeline()
    tr = project.transcript()
    report: dict = {}
    added = plan_motions(project, tl, tr, progress, report=report)
    report["added"] = [f"{m.id}:{m.template}" for m in added]
    if render_assets and added:
        from ..generate.motion_gfx import fill_motions

        report["render"] = fill_motions(project, tl, progress, ids=[m.id for m in added])
    project.save_timeline(tl, note=f"Animasyonlar: {', '.join(m.template for m in added) or 'yok'}")
    return tl, report
