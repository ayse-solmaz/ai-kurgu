"""Yönetmen ajanı ve alt ajanlar.

Akış:
  1. Tekrar çekim analisti   -> yarım kalan/düzeltilen ifadeleri keser
  2. Yönetmen (akıl yürütme)  -> kullanıcının stil isteğine göre kreatif brif
  3. B-roll sanat yönetmeni   -> brifteki görsel dile uygun ara görüntü planı
  4. Hareket/yazı tasarımcısı -> brifteki tempoya göre ekran yazıları ve zoom'lar
  5. Kurallar (kod)           -> aralık/süre/yoğunluk; altyazı stili uygulanır
  6. Varlık üretimi           -> FLUX/stok/bulut
  7. Görsel denetçi (VLM)     -> zayıf görselleri sanat yönetmenine yeniden yazdırır

Sistem prompt'ları ve modeller agents.yaml'dan okunur (arayüzden düzenlenebilir).
Bu bilgisayarda Ollama tek iGPU'da istekleri sırayla işlediği için alt ajanlar paralel değil,
sırayla ve kısa çıktılarla çalışır.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Literal

from pydantic import BaseModel, Field

from ..core.config import get_config, load_agents, load_preset
from ..core.project_store import Project
from ..core.timeline import Timeline
from .llm import LLMError, chat_json, has_model
from .planner import (
    ChunkPlan,
    GlobalMeta,
    PlanBRoll,
    PlanText,
    PlanZoom,
    _limit_words,
    assemble,
    chunk_sentences,
    detect_retakes_llm,
    format_sentences,
    kept_sentences,
)

from ..render.caption_themes import apply_caption_theme

Progress = Callable[[str, float, str], None]


# --------------------------------------------------------------------------- şemalar


class DirectorBrief(BaseModel):
    title: str = Field(description="Videonun başlığı (videonun dilinde, en fazla 70 karakter)")
    hook: str = Field(description="İlk 3 saniyede ekranda çıkacak merak uyandırıcı cümle, en fazla 6 kelime")
    description: str = Field(description="2-3 cümlelik açıklama")
    hashtags: list[str] = Field(description="3-6 hashtag")
    tone: str = Field(description="Ton ve anlatım tarzı, kısa")
    pacing: Literal["sakin", "orta", "hızlı"]
    caption_theme: Literal["apple", "bold", "neon", "minimal"] = Field(default="apple", description=(
        "Altyazı ve animasyon teması. apple: zarif, modern, mavi kapsül vurgu (çoğu video için en iyisi); "
        "bold: büyük harf, sarı vurgu, enerjik; neon: parlak, teknoloji/oyun; minimal: sade, belgesel/sakin"))
    broll_density: Literal["az", "orta", "çok"]
    visual_style: str = Field(description=(
        "TÜM b-roll'lara eklenecek İNGİLİZCE görsel dil (renk paleti, ışık, çekim tarzı). Örnek: "
        "'dark cinematic scientific visualization, deep red and amber palette, macro photography, "
        "volumetric light, shallow depth of field'"))
    avoid: str = Field(description="İNGİLİZCE, görsellerde kaçınılacaklar. Örnek: 'text, letters, signs, logos, gore, distorted faces'")
    text_density: Literal["az", "orta", "çok"]
    zoom_intensity: Literal["yok", "hafif", "güçlü"] = Field(
        description="Konuşan kişinin göründüğü videolarda en az 'hafif' önerilir; 'yok' yalnızca ekran kaydı vb. için")
    music_mood: Literal["calm", "upbeat", "inspiring", "dramatic", "corporate", "none"]
    notes: str = Field(description="Alt ekiplere kısa notlar (videonun dilinde)")


class ArtPlan(BaseModel):
    brolls: list[PlanBRoll] = Field(default_factory=list, max_length=8)  # maxItems: kontrolsüz uzun çıktıyı engeller


class MotionPlan(BaseModel):
    texts: list[PlanText] = Field(default_factory=list, max_length=8)  # maxItems: kontrolsüz uzun çıktıyı engeller
    zooms: list[PlanZoom] = Field(default_factory=list, max_length=8)  # maxItems: kontrolsüz uzun çıktıyı engeller


# --------------------------------------------------------------------------- yardımcılar


def _agent(name: str) -> dict:
    return (load_agents().get("agents") or {}).get(name) or {}


def _model(name: str, fallback: str) -> str:
    m = (_agent(name).get("model") or "").strip()
    return m or fallback


def _system(name: str, default: str = "") -> str:
    s = (_agent(name).get("system") or "").strip()
    return default if not s or s == "builtin" else s


def default_style() -> str:
    return (load_agents().get("default_style") or "").strip()


def tuned_preset(preset, brief: DirectorBrief):
    """Brifteki yoğunluk/tempo kararlarını preset kurallarına çevirir."""
    p = preset.model_copy(deep=True)
    k = {"az": 1.7, "orta": 1.0, "çok": 0.6}[brief.broll_density]
    p.broll.every_min *= k
    p.broll.every_max *= k
    if brief.zoom_intensity == "yok":
        p.zoom.enabled = False
    elif brief.zoom_intensity == "hafif":
        p.zoom.scale = min(p.zoom.scale, 1.07)
        p.zoom.every_min *= 1.6
        p.zoom.every_max *= 1.6
    else:
        p.zoom.enabled = True
        p.zoom.scale = max(p.zoom.scale, 1.14)
        p.zoom.every_min *= 0.75
        p.zoom.every_max *= 0.75
    pace = {"sakin": 1.3, "orta": 1.0, "hızlı": 0.8}[brief.pacing]
    p.zoom.every_min *= pace
    p.zoom.every_max *= pace
    p.captions = apply_caption_theme(p.captions, brief.caption_theme, p.output.width, p.output.height)
    return p


def make_slots(sents: list, every: float, start: float) -> list[list]:
    """Cümleleri ~`every` saniyelik ardışık zaman dilimlerine böler (başlangıç koruması hariç)."""
    slots: list[list] = []
    cur: list = []
    slot_start = None
    for s in sents:
        if s.out_start < start:
            continue
        if slot_start is None:
            slot_start = s.out_start
        if cur and s.out_start - slot_start >= every:
            slots.append(cur)
            cur, slot_start = [], s.out_start
        cur.append(s)
    if cur and (not slots or cur[-1].out_end - cur[0].out_start > every * 0.4):
        slots.append(cur)
    return slots


def format_slots(slots: list[list]) -> str:
    out = []
    for i, slot in enumerate(slots, 1):
        out.append(f"--- Dilim {i} ({slot[0].out_start:.0f}-{slot[-1].out_end:.0f} sn) ---")
        out.append(format_sentences(slot))
    return "\n".join(out)


_TR_CHARS = set("çğıöşüÇĞİÖŞÜ")


def _ensure_english(text: str) -> str:
    """Görsel üretim modeli Türkçe anlamaz: Türkçe karakter varsa kısa bir çağrıyla çevir."""
    if not any(c in _TR_CHARS for c in text):
        return text

    class En(BaseModel):
        english: str

    try:
        return chat_json([{"role": "user", "content": f"Translate to English, keep it as a short style phrase: {text}"}],
                         En, max_tokens=120, retries=0).english
    except Exception:
        return "dark cinematic scientific visualization, macro photography, volumetric light"


_DANGLING = {"bir", "ve", "ile", "de", "da", "ama", "için", "gibi", "çok", "en", "ki", "veya"}
_TRAIL = "—–-,:; "


def _is_dangling(text: str) -> bool:
    """Yarım kalmış cümle: tire/virgülle ya da bağlaçla bitiyor."""
    t = text.strip()
    return t.endswith(tuple(_TRAIL.strip())) or (t.split() or [""])[-1].lower() in _DANGLING


def _trim_dangling(text: str) -> str:
    """Sondaki bağlaç ve noktalama kalıntılarını temizler: '... değil — bir' -> '... değil'."""
    t = text.strip()
    while t:
        t = t.rstrip(_TRAIL)
        words = t.split()
        if words and words[-1].lower() in _DANGLING:
            t = " ".join(words[:-1])
            continue
        break
    return t


_LEAK = re.compile(r"\s*[\[(]\s*\d+(?:[.,]\d+)?\s*s(?:n|aniye)?\s*[\])]|\s*\[\d+\]")


def _clean_leaks(text: str) -> str:
    """Transkript biçiminden sızan '(0.0s)' / '[12]' gibi kalıntıları siler."""
    return _LEAK.sub("", text or "").strip()


_STYLE_DEFAULTS = {
    "eğitici": "clean bright documentary photography, soft natural daylight, realistic detail, shallow depth of field",
    "bilim": "scientific macro photography, clean studio lighting, realistic, crisp detail, cool teal and warm amber accents",
    "vlog": "natural candid lifestyle photography, warm daylight, realistic, gentle film look",
    "ürün tanıtımı": "premium product photography, soft studio light, minimal background, crisp detail",
}


def _valid_visual_style(style: str, kind: str | None) -> str:
    """Görsel dil, b-roll prompt'larına eklenir: kısa/tema adı gibi geçersiz değerleri profile göre düzeltir."""
    s = _clean_leaks(style)
    bad = {"apple", "bold", "neon", "minimal", "teknoloji", "oyun", "eğlence", "belgesel", "sakin"}
    words = [w.strip(",./").lower() for w in s.split()]
    if len(words) < 5 or sum(w in bad for w in words) >= 2 or "/" in s:
        return _STYLE_DEFAULTS.get(kind or "", _STYLE_DEFAULTS["eğitici"])
    return s


def _shorten_hook(hook: str) -> str:
    hook = _clean_leaks(hook)
    if len(hook.split()) <= 7 and not _is_dangling(hook):
        return hook.strip()

    class Short(BaseModel):
        hook: str

    try:
        s = chat_json([{"role": "user", "content": (
            f'Bu hook cümlesini anlamını koruyarak, TAM bir cümle olarak en fazla 6 kelimeye kısalt: "{hook}"')}],
            Short, max_tokens=60, retries=0).hook
        if len(s.split()) <= 7:
            return _trim_dangling(s) if _is_dangling(s) else s.strip()
    except Exception:
        pass
    return _trim_dangling(_limit_words(hook, 7, "Hook"))


# --------------------------------------------------------------------------- ana akış


_THEME_WORDS = {
    "apple": ("apple", "zarif", "modern"),
    "bold": ("bold", "kalın", "büyük harf", "sarı"),
    "neon": ("neon", "parlak", "oyun", "gaming"),
    "minimal": ("minimal", "sade", "sakin altyazı"),
}


def _theme_from_style(style: str) -> str:
    s = (style or "").lower()
    for theme, words in _THEME_WORDS.items():
        if any(w in s for w in words) and theme != "apple":
            return theme
    return (load_agents().get("default_theme") or "apple").strip() or "apple"


def _fallback_highlight(tl: Timeline, tr, sents: list, max_s: float) -> list:
    """LLM olmadan: baştan, cümle sınırında, süre sınırına sığan bölüm."""
    from ..core.timeline import CutReason

    chosen = []
    for s in sents:
        if chosen and s.out_end - chosen[0].out_start > max_s:
            break
        chosen.append(s)
    if not chosen or len(chosen) == len(sents):
        return sents
    end = tr.words[chosen[-1].word_ids[-1]].end + 0.25
    tl.add_cut(end, tl.source.duration, CutReason.trim, "süre sınırı dışı")
    return kept_sentences(tr, tl.time_map())


def _fill_animation_params(beats: list, sents: list, rep: Progress) -> list:
    """Animasyon ekibi: senaristin seçtiği şablonların parametrelerini doldurur (şablon/cümle değişmez)."""
    from ..generate.motion_gfx import catalog_for_llm, list_templates
    from .animator import MotionPick, MotionPlan, _model as anim_model, _system as anim_system

    tpls = list_templates()
    by_id = {s.id: s for s in sents}
    picks: list[MotionPick] = []
    for g in range(0, len(beats), 6):
        group = beats[g:g + 6]
        used = {b.template: tpls[b.template] for b in group if b.template in tpls}
        items = "\n".join(
            f"{i + 1}. şablon={b.template}; sentence_id={b.sentence_id}; phrase=\"{b.phrase}\"; "
            f"senaristin fikri: {b.idea}\n   cümle: {by_id[b.sentence_id].text if b.sentence_id in by_id else ''}"
            for i, b in enumerate(group))
        rep("plan", 0.62, f"Animasyon ekibi şablonları dolduruyor ({len(group)})")
        try:
            plan = chat_json(
                [{"role": "system", "content": anim_system()},
                 {"role": "user", "content": (
                     f"Şablonlar:\n{catalog_for_llm(used)}\n\nSenaristin istediği animasyonlar:\n{items}\n\n"
                     f"Her madde için TAM OLARAK bir animasyon yaz (aynı sırayla, {len(group)} öğe). template, "
                     "sentence_id ve phrase'i DEĞİŞTİRME; yalnızca params alanını şablon şemasına göre doldur.")}],
                MotionPlan, model=anim_model(), max_tokens=320 * len(group) + 200,
            )
        except Exception:
            continue
        for b, p in zip(group, plan.motions):
            p.template, p.sentence_id = b.template, b.sentence_id
            p.phrase = p.phrase or b.phrase
            picks.append(p)
    return picks


_ANIM_MOMENT = re.compile(
    r"\d|yüzde|kat |önce|sonra|ardından|adım|aşama|birinci|ikinci|üçüncü|dikkat|tehlike|risk|önemli|"
    r"yani|demek ki|aslında|sorun|çözüm|neden|nasıl|fark|karşı|milyon|milyar|bin ", re.I)


def motion_target(sents, duration: float, vertical: bool) -> int:
    """Videoya kaç animasyon yakışır: süreye göre bir taban + konuşmadaki 'görselleştirilebilir an' sayısı
    (sayı, süreç, uyarı, karşılaştırma, açıklama). Üst sınır: dikeyde ~8 sn'de, yatayda ~15 sn'de bir."""
    base = round(duration / (15 if vertical else 30))
    moments = sum(1 for x in sents if _ANIM_MOMENT.search(" " + x.text + " "))
    cap = max(2, int(duration / (8 if vertical else 15)))
    return max(2, min(cap, max(base, moments // 2)))


def run_director(project: Project, style: str | None = None, progress: Progress | None = None,
                 assets: bool = True, review: bool = True) -> tuple[Timeline, dict]:
    """Ölçüm araçları -> video profili -> senarist (brif + sahne planı) -> uygulayıcı ajanlar -> varlıklar."""
    from ..analyze.profile import build_profile, profile_text
    from .animator import place_picks
    from .screenwriter import EditScript, balance, format_script, system_prompt, write_beats, write_brief

    rep = progress or (lambda *a: None)
    cfg = get_config()
    if not has_model(cfg.models.llm):
        raise LLMError(f"LLM modeli Ollama'da yok: {cfg.models.llm}")
    style = (style or "").strip() or default_style()
    preset = load_preset(project.info.preset)
    tl = project.timeline()
    tr = project.transcript()
    report: dict = {"style": style}

    # 1) tekrar çekim analisti (aday + onay; her çalışmada)
    sents = kept_sentences(tr, tl.time_map())
    if preset.cleanup.remove_retakes:
        rep("plan", 0.03, "Tekrar çekim analisti çalışıyor")
        found = detect_retakes_llm(tl, tr, sents, None)
        project.save_transcript(tr)
        project.save_analysis("retakes_llm", found)
        report["retakes"] = found
        sents = kept_sentences(tr, tl.time_map())
    if not sents:
        raise ValueError("Transkriptte kurgulanacak cümle yok.")

    # 1b) Shorts gibi süre sınırlı formatlar: temizlenmiş video sınırı aşıyorsa en iyi bölüm seçilir
    if preset.max_duration and tl.output_duration() > preset.max_duration + 1:
        rep("plan", 0.05, f"En iyi {preset.max_duration:.0f} saniyelik bölüm seçiliyor")
        before = tl.output_duration()
        try:
            from .planner import _apply_highlight

            sents = _apply_highlight(tl, tr, sents, preset)
        except Exception as e:  # LLM yoksa: sığan ilk bölüm (cümle sınırında)
            report["highlight_error"] = str(e)[:200]
            sents = _fallback_highlight(tl, tr, sents, preset.max_duration)
        report["highlight"] = {"from_s": round(before, 1), "to_s": round(tl.output_duration(), 1),
                               "sentences": [sents[0].id, sents[-1].id] if sents else []}
    transcript_text = format_sentences(sents)
    if len(transcript_text) > 9000:
        transcript_text = transcript_text[:9000] + "\n..."

    # 2) analiz araçları: video profili ("nasıl bir video?")
    rep("plan", 0.06, "Analiz araçları videoyu inceliyor")
    try:
        prof = build_profile(project, rep)
    except Exception as e:  # profil opsiyonel: senarist transkriptle de çalışır
        prof = {"error": str(e)[:200]}
    report["profile"] = prof.get("llm") or {}
    ptxt = profile_text(prof) if "speech" in prof else "(profil yok)"

    # 3) senarist: brif (aynı stil + aynı istem için önbellekten)
    sw_system = system_prompt()
    saved = project.load_analysis("senaryo_brief") or {}
    vertical = preset.output.height > preset.output.width
    fmt = f"{preset.output.width}x{preset.output.height} ({'dikey' if vertical else 'yatay'}), yaklaşık {tl.output_duration():.0f} sn"
    from .screenwriter import ScriptBrief

    from .screenwriter import _model as _sw_model

    if saved.get("_style") == style and saved.get("_system") == sw_system and saved.get("_model") == _sw_model():
        rep("plan", 0.12, "Senaristin bu stil için yazdığı brif kullanılıyor")
        brief = ScriptBrief.model_validate({k: v for k, v in saved.items() if not k.startswith("_")})
    else:
        brief = write_brief(style, ptxt, transcript_text, fmt, rep)
    # Tema kullanıcının kararıdır: stil talimatında açıkça istenmedikçe varsayılan tema (agents.yaml
    # default_theme, yoksa apple) kullanılır; küçük modelin rastgele "bold/neon" seçimi uygulanmaz.
    brief.caption_theme = _theme_from_style(style)
    brief.hook = _shorten_hook(brief.hook)
    brief.logline = _clean_leaks(brief.logline)
    brief.visual_style = _ensure_english(_valid_visual_style(brief.visual_style, (prof.get("llm") or {}).get("kind")))
    brief.avoid = _ensure_english(_clean_leaks(brief.avoid)) or "text, letters, logos, watermark"
    project.save_analysis("senaryo_brief", {**brief.model_dump(), "_style": style, "_system": sw_system, "_model": _sw_model()})
    report["brief"] = brief.model_dump()
    scene = prof.get("scene") or {}
    if brief.zoom_intensity == "yok" and (scene.get("people", 1) >= 1 or scene.get("video_type") in
                                          ("talking_head", "vlog", "tutorial")):
        brief.zoom_intensity = "hafif"  # konuşmacı görünüyorsa kesimler zoom'suz sert atlar
    tp = tuned_preset(preset, brief)
    start_guard = tp.hook.duration if tp.hook.enabled else 1.5

    # 4) senarist: sahne planı (her zaman dilimi için bir karar)
    pace = {"sakin": 1.25, "orta": 1.0, "hızlı": 0.85}[brief.pacing]
    slots = make_slots(sents, (6.5 if vertical else 10.0) * pace, start_guard)[:40]
    beats = balance(write_beats(brief, slots, rep), slots, brief) if slots else []
    script = EditScript(brief=brief, beats=beats, profile=prof.get("llm") or {})
    project.save_analysis("senaryo", script.model_dump())
    report["script"] = format_script(script)

    team_notes = (
        f"Senaristin brifi:\n- Öz: {brief.logline}\n- Ton: {brief.tone}\n- Tempo: {brief.pacing}\n- Notlar: {brief.notes}\n"
        f"- Görsel dil (İngilizce, her görsele uygula): {brief.visual_style}\n- Kaçın: {brief.avoid}"
    )
    by_id = {s.id: s for s in sents}

    # 5a) b-roll ekibi: senaristin fikirlerini görsel prompt'a çevirir
    art = ArtPlan()
    broll_beats = [b for b in beats if b.visual == "b-roll"]
    if broll_beats and tp.broll.enabled:
        for g in range(0, len(broll_beats), 8):
            group = broll_beats[g:g + 8]
            items = "\n".join(
                f"{i + 1}. sentence_id={b.sentence_id}; phrase=\"{b.phrase}\"; senaristin fikri: {b.idea}\n"
                f"   cümle: {by_id[b.sentence_id].text if b.sentence_id in by_id else ''}" for i, b in enumerate(group))
            rep("plan", 0.5, f"B-roll ekibi görselleri tasarlıyor ({len(group)})")
            try:
                part = chat_json(
                    [{"role": "system", "content": _system("art_director") + "\n\n" + team_notes},
                     {"role": "user", "content": (
                         f"Senaristin istediği ara görüntüler:\n{items}\n\nHer madde için TAM OLARAK bir b-roll yaz "
                         f"(aynı sırayla, {len(group)} öğe). sentence_id ve phrase'i değiştirme; visual İngilizce, "
                         "somut, tek cümle.")}],
                    ArtPlan, model=_model("art_director", cfg.models.llm),
                    think=bool(_agent("art_director").get("think", False)),
                )
            except Exception as e:
                report.setdefault("errors", []).append(f"b-roll: {str(e)[:160]}")
                continue
            for b, pb in zip(group, part.brolls):
                pb.sentence_id = b.sentence_id
                pb.phrase = pb.phrase or b.phrase
                if brief.visual_style.lower()[:30] not in pb.visual.lower():
                    pb.visual = f"{pb.visual.rstrip('. ')}, {brief.visual_style}"[:400]
                art.brolls.append(pb)

    # 5b) yazı ekibi ve zoom: doğrudan senaryodan
    texts = [PlanText(sentence_id=b.sentence_id, phrase=b.phrase, text=b.idea) for b in beats
             if b.visual == "yazı" and len(b.idea.split()) <= 4]
    zooms = [PlanZoom(sentence_id=b.sentence_id, phrase=b.phrase) for b in beats
             if b.visual == "konuşmacı" and b.purpose in ("hook", "merak", "vurgu", "uyarı")]
    plans = [ChunkPlan(summary=brief.description, brolls=art.brolls, texts=texts, zooms=zooms)]
    project.save_analysis("plan_raw", {"plans": [p.model_dump() for p in plans], "brief": brief.model_dump()})

    meta = GlobalMeta(title=brief.title, hook=brief.hook, description=brief.description,
                      hashtags=brief.hashtags, music_mood=brief.music_mood)
    text_gap = {"az": 12.0, "orta": 7.0, "çok": 4.0}[brief.text_density]
    tl2 = assemble(tl, tr, tp, sents, plans, meta, brief.description, text_gap=text_gap)
    tl2.captions = tp.captions.model_copy()
    tl2.theme = brief.caption_theme
    tl2.cut_punch = tp.zoom.cut_punch if brief.zoom_intensity != "yok" else 0.0

    # 5c) animasyon ekibi: senaristin seçtiği şablonları doldurur ve yerleştirir
    tl2.motions = []
    anim_beats = [b for b in beats if b.visual == "animasyon"]
    if anim_beats:
        try:
            from ..generate.motion_gfx import list_templates as _lt

            picks = _fill_animation_params(anim_beats, sents, rep)
            added = place_picks(tl2, tr, sents, picks, _lt(), start_guard, report)
            report["motions"] = [f"{m.id}:{m.template}" for m in added]
        except Exception as e:  # animasyon opsiyonel; kurgu yine de tamamlansın
            report["motions_error"] = str(e)[:300]
    # güvenlik ağı: senaryodan yeterli animasyon çıkmadıysa animasyon tasarımcısı boş dilimlere kendisi seçer
    target = motion_target(sents, tl2.output_duration(), vertical)
    report["motion_target"] = target
    if len(tl2.motions) < target:
        try:
            from .animator import plan_motions

            extra = plan_motions(project, tl2, tr, rep, every=max(10.0, tl2.output_duration() / (target + 1)),
                                 notes=team_notes, report=report)
            report["motions_extra"] = [f"{m.id}:{m.template}" for m in extra]
        except Exception as e:
            report["motions_extra_error"] = str(e)[:300]

    # 5d) greenscreen / etkileşim çağrıları (konuşmada "abone olun", "beğenin" geçince)
    try:
        from ..generate.greenscreen import auto_place

        report["greenscreen"] = [f"{m.id}:{m.template}:{m.params.get('variant', '')}" for m in auto_place(tl2, tr)]
    except Exception as e:
        report["greenscreen_error"] = str(e)[:200]

    # 5e) besteci: profile ve brife göre fon müziği
    if assets and brief.music_mood != "none":
        try:
            from ..generate.music import score_timeline

            report["music"] = score_timeline(project, tl2, brief, prof.get("llm") or {}, rep)
        except Exception as e:
            report["music_error"] = str(e)[:300]

    # 5f) kartlar konuşmacının yüzünü kapatmasın
    try:
        from ..generate.motion_gfx import place_cards_off_face

        report["card_placement"] = place_cards_off_face(project, tl2)
    except Exception as e:
        report["card_placement_error"] = str(e)[:200]

    project.save_timeline(tl2, note=f"Senaryo: {brief.logline[:60]} · {len(tl2.brolls)} b-roll, "
                                   f"{len(tl2.motions)} animasyon, {len(tl2.texts)} yazı, {len(tl2.zooms)} zoom")
    if assets and tl2.motions:
        from ..generate.motion_gfx import fill_motions

        report["motion_render"] = fill_motions(project, tl2, rep)

    # 6) varlıklar + 7) görsel denetçi
    if assets and tl2.brolls:
        from ..generate.router import fill_brolls

        fr = fill_brolls(project, tl2, rep)
        report["assets"] = {"filled": fr.filled, "failed": fr.failed}
        if review and fr.filled:
            from .reviewer import review_brolls

            try:
                report["review"] = review_brolls(
                    project, tl2, rep,
                    style_hint=f"{_system('art_director')}\nGörsel dil: {brief.visual_style}\nKaçın: {brief.avoid}",
                )
            except Exception as e:  # denetim opsiyonel
                report["review"] = {"error": str(e)}
    project.save_timeline(tl2, note=f"Senaryo uygulandı: {len(tl2.brolls)} b-roll, {len(tl2.motions)} animasyon"
                                   + (", müzik" if tl2.music else ""))
    rep("plan", 1.0, f"{len(beats)} sahne · {len(tl2.brolls)} b-roll · {len(tl2.motions)} animasyon")
    # neyin neden reddedildiği/kurtarıldığı sonradan incelenebilsin
    try:
        project.save_analysis("director_report", json.loads(json.dumps(report, ensure_ascii=False, default=str)))
    except Exception:
        pass
    return tl2, report
