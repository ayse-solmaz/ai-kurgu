"""Ajan araçları: timeline üzerinde güvenli, tipli düzenleme işlemleri.

Kullanıcı zamanları ÇIKTI videosuna göre söyler ("ilk 5 saniye", "0:20'de"). Araçlar bu
zamanları kaynak zamana ve kelime ID'lerine çevirir; böylece sonraki kesimlerde öğeler kaymaz.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from ..core.project_store import Project
from ..core.timeline import (
    Anchor,
    BRoll,
    CutReason,
    Music,
    TextOverlay,
    Timeline,
    Transcript,
    Zoom,
)


@dataclass
class ToolContext:
    project: Project
    tl: Timeline
    tr: Transcript | None
    actions: list[str] = field(default_factory=list)
    changed: bool = False
    broll_dirty: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- zaman yardımcıları

    def out_to_anchor(self, a: float, b: float) -> Anchor:
        tm = self.tl.time_map()
        sa, sb = tm.to_src(max(a, 0.0)), tm.to_src(max(b, a + 0.1))
        if self.tr and self.tr.words:
            inside = [w for w in self.tr.words if w.end > sa and w.start < sb
                      and tm.to_out((w.start + w.end) / 2, "strict") is not None]
            if inside:
                return Anchor(word_start=inside[0].id, word_end=inside[-1].id, start=sa, end=sb)
        return Anchor(start=sa, end=sb)

    def out_span(self, anchor: Anchor) -> tuple[float, float] | None:
        words = self.tr.word_map() if self.tr else {}
        try:
            a, b = anchor.resolve(words)
        except ValueError:
            return None
        return self.tl.time_map().range_to_out(a, b)

    def log(self, text: str):
        self.actions.append(text)
        self.changed = True


# --------------------------------------------------------------------------- argüman şemaları


class NoArgs(BaseModel):
    pass


class FindText(BaseModel):
    query: str = Field(description="Transkriptte aranacak kelime veya ifade")


class Range(BaseModel):
    start: float = Field(description="Çıktı videosunda başlangıç saniyesi")
    end: float = Field(description="Çıktı videosunda bitiş saniyesi")


class SentenceRef(BaseModel):
    sentence_id: int


class AddBRoll(BaseModel):
    at: float = Field(description="Çıktı videosunda başlangıç saniyesi")
    duration: float = Field(default=3.0, description="Saniye (1.5-6)")
    visual: str = Field(description="İngilizce, somut görsel sahne tarifi (görsel üretim prompt'u)")
    keywords: list[str] = Field(default_factory=list, description="İngilizce stok arama kelimeleri")
    mode: Literal["full", "pip"] = "full"


class ItemRef(BaseModel):
    id: str = Field(description="Öğe kimliği, ör. b1, t2, z3")


class RegenBRoll(BaseModel):
    id: str
    visual: str | None = Field(default=None, description="Yeni İngilizce görsel tarifi (boşsa aynı)")


class AddText(BaseModel):
    at: float
    duration: float = 2.0
    text: str = Field(description="Ekranda görünecek kısa yazı")
    style: Literal["pop", "lower_third", "title", "hook"] = "pop"
    position: Literal["top", "center", "bottom"] = "top"


class AddZoom(BaseModel):
    at: float
    duration: float = 1.5
    scale: float = Field(default=1.15, description="1.05-1.4 arası yakınlaştırma")


class SetCaptions(BaseModel):
    enabled: bool | None = None
    style: Literal["karaoke", "classic", "pop"] | None = None
    position: Literal["bottom", "center", "top"] | None = None
    uppercase: bool | None = None
    size: int | None = Field(default=None, description="Piksel cinsinden yazı boyutu")
    highlight: str | None = Field(default=None, description="Vurgu rengi #RRGGBB")
    primary: str | None = Field(default=None, description="Yazı rengi #RRGGBB")
    font: str | None = None
    theme: Literal["apple", "bold", "neon", "minimal"] | None = Field(
        default=None, description="Hazır tema; font, renk ve vurgu biçimini birlikte değiştirir (önce uygulanır)")


class SetMeta(BaseModel):
    title: str | None = None
    hook: str | None = None
    description: str | None = None


class SetMusic(BaseModel):
    enabled: bool = True
    mood: Literal["calm", "upbeat", "inspiring", "dramatic", "corporate"] | None = None
    volume_db: float | None = Field(default=None, description="-35 ile -12 arası")


class AddVoiceover(BaseModel):
    at: float = Field(description="Çıktı videosunda başlangıç saniyesi")
    text: str = Field(description="Seslendirilecek Türkçe metin")


# --------------------------------------------------------------------------- araçlar


def t_overview(ctx: ToolContext, _: NoArgs) -> dict:
    tl = ctx.tl
    out = {
        "preset": tl.preset,
        "output_duration": round(tl.output_duration(), 2),
        "source_duration": round(tl.source.duration, 2),
        "cuts": len(tl.cuts),
        "captions": tl.captions.model_dump(include={"enabled", "theme", "style", "position", "uppercase", "size", "highlight"}),
        "meta": {"title": tl.meta.title, "hook": tl.meta.hook},
        "music": tl.music.model_dump() if tl.music else None,
        "brolls": [], "texts": [], "zooms": [],
    }
    for b in tl.brolls:
        s = ctx.out_span(b.anchor)
        out["brolls"].append({"id": b.id, "at": s and round(s[0], 1), "end": s and round(s[1], 1),
                              "visual": b.prompt, "ready": bool(b.asset)})
    for t in tl.texts:
        s = ctx.out_span(t.anchor)
        out["texts"].append({"id": t.id, "at": s and round(s[0], 1), "text": t.text, "style": t.style})
    for z in tl.zooms:
        s = ctx.out_span(z.anchor)
        out["zooms"].append({"id": z.id, "at": s and round(s[0], 1)})
    return out


def t_find(ctx: ToolContext, a: FindText) -> list[dict]:
    from ..analyze.fillers import normalize

    if not ctx.tr:
        return []
    q = normalize(a.query)
    tm = ctx.tl.time_map()
    res = []
    for s in ctx.tr.sentences:
        text = " ".join(ctx.tr.words[i].text for i in s.word_ids)
        if q and q in normalize(text.replace(" ", "")) or a.query.lower() in text.lower():
            o = tm.to_out(s.start, "next")
            res.append({"sentence_id": s.id, "at": None if o is None else round(o, 2),
                        "visible": tm.to_out((s.start + s.end) / 2, "strict") is not None, "text": text})
    return res[:10]


def t_cut(ctx: ToolContext, a: Range) -> str:
    tm = ctx.tl.time_map()
    if a.end <= a.start:
        return "Hata: bitiş başlangıçtan büyük olmalı"
    sa, sb = tm.to_src(a.start), tm.to_src(min(a.end, tm.duration))
    # kesilecek aralık birden çok tutulan parçaya yayılıyorsa her parçayı ayrı kes
    for src_a, src_b, out_a in list(tm.spans):
        out_b = out_a + (src_b - src_a)
        lo, hi = max(a.start, out_a), min(a.end, out_b)
        if hi > lo:
            ctx.tl.add_cut(src_a + (lo - out_a), src_a + (hi - out_a), CutReason.manual, "ajan")
    ctx.log(f"{a.start:.1f}-{a.end:.1f} sn kesildi")
    return f"Kesildi. Yeni süre: {ctx.tl.output_duration():.1f} sn (kaynak {sa:.1f}-{sb:.1f})"


def t_cut_sentence(ctx: ToolContext, a: SentenceRef) -> str:
    if not ctx.tr or a.sentence_id >= len(ctx.tr.sentences):
        return "Hata: cümle bulunamadı"
    s = ctx.tr.sentences[a.sentence_id]
    ctx.tl.add_cut(s.start - 0.05, s.end + 0.05, CutReason.manual, "ajan: cümle")
    ctx.log(f"cümle {a.sentence_id} kesildi: {s.text[:40]}")
    return f"Kesildi: {s.text}"


def t_restore_sentence(ctx: ToolContext, a: SentenceRef) -> str:
    if not ctx.tr or a.sentence_id >= len(ctx.tr.sentences):
        return "Hata: cümle bulunamadı"
    s = ctx.tr.sentences[a.sentence_id]
    ctx.tl.restore(s.start - 0.05, s.end + 0.05)
    ctx.log(f"cümle {a.sentence_id} geri getirildi")
    return f"Geri getirildi: {s.text}"


def t_add_broll(ctx: ToolContext, a: AddBRoll) -> str:
    d = min(max(a.duration, 1.0), 8.0)
    br = BRoll(id=ctx.tl.next_id("b"), anchor=ctx.out_to_anchor(a.at, a.at + d), prompt=a.visual,
               keywords=a.keywords[:3], mode=a.mode)
    ctx.tl.brolls.append(br)
    ctx.broll_dirty.append(br.id)
    ctx.log(f"b-roll {br.id} eklendi ({a.at:.1f} sn): {a.visual[:40]}")
    return f"{br.id} eklendi; render öncesi görsel üretilecek"


def _remove(ctx: ToolContext, kind: str, item_id: str) -> str:
    lst = getattr(ctx.tl, kind)
    before = len(lst)
    setattr(ctx.tl, kind, [x for x in lst if x.id != item_id])
    if len(getattr(ctx.tl, kind)) == before:
        return f"Hata: {item_id} bulunamadı"
    ctx.log(f"{item_id} silindi")
    return f"{item_id} silindi"


def t_remove_broll(ctx: ToolContext, a: ItemRef) -> str:
    return _remove(ctx, "brolls", a.id)


def t_regen_broll(ctx: ToolContext, a: RegenBRoll) -> str:
    for b in ctx.tl.brolls:
        if b.id == a.id:
            if a.visual:
                b.prompt = a.visual
            b.asset = None
            b.provider = None
            ctx.broll_dirty.append(b.id)
            ctx.log(f"{b.id} yeniden üretilecek")
            return f"{b.id} yeniden üretilecek: {b.prompt}"
    return f"Hata: {a.id} bulunamadı"


def t_add_text(ctx: ToolContext, a: AddText) -> str:
    t = TextOverlay(id=ctx.tl.next_id("t"), anchor=ctx.out_to_anchor(a.at, a.at + max(a.duration, 0.6)),
                    text=a.text, style=a.style, position=a.position)
    ctx.tl.texts.append(t)
    ctx.log(f"yazı {t.id} eklendi: {a.text}")
    return f"{t.id} eklendi"


def t_remove_text(ctx: ToolContext, a: ItemRef) -> str:
    return _remove(ctx, "texts", a.id)


def t_add_zoom(ctx: ToolContext, a: AddZoom) -> str:
    z = Zoom(id=ctx.tl.next_id("z"), anchor=ctx.out_to_anchor(a.at, a.at + max(a.duration, 0.4)),
             scale=min(max(a.scale, 1.02), 1.5))
    ctx.tl.zooms.append(z)
    ctx.log(f"zoom {z.id} eklendi ({a.at:.1f} sn)")
    return f"{z.id} eklendi"


def t_remove_zoom(ctx: ToolContext, a: ItemRef) -> str:
    return _remove(ctx, "zooms", a.id)


def t_set_captions(ctx: ToolContext, a: SetCaptions) -> str:
    upd = {k: v for k, v in a.model_dump().items() if v is not None}
    theme = upd.pop("theme", None)
    if theme:
        from ..render.caption_themes import apply_caption_theme

        ctx.tl.captions = apply_caption_theme(ctx.tl.captions, theme, ctx.tl.output.width, ctx.tl.output.height)
        ctx.tl.theme = theme
    ctx.tl.captions = ctx.tl.captions.model_copy(update=upd)
    if theme:
        upd["theme"] = theme
    ctx.log(f"altyazı ayarı: {upd}")
    return f"Altyazı güncellendi: {upd}"


def t_set_meta(ctx: ToolContext, a: SetMeta) -> str:
    upd = {k: v for k, v in a.model_dump().items() if v is not None}
    ctx.tl.meta = ctx.tl.meta.model_copy(update=upd)
    if "hook" in upd:
        for t in ctx.tl.texts:
            if t.style == "hook":
                t.text = upd["hook"]
    ctx.log(f"meta: {list(upd)}")
    return "Güncellendi"


def t_set_music(ctx: ToolContext, a: SetMusic) -> str:
    if not a.enabled:
        ctx.tl.music = None
        ctx.log("müzik kapatıldı")
        return "Müzik kapatıldı"
    m = ctx.tl.music or Music()
    if a.mood:
        m.mood = a.mood
        m.asset = None
    if a.volume_db is not None:
        m.volume_db = min(max(a.volume_db, -40), -8)
    ctx.tl.music = m
    ctx.log(f"müzik: {m.mood} {m.volume_db} dB")
    return "Müzik güncellendi"


def t_add_voiceover(ctx: ToolContext, a: AddVoiceover) -> str:
    from ..core.timeline import Voiceover

    vo = Voiceover(id=ctx.tl.next_id("v"), anchor=ctx.out_to_anchor(a.at, a.at + 1.0), text=a.text)
    ctx.tl.voiceovers.append(vo)
    ctx.log(f"dış ses {vo.id} eklendi")
    return f"{vo.id} eklendi; render öncesi seslendirilecek"


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[[ToolContext, Any], Any]

    def schema(self) -> dict:
        params = self.args.model_json_schema()
        params.pop("title", None)
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": params}}


TOOLS: list[Tool] = [
    Tool("get_overview", "Kurgunun mevcut durumunu getirir (süre, b-roll, yazı, zoom, altyazı).", NoArgs, t_overview),
    Tool("find_text", "Transkriptte ifade arar; cümle numarası ve çıktı saniyesini döndürür.", FindText, t_find),
    Tool("cut_range", "Çıktı videosunda verilen saniye aralığını keser.", Range, t_cut),
    Tool("cut_sentence", "Bir cümleyi tamamen keser.", SentenceRef, t_cut_sentence),
    Tool("restore_sentence", "Kesilmiş bir cümleyi geri getirir.", SentenceRef, t_restore_sentence),
    Tool("add_broll", "Belirtilen saniyeye üretilecek bir ara görüntü (b-roll) ekler.", AddBRoll, t_add_broll),
    Tool("remove_broll", "B-roll siler.", ItemRef, t_remove_broll),
    Tool("regenerate_broll", "B-roll görselini (isteğe bağlı yeni tarifle) yeniden üretir.", RegenBRoll, t_regen_broll),
    Tool("add_text", "Ekrana animasyonlu yazı ekler.", AddText, t_add_text),
    Tool("remove_text", "Ekran yazısını siler.", ItemRef, t_remove_text),
    Tool("add_zoom", "Belirtilen ana zoom (yakınlaştırma) ekler.", AddZoom, t_add_zoom),
    Tool("remove_zoom", "Zoom siler.", ItemRef, t_remove_zoom),
    Tool("set_captions", "Altyazı stilini değiştirir.", SetCaptions, t_set_captions),
    Tool("set_meta", "Başlık, hook veya açıklamayı değiştirir.", SetMeta, t_set_meta),
    Tool("set_music", "Arka plan müziğini açar/kapatır, ruh halini veya sesini ayarlar.", SetMusic, t_set_music),
    Tool("add_voiceover", "Yapay sesle dış ses (anlatıcı) ekler.", AddVoiceover, t_add_voiceover),
]
TOOL_MAP = {t.name: t for t in TOOLS}


def dispatch(ctx: ToolContext, name: str, arguments: dict | str | None) -> str:
    tool = TOOL_MAP.get(name)
    if not tool:
        return f"Hata: bilinmeyen araç {name}"
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
    try:
        args = tool.args.model_validate(arguments or {})
    except ValidationError as e:
        return f"Hata: geçersiz argümanlar: {e}"
    try:
        res = tool.fn(ctx, args)
    except Exception as e:  # araç hatasını modele bildir, döngü devam etsin
        return f"Hata: {e}"
    return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
