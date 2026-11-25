"""Senarist: videonun kurgu senaryosunu yazar; uygulayıcı ajanlar senaryoyu hayata geçirir.

Akış:
  1) Ölçüm araçları + görsel model -> video profili (analyze/profile.py): "nasıl bir video?"
  2) Senarist (LLM) -> brif: anlatı yapısı, ton, tempo, tema, görsel dil, müzik
  3) Senarist (LLM) -> sahne planı (beat): video kodla zaman dilimlerine bölünür, HER DİLİM İÇİN bir karar:
       konuşmacı (yalın) | b-roll | animasyon (şablon adıyla) | yazı
     Küçük modeller için güvenilir: dilimler kodla belirlenir, çıktı en fazla 8 dilimlik gruplar halinde istenir,
     dağılım (b-roll/animasyon oranı, art arda aynı tür) kodla dengelenir.
  4) Uygulayıcılar senaryodaki beat'leri gerçekleştirir:
       b-roll -> sanat yönetmeni (İngilizce görsel prompt) -> görsel/video üretimi
       animasyon -> animasyon tasarımcısı (şablon parametreleri) -> Electron render
       yazı -> ekran yazısı; konuşmacı + vurgu -> zoom
       konuşmadaki çağrılar -> greenscreen/CTA (kural tabanlı)
       müzik -> besteci (ACE-Step) veya müzik kütüphanesi
Senaryo analysis/senaryo.json olarak kaydedilir ve arayüzde gösterilir.
"""

from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, Field

from ..core.config import get_config, load_agents
from ..generate.motion_gfx import catalog_short, list_templates
from .llm import chat_json

Progress = Callable[[str, float, str], None]

VISUALS = ("konuşmacı", "b-roll", "animasyon", "yazı")

DEFAULT_SYSTEM = """Sen deneyimli bir video senaristi ve kurgu yönetmenisin. Ham bir konuşma videosunu, izleyiciyi \
sonuna kadar tutan bir kurguya dönüştürecek SENARYOYU yazarsın. Kararların uygulayıcı ekiplere gider:
b-roll ekibi (ara görüntü üretir), animasyon ekibi (hazır şablonlarla hareketli grafik yapar), yazı ekibi.
İlkeler:
- Anlatıyı yapılandır: güçlü giriş (hook), merak, açıklama, örnek, doruk, kapanış.
- Görsel çeşitlilik: konuşmacıyı da göster; aynı türü art arda kullanma. Duygusal veya kişisel anlarda konuşmacı kalsın.
- b-roll: konuşulan şey somut ve görselleştirilebilirse (nesne, yer, olay, vücut, doğa).
- animasyon: sayı, istatistik, adımlar, karşılaştırma, uyarı, liste, zaman içinde gelişim anlatılıyorsa.
- yazı: vurucu kısa cümle, soru veya anahtar kavram.
- Metinler videonun dilinde; 'phrase' alanı cümleden birebir kopyalanır."""


class ScriptBrief(BaseModel):
    logline: str = Field(description="Videonun tek cümlelik özü (videonun dilinde)")
    structure: str = Field(description="Anlatı yapısı, oklarla: ör. 'soru -> neden -> mekanizma -> uyarı -> sonuç'")
    title: str = Field(description="Dikkat çekici başlık, en fazla 70 karakter")
    hook: str = Field(description="İlk 3 saniyede ekranda çıkacak merak uyandırıcı cümle, en fazla 6 kelime")
    description: str = Field(description="2-3 cümlelik açıklama")
    hashtags: list[str] = Field(description="3-6 hashtag", max_length=6)
    tone: str = Field(description="Ton ve anlatım tarzı, kısa")
    pacing: Literal["sakin", "orta", "hızlı"]
    broll_density: Literal["az", "orta", "çok"]
    text_density: Literal["az", "orta", "çok"]
    zoom_intensity: Literal["yok", "hafif", "güçlü"] = Field(description=(
        "Konuşan kişinin göründüğü videolarda en az 'hafif' (kesimleri yumuşatır); 'yok' yalnızca ekran kaydı vb. için"))
    caption_theme: Literal["apple", "bold", "neon", "minimal"]
    visual_style: str = Field(description=(
        "TÜM b-roll'lara eklenecek İNGİLİZCE görsel dil (renk paleti, ışık, çekim tarzı), ör. "
        "'clean bright scientific macro photography, soft daylight, teal and amber palette'"))
    avoid: str = Field(description="İNGİLİZCE, görsellerde kaçınılacaklar, ör. 'text, letters, logos, gore'")
    music_mood: Literal["calm", "upbeat", "inspiring", "dramatic", "corporate", "none"]
    notes: str = Field(description="Uygulayıcı ekiplere kısa notlar")


class Beat(BaseModel):
    slot: int = Field(description="Dilim numarası")
    sentence_id: int = Field(description="Bu dilimdeki cümlelerden biri")
    phrase: str = Field(description="O cümleden BİREBİR kopyalanmış 1-5 kelime: öğenin başlayacağı an")
    purpose: Literal["hook", "merak", "açıklama", "örnek", "vurgu", "uyarı", "kapanış"]
    visual: Literal["konuşmacı", "b-roll", "animasyon", "yazı"]
    template: str = Field(default="", description="visual=animasyon ise şablon kimliği, değilse boş")
    idea: str = Field(description="Ne gösterilecek: kısa, somut (videonun dilinde); yazı ise ekrandaki 1-4 kelime")


class BeatPlan(BaseModel):
    beats: list[Beat] = Field(default_factory=list, max_length=8)


class EditScript(BaseModel):
    brief: ScriptBrief
    beats: list[Beat] = []

    profile: dict = {}


def _agent(name: str) -> dict:
    return (load_agents().get("agents") or {}).get(name) or {}


def system_prompt() -> str:
    s = (_agent("screenwriter").get("system") or "").strip()
    return DEFAULT_SYSTEM if not s or s == "builtin" else s


def _model() -> str:
    return (_agent("screenwriter").get("model") or "").strip() or get_config().models.llm


def _big_model(name: str) -> bool:
    """Geniş bağlamı rahat taşıyan modeller (14B ve üstü): tüm videoyu görür, daha büyük gruplar yazar."""
    import re as _re

    m = _re.search(r"(\d+)b", name.lower())
    return bool(m and int(m.group(1)) >= 14)


def write_brief(style: str, profile_txt: str, transcript_text: str, fmt: str, rep: Progress) -> ScriptBrief:
    rep("plan", 0.12, "Senarist anlatıyı kuruyor (brif)")
    return chat_json(
        [{"role": "system", "content": system_prompt()},
         {"role": "user", "content": (
             f"İstenen stil: {style}\nFormat: {fmt}\n\nVIDEO PROFİLİ (analiz araçlarından)\n{profile_txt}\n\n"
             f"TRANSKRİPT ([cümle no] (saniye) metin)\n{transcript_text}\n\nSenaryo brifini JSON olarak ver.")}],
        ScriptBrief, model=_model(), temperature=0.5, retries=1, max_tokens=900,
        think=bool(_agent("screenwriter").get("think", False)),
    )


def write_beats(brief: ScriptBrief, slots: list[list], rep: Progress, batch: int | None = None) -> list[Beat]:
    """Her dilim için bir beat; atlanan dilimler yeniden sorulur.

    Küçük modelde 5'erli gruplar (bağlam penceresine sığsın). Büyük modelde 10'arlı gruplar ve her istekte
    videonun tamamının metni verilir: senarist bütün hikâyeyi bilerek karar verir (tekrarsız, akışa uygun).
    """
    from .planner import format_sentences

    big = _big_model(_model())
    batch = batch or (10 if big else 5)
    whole = ""
    if big:
        whole = "TÜM VİDEO (bağlam için; sadece aşağıdaki dilimler için beat yaz)\n" + "\n".join(
            f"[{n}] " + " ".join(x.text for x in sl) for n, sl in enumerate(slots, 1)) + "\n\n"

    tpls = {k: t for k, t in list_templates().items() if not t.spec.get("auto")}
    catalog = catalog_short(tpls)
    beats: list[Beat] = []

    def ask(items: list[tuple[int, list]], recent: str) -> dict[int, Beat]:
        nums = [n for n, _ in items]
        text = "\n".join(f"--- Dilim {n} ({sl[0].out_start:.0f}-{sl[-1].out_end:.0f} sn) ---\n{format_sentences(sl)}"
                         for n, sl in items)
        try:
            plan = chat_json(
                [{"role": "system", "content": system_prompt()},
                 {"role": "user", "content": (
                     f"BRİF\nÖz: {brief.logline}\nYapı: {brief.structure}\nTon: {brief.tone}, tempo: {brief.pacing}, "
                     f"b-roll yoğunluğu: {brief.broll_density}\n\n"
                     f"ANİMASYON ŞABLONLARI (visual=animasyon ise template bunlardan biri)\n{catalog}\n\n"
                     f"{whole}Önceki dilimlerin görsel türleri: {recent}\n\n"
                     f"Aşağıda {len(items)} zaman dilimi var: {', '.join(map(str, nums))}. HER BİRİ İÇİN TAM OLARAK "
                     f"BİR beat yaz; beats listesi {len(items)} öğe içermeli, slot = dilim numarası. "
                     "visual: konuşmacı | b-roll | animasyon | yazı.\n\n" + text)}],
                BeatPlan, model=_model(), max_tokens=230 * len(items) + 150,
            )
        except Exception:
            return {}
        return {b.slot: b for b in plan.beats if b.slot in nums}

    for g in range(0, len(slots), batch):
        items = [(g + i + 1, sl) for i, sl in enumerate(slots[g:g + batch])]
        rep("plan", 0.2 + 0.25 * g / max(len(slots), 1),
            f"Senarist sahne planını yazıyor ({g + 1}-{g + len(items)}/{len(slots)})")
        recent = ", ".join(b.visual for b in beats[-3:]) or "-"
        got = ask(items, recent)
        missing = [(n, sl) for n, sl in items if n not in got]
        if missing:  # küçük modeller bazen dilim atlar: eksikler tek seferde yeniden sorulur
            got.update(ask(missing, recent))
        for n, sl in items:
            b = got.get(n)
            if b is None:  # yine yoksa konuşmacıda kalır (dengeleyici gerekirse animasyona çevirir)
                s = sl[0]
                b = Beat(slot=n, sentence_id=s.id, phrase=" ".join(s.text.split()[:3]), purpose="açıklama",
                         visual="konuşmacı", idea="")
            beats.append(b)
    return sorted(beats, key=lambda b: b.slot)


# senaristin fikir metninden / cümleden şablon tahmini (model türü yanlış etiketlediğinde)
_TPL_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("timeline", ("zaman çizelgesi", "timeline", "evrim", "milyon yıl", "yıl önce", "tarihçe", "zamanla")),
    ("gauge", ("derece", "°c", "sıcaklık", "termometre", "ısı")),
    ("big_number", ("yüzde", "%", "istatistik", "oran", "sayı")),
    ("callout", ("uyarı", "dikkat", "tehlike", "risk", "zararlı", "önemli")),
    ("process_steps", ("adım adım", "süreç", "nasıl çalış", "mekanizma", "aşama", "process")),
    ("bullet_list", ("liste", "madde", "gruplar", "sırala")),
    ("word_slam", ("soru", "vurucu", "slogan")),
]
_ANIM_WORDS = ("animasyon", "grafik", "infografik", "çizelge", "diyagram", "şema", "sayaç")


def infer_template(text: str, tpls: dict) -> str | None:
    t = (text or "").lower()
    for tid in tpls:
        if tid in t and not tpls[tid].spec.get("auto"):
            return tid
    for tid, words in _TPL_HINTS:
        if tid in tpls and any(w in t for w in words):
            return tid
    return None


def balance(beats: list[Beat], slots: list[list], brief: ScriptBrief) -> list[Beat]:
    """Dağılımı kodla dengeler: geçersiz/yanlış etiketli şablonlar, oranlar (dilim sayısına göre), art arda aynı tür,
    tam ekran sınırı ve videonun yalnızca konuşmacıdan oluşmaması için en az animasyon sayısı."""
    tpls = list_templates()
    n = max(len(slots), len(beats), 1)
    cap = {"b-roll": {"az": 0.2, "orta": 0.32, "çok": 0.45}[brief.broll_density], "animasyon": 0.34,
           "yazı": {"az": 0.1, "orta": 0.2, "çok": 0.3}[brief.text_density]}
    limit = {k: max(1, round(v * n)) for k, v in cap.items()}
    sent_text = {s.id: getattr(s, "text", "") for sl in slots for s in sl}
    used = {k: 0 for k in VISUALS}
    full = 0
    out: list[Beat] = []

    def can_full(tid: str) -> bool:
        return tpls[tid].layout != "full" or full < 2

    for b in beats:
        slot_ids = {s.id for s in slots[b.slot - 1]} if 0 < b.slot <= len(slots) else set()
        if b.sentence_id not in slot_ids and slot_ids:
            b.sentence_id = min(slot_ids)
        idea = b.idea.lower()
        # model animasyonu tarif edip başka tür yazmışsa (ör. "zaman çizelgesi ile..." + b-roll) düzelt
        if b.visual != "animasyon" and (any(w in idea for w in _ANIM_WORDS) or infer_template(idea, tpls) in
                                         ("timeline", "process_steps")):
            tid = infer_template(b.idea + " " + sent_text.get(b.sentence_id, ""), tpls)
            if tid:
                b.visual, b.template = "animasyon", tid
        if b.visual == "animasyon":
            t = tpls.get(b.template)
            if not t or t.spec.get("auto"):
                tid = infer_template(b.idea + " " + sent_text.get(b.sentence_id, ""), tpls)
                b.visual, b.template = ("animasyon", tid) if tid else ("b-roll" if b.idea else "konuşmacı", "")
        if b.visual == "animasyon" and not can_full(b.template):
            b.visual, b.template = "yazı", ""
        if b.visual != "konuşmacı" and used[b.visual] + 1 > limit.get(b.visual, n):
            tid = infer_template(b.idea + " " + sent_text.get(b.sentence_id, ""), tpls) if b.visual == "b-roll" else None
            if tid and used["animasyon"] < limit["animasyon"] and can_full(tid):
                b.visual, b.template = "animasyon", tid  # fazla b-roll anlatılabiliyorsa animasyona
            else:
                b.visual, b.template = "konuşmacı", ""
        if b.visual != "konuşmacı" and len(out) >= 2 and out[-1].visual == out[-2].visual == b.visual:
            b.visual, b.template = "konuşmacı", ""  # üç kez art arda aynı tür olmasın
        if b.visual == "animasyon" and tpls[b.template].layout == "full":
            full += 1
        used[b.visual] += 1
        out.append(b)

    # en az animasyon: sayı/süreç/uyarı içeren konuşmacı dilimleri animasyona çevrilir
    need = max(2, n // 5) - used["animasyon"]
    for i, b in enumerate(out):
        if need <= 0:
            break
        if b.visual != "konuşmacı" or b.slot == 1:
            continue
        tid = infer_template(sent_text.get(b.sentence_id, "") + " " + b.idea, tpls)
        if not tid or not can_full(tid):
            continue
        if (i > 0 and out[i - 1].visual == "animasyon") or (i + 1 < len(out) and out[i + 1].visual == "animasyon"):
            continue
        b.visual, b.template = "animasyon", tid
        b.idea = b.idea or sent_text.get(b.sentence_id, "")[:80]
        full += tpls[tid].layout == "full"
        need -= 1
    return out


def format_script(script: EditScript) -> str:
    b = script.brief
    lines = [f"Öz: {b.logline}", f"Yapı: {b.structure}", f"Ton: {b.tone} · Tempo: {b.pacing} · Tema: {b.caption_theme}"]
    for x in script.beats:
        t = f" [{x.template}]" if x.template else ""
        lines.append(f"{x.slot:>2}. {x.purpose:<9} {x.visual}{t}: {x.idea}")
    return "\n".join(lines)
