"""Uçtan uca iş akışı: içe al -> analiz et -> planla -> render et.

CLI ve web API aynı fonksiyonları kullanır.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .analyze.cutter import build_cleanup_cuts, summarize_cuts
from .analyze.fillers import analyze_disfluencies
from .core.config import load_preset
from .core.project_store import Project
from .core.timeline import SourceInfo, Timeline
from .ingest.probe import extract_audio, make_proxy, probe_source

Progress = Callable[[str, float, str], None]


def _noop(stage: str, frac: float, msg: str = ""):
    pass


def ingest(src: Path, preset: str, name: str | None = None, progress: Progress = _noop) -> Project:
    load_preset(preset)  # geçersiz preset'i erken yakala
    project = Project.create(Path(src), preset, name)
    progress("ingest", 0.0, "video inceleniyor")
    info = probe_source(project.source)
    project.save_analysis("source", info)
    if info.has_audio:
        extract_audio(project.source, project.audio, lambda f, m: progress("ingest", f * 0.9, "ses ayrıştırılıyor"),
                      info.duration)
    project.set_status("ingested")
    progress("ingest", 1.0, f"{info.width}x{info.height} {info.fps:.0f}fps {info.duration:.1f}s")
    return project


def source_info(project: Project) -> SourceInfo:
    data = project.load_analysis("source")
    if data is None:
        info = probe_source(project.source)
        project.save_analysis("source", info)
        return info
    return SourceInfo.model_validate(data)


def ensure_proxy(project: Project, progress: Progress = _noop) -> Path:
    if not project.proxy.exists():
        info = source_info(project)
        make_proxy(project.source, project.proxy, progress=lambda f, m: progress("proxy", f, ""),
                   duration=info.duration)
    return project.proxy


def analyze(project: Project, progress: Progress = _noop, force: bool = False) -> dict:
    """Transkript + VAD + dolgu/kekeleme/retake işaretleme."""
    from .analyze import asr, vad

    preset = load_preset(project.info.preset)
    info = source_info(project)
    stats: dict = {}
    if not info.has_audio:
        project.set_status("analyzed")
        return {"warning": "Videoda ses yok; konuşma analizi atlandı."}

    if force or not project.has_analysis("transcript"):
        progress("asr", 0.0, "konuşma yazıya çevriliyor (ilk çalıştırmada model indirilir)")
        tr = asr.transcribe(project.audio, progress=lambda f, m: progress("asr", f, m))
        project.save_transcript(tr)
    tr = project.transcript()

    if force or not project.has_analysis("vad"):
        progress("vad", 0.0, "konuşma bölgeleri bulunuyor")
        regions = vad.speech_regions(project.audio)
        project.save_analysis("vad", regions)
        progress("vad", 1.0, f"{len(regions)} konuşma bölgesi")

    tr.sentences = asr.build_sentences(tr.words)  # noktalama/duraklamaya göre yeniden kur
    c = preset.cleanup
    stats = analyze_disfluencies(tr, c.remove_fillers, c.remove_stutters, c.remove_retakes)
    project.save_transcript(tr)
    stats["words"] = len(tr.words)
    stats["language"] = tr.language
    project.set_status("analyzed")
    return stats


def plan_cleanup(project: Project, preset_name: str | None = None) -> Timeline:
    """Kurala dayalı temizleme planı (Faz 1). LLM planlayıcı bunun üzerine ekleme yapar."""
    preset = load_preset(preset_name or project.info.preset)
    info = source_info(project)
    tl = Timeline.from_preset(preset, info)
    if info.has_audio and project.has_analysis("transcript"):
        tr = project.transcript()
        vad_regions = [tuple(r) for r in project.load_analysis("vad", [])]
        tl.cuts = build_cleanup_cuts(tr, info.duration, preset.cleanup, vad_regions or None)
    project.save_timeline(tl, note="Otomatik temizlik: sessizlik, dolgu, kekeleme, tekrar çekim")
    project.set_status("planned")
    return tl


def track_faces(project: Project, tl: Timeline, progress: Progress = _noop) -> Timeline:
    """Kaynak hedeften genişse (16:9 -> 9:16) yüz takibiyle kırpma noktaları üretir."""
    info = tl.source
    if not (tl.reframe.enabled and tl.reframe.track_faces):
        return tl
    if info.width / info.height <= tl.output.width / tl.output.height + 0.01:
        return tl
    keys = project.load_analysis("faces")
    if keys is None:
        progress("faces", 0.0, "yüz takibi")
        from .analyze.faces import track_faces as _track

        video = ensure_proxy(project, progress)
        keys = [k.model_dump() for k in _track(video)]
        project.save_analysis("faces", keys)
        progress("faces", 1.0, f"{len(keys)} nokta")
    from .core.timeline import ReframeKey

    tl.reframe.keys = [ReframeKey.model_validate(k) for k in keys]
    return tl


def smart(
    project: Project,
    progress: Progress = _noop,
    instruction: str | None = None,
    assets: bool = True,
    review: bool = True,
) -> tuple[Timeline, dict]:
    """LLM planı + yüz takibi + b-roll üretimi. Yeni timeline sürümü kaydeder."""
    from .agent.planner import smart_plan

    tl = smart_plan(project, progress, instruction)
    tl = track_faces(project, tl, progress)
    report: dict = {}
    if assets and tl.brolls:
        from .generate.router import fill_brolls

        fr = fill_brolls(project, tl, progress)
        report = {"filled": fr.filled, "failed": fr.failed, "providers": fr.providers}
        if review and fr.filled:
            from .agent.reviewer import review_brolls

            try:
                report["review"] = review_brolls(project, tl, progress)
            except Exception as e:  # öz-denetim opsiyonel
                report["review"] = {"error": str(e)}
    project.save_timeline(tl, note=f"Akıllı kurgu: {len(tl.brolls)} b-roll, {len(tl.texts)} yazı, {len(tl.zooms)} zoom")
    return tl, report


def auto(
    project: Project,
    progress: Progress = _noop,
    instruction: str | None = None,
    assets: bool = True,
    render_final: bool = True,
) -> tuple[Timeline, dict]:
    """Promptsuz tam otomatik kurgu: temizlenmiş kurgu üzerine analiz araçları -> video profili -> senarist ->
    uygulayıcı ajanlar (b-roll, animasyon, greenscreen, müzik) -> görsel denetim -> son render.
    `instruction` verilmezse agents.yaml'daki varsayılan stil kullanılır."""
    from .agent.director import run_director

    tl = project.timeline()
    tl = track_faces(project, tl, progress)
    if tl.reframe.keys:
        project.save_timeline(tl, note="Yüz takipli kırpma")
    report: dict = {}
    try:
        tl, report = run_director(project, instruction, progress, assets=assets, review=assets)
    except Exception as e:  # LLM yoksa / hata verirse temizlenmiş kurguyla devam (altyazı + tema)
        report["director_error"] = str(e)[:400]
        tl = project.timeline()
    if render_final:
        from .render.render import render

        tl = project.timeline()
        if assets:
            if any(not b.asset for b in tl.brolls):
                from .generate.router import fill_brolls

                if fill_brolls(project, tl, progress).filled:
                    project.save_timeline(tl, note="Eksik b-roll'lar üretildi")
            if any(not m.asset or m.theme != tl.theme for m in tl.motions):
                from .generate.motion_gfx import fill_motions

                if fill_motions(project, tl, progress)["filled"]:
                    project.save_timeline(tl, note="Animasyonlar üretildi")
        res = render(project, preview=False, progress=progress)
        report["render"] = {"file": res.path.name, "duration": res.duration, "seconds": res.seconds}
    project.set_status("rendered" if render_final else "planned")
    return tl, report


def cut_report(tl: Timeline) -> dict:
    return {
        "source_duration": round(tl.source.duration, 2),
        "output_duration": round(tl.output_duration(), 2),
        "cuts": len(tl.cuts),
        "removed_by_reason": summarize_cuts(tl.cuts),
    }
