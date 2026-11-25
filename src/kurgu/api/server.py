"""Web API (FastAPI). Çalıştırma: kurgu serve  (veya uvicorn kurgu.api.server:app)"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import pipeline
from ..core.config import get_config, list_presets, load_preset, model_registry, project_root
from ..core.project_store import Project
from ..core.timeline import CutReason, Timeline
from .jobs import jobs

app = FastAPI(title="AI Kurgu API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_html(request, call_next):
    """index.html önbelleğe alınmasın: yeni derleme her zaman yüklensin (asset'ler hash'li)."""
    resp = await call_next(request)
    path = request.url.path
    if not path.startswith(("/api", "/assets")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def _project(pid: str) -> Project:
    try:
        return Project.open(pid)
    except FileNotFoundError:
        raise HTTPException(404, "Proje bulunamadı")


# --------------------------------------------------------------------------- görünüm


def project_view(p: Project) -> dict[str, Any]:
    """Arayüzün ihtiyaç duyduğu her şey: timeline, zaman eşleme, transkript, öğelerin konumları."""
    data: dict[str, Any] = {"info": p.info.model_dump(), "history": p.history(),
                            "jobs": [j.public() for j in jobs.active_for(p.id)]}
    source = p.load_analysis("source")
    data["source"] = source
    data["has_proxy"] = p.proxy.exists()
    data["renders"] = sorted(
        ({"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
         for f in p.renders_dir.glob("*.mp4")), key=lambda r: -r["mtime"])
    if not p.has_timeline():
        return data
    tl = p.timeline()
    tm = tl.time_map()
    data["timeline"] = tl.model_dump(mode="json")
    data["keep"] = [[a, b] for a, b, _ in tm.spans]
    data["output_duration"] = tm.duration
    try:
        tr = p.transcript()
    except FileNotFoundError:
        tr = None
    words = tr.word_map() if tr else {}

    def span(anchor):
        try:
            a, b = anchor.resolve(words)
        except ValueError:
            return None
        r = tm.range_to_out(a, b)
        return {"src": [a, b], "out": list(r) if r else None}

    data["items"] = {
        "brolls": [{**b.model_dump(mode="json"), "span": span(b.anchor)} for b in tl.brolls],
        "texts": [{**t.model_dump(mode="json"), "span": span(t.anchor)} for t in tl.texts],
        "zooms": [{**z.model_dump(mode="json"), "span": span(z.anchor)} for z in tl.zooms],
        "voiceovers": [{**v.model_dump(mode="json"), "span": span(v.anchor)} for v in tl.voiceovers],
        "motions": [],
    }
    from ..generate.motion_gfx import list_templates, motion_span

    tpls = list_templates()
    for m in tl.motions:
        sp = motion_span(m, tl, words, tm)
        t = tpls.get(m.template)
        data["items"]["motions"].append({
            **m.model_dump(mode="json"), "span": {"src": None, "out": list(sp) if sp else None},
            "name": t.name if t else m.template, "hide_captions": bool(t and t.spec.get("hide_captions")),
        })
    if tr:
        out_words = []
        for w in tr.words:
            o = tm.to_out((w.start + w.end) / 2, "strict")
            out_words.append({"id": w.id, "text": w.text, "start": w.start, "end": w.end, "cut": o is None,
                              "filler": w.is_filler, "stutter": w.is_stutter, "retake": w.is_retake})
        data["transcript"] = {
            "language": tr.language,
            "words": out_words,
            "sentences": [{"id": s.id, "word_ids": s.word_ids} for s in tr.sentences],
        }
    data["report"] = pipeline.cut_report(tl)
    return data


# --------------------------------------------------------------------------- genel


@app.get("/api/health")
def health():
    from ..core import ffmpeg as ff
    from ..agent.llm import available_models

    cfg = get_config()
    out: dict[str, Any] = {"commercial_mode": cfg.commercial_mode, "presets": list_presets()}
    try:
        out["ffmpeg"] = ff.version()
        out["encoder"] = ff.pick_encoder(cfg.render.encoder)
    except ff.FFmpegError as e:
        out["ffmpeg_error"] = str(e)
    names = available_models()
    out["ollama"] = bool(names)
    out["models"] = {k: {"name": getattr(cfg.models, k),
                         "license": (model_registry().get(getattr(cfg.models, k)) or None) and
                         model_registry()[getattr(cfg.models, k)].license}
                     for k in ("asr", "llm", "vlm", "image", "video", "tts")}
    out["models"]["llm"]["ready"] = cfg.models.llm in names or f"{cfg.models.llm}:latest" in names
    out["models"]["vlm"]["ready"] = cfg.models.vlm in names or f"{cfg.models.vlm}:latest" in names
    return out


@app.get("/api/providers")
def providers():
    from ..generate.router import provider_status

    return provider_status()


@app.get("/api/presets")
def presets():
    return {name: load_preset(name).model_dump() for name in list_presets()}


@app.get("/api/projects")
def projects():
    return [i.model_dump() for i in Project.list_all()]


@app.post("/api/projects")
async def create_project(
    file: UploadFile = File(...),
    preset: str = Form("shorts"),
    name: str | None = Form(None),
    smart: bool = Form(True),
    instruction: str | None = Form(None),
    assets: bool = Form(True),
    auto_render: bool = Form(True),
):
    if preset not in list_presets():
        raise HTTPException(400, f"Geçersiz preset: {preset}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="kurgu_up_"))
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    tmp = tmp_dir / f"upload{suffix}"
    with open(tmp, "wb") as f:
        shutil.copyfileobj(file.file, f, length=1 << 20)
    project = pipeline.ingest(tmp, preset, name or Path(file.filename or "video").stem)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    def run(progress):
        pipeline.ensure_proxy(project, progress)
        stats = pipeline.analyze(project, progress)
        tl = pipeline.plan_cleanup(project)
        report = {}
        if smart:  # promptsuz tam otomatik: profil -> senarist -> ajanlar -> müzik -> son render
            tl, report = pipeline.auto(project, progress, instruction or None, assets=assets, render_final=auto_render)
        else:
            tl = pipeline.track_faces(project, tl, progress)
            if tl.reframe.keys:
                project.save_timeline(tl, note="Yüz takipli kırpma")
        return {"stats": stats, "report": report}

    job = jobs.submit("pipeline", run, project.id)
    return {"project": project.info.model_dump(), "job": job.public()}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return project_view(_project(pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    p = _project(pid)
    if jobs.active_for(p.id):
        raise HTTPException(409, "Projede çalışan iş var")
    shutil.rmtree(p.dir)
    return {"ok": True}


# --------------------------------------------------------------------------- medya


@app.get("/api/projects/{pid}/media/{kind}")
def media(pid: str, kind: str):
    p = _project(pid)
    path = {"source": p.source, "proxy": p.proxy}.get(kind)
    if path is None or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/projects/{pid}/renders/{name}")
def render_file(pid: str, name: str, download: bool = False):
    p = _project(pid)
    path = (p.renders_dir / name).resolve()
    if path.parent != p.renders_dir.resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=name if download else None)


@app.get("/api/projects/{pid}/assets/{rel:path}")
def asset_file(pid: str, rel: str):
    p = _project(pid)
    path = p.abs(rel)
    if p.assets_dir.resolve() not in path.parents or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# --------------------------------------------------------------------------- düzenleme


class WordCut(BaseModel):
    word_ids: list[int]
    restore: bool = False


@app.post("/api/projects/{pid}/words")
def cut_words(pid: str, body: WordCut):
    """Transkriptten kelime silmek = videodan kesmek."""
    p = _project(pid)
    tl = p.timeline()
    tr = p.transcript()
    ids = sorted(set(i for i in body.word_ids if 0 <= i < len(tr.words)))
    if not ids:
        raise HTTPException(400, "Kelime seçilmedi")
    groups: list[list[int]] = []
    for i in ids:
        if groups and i == groups[-1][-1] + 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    for g in groups:
        first, last = tr.words[g[0]], tr.words[g[-1]]
        prev_end = tr.words[g[0] - 1].end if g[0] > 0 else 0.0
        next_start = tr.words[g[-1] + 1].start if g[-1] + 1 < len(tr.words) else tl.source.duration
        a = max(prev_end, first.start - 0.04)
        b = min(next_start, last.end + 0.04)
        if body.restore:
            tl.restore(a, b)
        else:
            tl.add_cut(a, b, CutReason.manual, "transkript")
    note = f"Transkript: {len(ids)} kelime {'geri getirildi' if body.restore else 'kesildi'}"
    p.save_timeline(tl, note=note)
    return project_view(p)


@app.put("/api/projects/{pid}/timeline")
def put_timeline(pid: str, body: dict):
    p = _project(pid)
    note = body.pop("_note", "Arayüzden düzenleme")
    tl = Timeline.model_validate(body)
    p.save_timeline(tl, note=note)
    return project_view(p)


@app.post("/api/projects/{pid}/undo")
def undo(pid: str):
    p = _project(pid)
    p.undo()
    return project_view(p)


@app.post("/api/projects/{pid}/redo")
def redo(pid: str):
    p = _project(pid)
    p.redo()
    return project_view(p)


class RenderReq(BaseModel):
    preview: bool = True


@app.post("/api/projects/{pid}/render")
def start_render(pid: str, body: RenderReq):
    p = _project(pid)

    def run(progress):
        from ..render.render import render

        tl = p.timeline()
        missing = [b.id for b in tl.brolls if not b.asset]
        if missing:
            from ..generate.router import fill_brolls

            rep = fill_brolls(p, tl, progress, ids=missing)
            if rep.filled:  # hiçbiri üretilemediyse gereksiz sürüm açma
                p.save_timeline(tl, note=f"Eksik b-roll'lar üretildi: {', '.join(rep.filled)}")
        if any(not m.asset or m.theme != tl.theme for m in tl.motions):
            from ..generate.motion_gfx import fill_motions

            mr = fill_motions(p, tl, progress)
            if mr["filled"]:
                p.save_timeline(tl, note=f"Eksik animasyonlar üretildi: {', '.join(mr['filled'])}")
        res = render(p, preview=body.preview, progress=progress)
        return {"file": res.path.name, "duration": res.duration, "encoder": res.encoder, "seconds": res.seconds,
                "srt": res.srt.name if res.srt else None}

    return jobs.submit("render-preview" if body.preview else "render", run, p.id).public()


class PlanReq(BaseModel):
    instruction: str | None = None
    assets: bool = True


@app.post("/api/projects/{pid}/plan")
def start_plan(pid: str, body: PlanReq):
    p = _project(pid)

    def run(progress):
        tl, report = pipeline.smart(p, progress, body.instruction, assets=body.assets)
        return {"brolls": len(tl.brolls), "texts": len(tl.texts), "zooms": len(tl.zooms), "report": report}

    return jobs.submit("plan", run, p.id).public()


@app.get("/api/agents")
def get_agents():
    """Yönetmen/alt ajan sistem prompt'ları, modelleri ve stil şablonları."""
    from ..core.config import load_agents

    return load_agents()


@app.put("/api/agents")
def put_agents(body: dict):
    from ..core.config import load_agents, save_agents

    if not isinstance(body.get("agents"), dict):
        raise HTTPException(400, "'agents' alanı gerekli")
    cur = load_agents()
    cur.update({k: v for k, v in body.items() if k in ("agents", "default_style", "style_presets")})
    save_agents(cur)
    return cur


@app.get("/api/themes")
def themes():
    from ..render.caption_themes import theme_list

    return theme_list()


@app.get("/api/greenscreen")
def greenscreen_list():
    from ..generate.greenscreen import list_clips

    return [{"id": c.id, "name": c.name, "duration": round(c.duration, 2), "triggers": c.spec.get("triggers", []),
             "position": c.spec.get("position", "lower"), "license": c.spec.get("license")} for c in list_clips().values()]


@app.post("/api/greenscreen")
async def greenscreen_add(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    triggers: str = Form(""),
    position: str = Form("lower"),
    scale: float = Form(0.7),
):
    """Greenscreen klibi yükler; perde rengi otomatik bulunur ve silinir."""
    from ..generate.greenscreen import add_clip, keyed

    tmp_dir = Path(tempfile.mkdtemp(prefix="kurgu_gs_"))
    tmp = tmp_dir / Path(file.filename or "clip.mp4").name
    with open(tmp, "wb") as f:
        shutil.copyfileobj(file.file, f, length=1 << 20)
    c = add_clip(tmp, name or tmp.stem, [t.strip() for t in triggers.split(",") if t.strip()], position, scale)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    k = keyed(c)
    return {"id": c.id, "key_color": k["key_color"]}


class ThemeReq(BaseModel):
    name: str


@app.post("/api/projects/{pid}/theme")
def put_theme(pid: str, body: ThemeReq):
    """Temayı değiştirir (altyazı + animasyonlar) ve animasyonları yeni temayla arka planda yeniden üretir."""
    from ..render.caption_themes import set_theme

    p = _project(pid)
    tl = p.timeline()
    try:
        set_theme(tl, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    p.save_timeline(tl, note=f"Tema: {body.name}")
    job = None
    if tl.motions:
        def run(progress):
            from ..generate.motion_gfx import fill_motions

            t2 = p.timeline()
            rep = fill_motions(p, t2, progress)
            if rep["filled"]:
                p.save_timeline(t2, note=f"Animasyonlar {body.name} temasıyla yeniden üretildi")
            return rep

        job = jobs.submit("motion-render", run, p.id).public()
    return {**project_view(p), "job": job}


@app.get("/api/motion/templates")
def motion_templates():
    """Şablon kütüphanesi: kimlik, ad, açıklama, yerleşim, süre, parametre şeması, örnek."""
    from ..generate.motion_gfx import list_templates

    return [{"id": t.id, "name": t.name, "description": t.spec.get("description", ""), "layout": t.layout,
             "duration": t.spec.get("duration", {}), "params": t.spec.get("params", {}),
             "example": t.spec.get("example", {})} for t in list_templates().values()]


@app.post("/api/motion/templates/{tid}/preview")
def motion_template_preview(tid: str, body: dict | None = None):
    """Şablonu (verilen ya da örnek parametrelerle) önizleme için render eder; webm yolu döner."""
    from ..generate.motion_gfx import list_templates, render as mg_render

    tpls = list_templates()
    if tid not in tpls:
        raise HTTPException(404, "Şablon yok")
    t = tpls[tid]
    out = project_root() / ".cache" / "motion_preview"

    def run(progress):
        progress("assets", 0.1, f"{t.name} render ediliyor")
        r = mg_render(tid, (body or {}).get("params") or t.spec.get("example", {}), t.duration_bounds()[0], out, 720, 1280)
        return {"webm": r["webm"].name if r.get("webm") else None}

    return jobs.submit("motion-preview", run, None).public()


@app.get("/api/motion/preview/{name}")
def motion_preview_file(name: str):
    path = (project_root() / ".cache" / "motion_preview" / name).resolve()
    if path.suffix != ".webm" or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/webm")


@app.post("/api/projects/{pid}/animate")
def start_animate(pid: str):
    """Animasyon tasarımcısı: mevcut kurguya hareketli grafikler ekler ve render eder."""
    p = _project(pid)

    def run(progress):
        from ..agent.animator import animate

        tl, report = animate(p, progress)
        return {"motions": len(tl.motions), "report": report}

    return jobs.submit("animate", run, p.id).public()


@app.post("/api/projects/{pid}/motions/render")
def render_motions(pid: str):
    """Timeline'daki (yeni eklenmiş ya da parametresi değişmiş) animasyonları render eder."""
    p = _project(pid)

    def run(progress):
        from ..generate.motion_gfx import fill_motions

        tl = p.timeline()
        rep = fill_motions(p, tl, progress)
        if rep["filled"]:
            p.save_timeline(tl, note=f"Animasyon render: {', '.join(rep['filled'])}")
        return rep

    return jobs.submit("motion-render", run, p.id).public()


class DirectReq(BaseModel):
    style: str | None = None
    assets: bool = True
    review: bool = True


@app.post("/api/projects/{pid}/direct")
def start_direct(pid: str, body: DirectReq):
    """Yönetmen + alt ajanlarla yeniden kurgula."""
    p = _project(pid)

    def run(progress):
        from ..agent.director import run_director

        tl, report = run_director(p, body.style, progress, assets=body.assets, review=body.review)
        return {"brolls": len(tl.brolls), "texts": len(tl.texts), "zooms": len(tl.zooms), "report": report}

    return jobs.submit("direct", run, p.id).public()


@app.get("/api/projects/{pid}/brief")
def get_brief(pid: str):
    p = _project(pid)
    return p.load_analysis("senaryo_brief") or p.load_analysis("director_brief") or {}


@app.get("/api/projects/{pid}/senaryo")
def get_script(pid: str):
    """Senaristin kurgu senaryosu (brif + sahne planı) ve analiz araçlarının video profili."""
    p = _project(pid)
    prof = p.load_analysis("profile") or {}
    return {"script": p.load_analysis("senaryo"), "profile": {k: v for k, v in prof.items() if k != "scene"},
            "scene": prof.get("scene")}


class ChatReq(BaseModel):
    message: str
    history: list[dict] = []


@app.post("/api/projects/{pid}/chat")
def start_chat(pid: str, body: ChatReq):
    p = _project(pid)

    def run(progress):
        from ..agent.chat_agent import chat

        res = chat(p, body.message, body.history, progress)
        return {"reply": res.reply, "actions": res.actions, "version": res.version,
                "broll_report": res.broll_report, "history": res.history}

    return jobs.submit("chat", run, p.id).public()


class RegenReq(BaseModel):
    prompt: str | None = None


@app.post("/api/projects/{pid}/brolls/{bid}/regenerate")
def regenerate_broll(pid: str, bid: str, body: RegenReq):
    p = _project(pid)

    def run(progress):
        from ..generate.router import fill_brolls

        tl = p.timeline()
        br = next((b for b in tl.brolls if b.id == bid), None)
        if not br:
            raise ValueError("B-roll bulunamadı")
        if body.prompt:
            br.prompt = body.prompt
        br.asset = None
        rep = fill_brolls(p, tl, progress, ids=[bid], only_missing=False)
        p.save_timeline(tl, note=f"{bid} yeniden üretildi")
        return {"filled": rep.filled, "failed": rep.failed}

    return jobs.submit("broll", run, p.id).public()


# --------------------------------------------------------------------------- işler


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404)
    return j.public()


@app.get("/api/jobs/{jid}/events")
async def job_events(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404)

    async def stream():
        i = 0
        while True:
            evs = await asyncio.to_thread(jobs.wait_events, j, i, 10.0)
            for ev in evs:
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
            i += len(evs)
            if j.status in ("done", "failed") and i >= len(j.events):
                break
            if not evs:
                yield ": ping\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- arayüz (derlenmiş)

# Masaüstü (Electron) kurulumunda arayüz dosyaları uygulama kaynaklarındadır: KURGU_WEB_DIST
_dist = Path(os.environ.get("KURGU_WEB_DIST") or project_root() / "web" / "dist")
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")
