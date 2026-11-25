"""Komut satırı arayüzü.

Örnekler:
  kurgu doctor
  kurgu edit video.mp4 --preset shorts
  kurgu render <proje> --preview
  kurgu transcript <proje>
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import pipeline
from .core.config import get_config, list_presets, model_registry
from .core.project_store import Project

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Türkçe karakterler için
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

app = typer.Typer(help="AI Kurgu: açık kaynak, ajan tabanlı video kurgu", no_args_is_help=True)
console = Console()

STAGE_NAMES = {
    "ingest": "İçe alma",
    "asr": "Transkript",
    "vad": "Sessizlik analizi",
    "audio": "Ses",
    "video": "Görüntü",
    "proxy": "Önizleme kopyası",
    "plan": "Planlama",
    "assets": "B-roll üretimi",
    "review": "Öz-denetim",
    "done": "Bitti",
}


@contextmanager
def progress_ui():
    with Progress(
        TextColumn("[bold cyan]{task.description:<18}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TextColumn("[dim]{task.fields[msg]}"),
        console=console,
        transient=False,
    ) as prog:
        tasks: dict[str, int] = {}

        def cb(stage: str, frac: float, msg: str = ""):
            if stage == "done":
                return
            if stage not in tasks:
                tasks[stage] = prog.add_task(STAGE_NAMES.get(stage, stage), total=1.0, msg="")
            upd = {"completed": frac}
            if msg:
                upd["msg"] = msg[:60]
            prog.update(tasks[stage], **upd)

        yield cb


def _print_report(tl):
    rep = pipeline.cut_report(tl)
    t = Table(title="Temizlik özeti", show_header=False)
    t.add_row("Kaynak süre", f"{rep['source_duration']} sn")
    t.add_row("Çıktı süre", f"{rep['output_duration']} sn")
    t.add_row("Kesim sayısı", str(rep["cuts"]))
    names = {"silence": "sessizlik", "filler": "dolgu (ııı/eee)", "stutter": "kekeleme",
             "retake": "tekrar çekim", "unworded": "anlamsız ses", "trim": "baş/son boşluk", "manual": "elle"}
    for k, v in rep["removed_by_reason"].items():
        t.add_row(f"  - {names.get(k, k)}", f"{v} sn")
    console.print(t)


@app.command()
def doctor():
    """Kurulumu kontrol eder: ffmpeg, encoder'lar, Ollama, modeller."""
    from .core import ffmpeg as ff

    ok = "[green]✓[/green]"
    bad = "[red]✗[/red]"
    t = Table(title="AI Kurgu ortam kontrolü")
    t.add_column("Bileşen")
    t.add_column("Durum")
    try:
        t.add_row("ffmpeg", f"{ok} {ff.version()}  ({ff.ffmpeg()})")
        t.add_row("libass (altyazı)", ok if ff.has_filter("subtitles") else bad)
        t.add_row("zoompan", ok if ff.has_filter("zoompan") else bad)
        for enc in ("h264_qsv", "h264_nvenc", "libx264"):
            t.add_row(f"encoder {enc}", ok if ff.encoder_works(enc) else "[dim]-[/dim]")
        t.add_row("seçilen encoder", ff.pick_encoder(get_config().render.encoder))
    except ff.FFmpegError as e:
        t.add_row("ffmpeg", f"{bad} {e}")

    import httpx

    from .core.ollama_server import ensure_ollama

    cfg = get_config()
    ensure_ollama()
    try:
        tags = httpx.get(f"{cfg.ollama.host}/api/tags", timeout=3).json()
        names = {m["name"] for m in tags.get("models", [])}
        t.add_row("Ollama", f"{ok} {cfg.ollama.host}")
        for key in ("llm", "vlm"):
            name = getattr(cfg.models, key)
            have = name in names or f"{name}:latest" in names
            t.add_row(f"  {key}: {name}", ok if have else f"{bad} yok → ollama pull {name}")
    except Exception:
        t.add_row("Ollama", f"{bad} çalışmıyor ({cfg.ollama.host})")

    reg = model_registry()
    t.add_row("Ticari mod", "açık" if cfg.commercial_mode else "kapalı")
    for key in ("asr", "llm", "vlm", "image", "video", "tts"):
        name = getattr(cfg.models, key)
        info = reg.get(name)
        lic = f"{info.license}{'' if info.commercial_ok else ' [yellow](ticari değil)[/yellow]'}" if info else "?"
        t.add_row(f"model {key}", f"{name} — {lic}")
    console.print(t)


@app.command()
def edit(
    video: Path = typer.Argument(..., exists=True, readable=True, help="Kaynak video"),
    preset: str = typer.Option("youtube", "--preset", "-p", help=f"Preset: {', '.join(list_presets())}"),
    name: Optional[str] = typer.Option(None, help="Proje adı"),
    preview: bool = typer.Option(False, "--preview", help="Hızlı, düşük çözünürlüklü çıktı"),
    no_render: bool = typer.Option(False, "--no-render", help="Sadece analiz et ve planla"),
    smart: bool = typer.Option(False, "--smart", help="LLM ile anla; b-roll, yazı, zoom, hook ekle (eski planlayıcı)"),
    auto: bool = typer.Option(False, "--auto", help="Tam otomatik: video profili, senarist, ajanlar, müzik, son render"),
    instruction: Optional[str] = typer.Option(None, "--istek", "-i", help="Kurgu isteği, ör. 'enerjik olsun'"),
    no_assets: bool = typer.Option(False, "--no-assets", help="B-roll üretme (sadece planla)"),
):
    """Videoyu baştan sona kurgular: analiz, temizlik, altyazı, (akıllı plan), render."""
    report = None
    with progress_ui() as cb:
        project = pipeline.ingest(video, preset, name, cb)
        stats = pipeline.analyze(project, cb)
        tl = pipeline.plan_cleanup(project)
        if auto:
            tl, report = pipeline.auto(project, cb, instruction, assets=not no_assets, render_final=not no_render)
        elif smart:
            tl, report = pipeline.smart(project, cb, instruction, assets=not no_assets)
        else:
            tl = pipeline.track_faces(project, tl, cb)
            if tl.reframe.keys:
                project.save_timeline(tl, note="Yüz takipli kırpma")
    console.print(f"[bold]Proje:[/bold] {project.id}")
    console.print(f"Analiz: {json.dumps(stats, ensure_ascii=False)}")
    _print_report(tl)
    if smart or auto:
        _print_smart(tl, report)
    if auto and report:
        if report.get("script"):
            console.print("[bold]Senaryo[/bold]")
            console.print(report["script"])
        if report.get("render"):
            console.print(f"[green]Çıktı:[/green] {project.renders_dir / report['render']['file']}")
        return
    if no_render:
        return
    _render(project, preview)


def _print_smart(tl, report: dict | None):
    m = tl.meta
    if m.title:
        console.print(f"[bold]Başlık:[/bold] {m.title}")
    if m.hook:
        console.print(f"[bold]Hook:[/bold] {m.hook}")
    if m.summary:
        console.print(f"[bold]Özet:[/bold] {m.summary}")
    if m.hashtags:
        console.print(" ".join(m.hashtags))
    for b in tl.brolls:
        state = "[green]hazır[/green]" if b.asset else "[yellow]yok[/yellow]"
        console.print(f"  b-roll {b.id} ({state}, {b.provider or '-'}): {b.prompt}")
    for t in tl.texts:
        console.print(f"  yazı {t.id} [{t.style}]: {t.text}")
    if report and report.get("failed"):
        console.print("[yellow]Üretilemeyen b-roll'lar (sağlayıcı durumu):[/yellow]")
        for k, v in report.get("providers", {}).items():
            console.print(f"  {k}: {v}")


@app.command()
def plan(
    project: str,
    instruction: Optional[str] = typer.Option(None, "--istek", "-i"),
    no_assets: bool = typer.Option(False, "--no-assets"),
):
    """Mevcut projeye LLM ile akıllı plan uygular (b-roll, yazı, zoom, hook)."""
    p = Project.open(project)
    with progress_ui() as cb:
        tl, report = pipeline.smart(p, cb, instruction, assets=not no_assets)
    _print_smart(tl, report)


@app.command()
def direct(
    project: str,
    stil: Optional[str] = typer.Option(None, "--stil", "-s", help="Kurgu stili / yönetmene talimat"),
    no_assets: bool = typer.Option(False, "--no-assets"),
):
    """Yönetmen ajanı + alt ajanlarla kurgular (agents.yaml'daki sistem prompt'larıyla)."""
    from .agent.director import run_director

    p = Project.open(project)
    with progress_ui() as cb:
        tl, report = run_director(p, stil, cb, assets=not no_assets)
    b = report.get("brief", {})
    console.print(f"[bold]Ton:[/bold] {b.get('tone')} · [bold]tempo:[/bold] {b.get('pacing')}")
    console.print(f"[bold]Görsel dil:[/bold] {b.get('visual_style')}")
    _print_smart(tl, report.get("assets"))
    if report.get("review"):
        console.print(f"[bold]Denetim:[/bold] {report['review']}")


@app.command()
def animate(project: str, no_render: bool = typer.Option(False, "--no-render")):
    """Mevcut kurguya hareketli grafik animasyonları ekler (motion/templates şablonlarıyla)."""
    from .agent.animator import animate as run_animate

    p = Project.open(project)
    with progress_ui() as cb:
        tl, report = run_animate(p, cb, render_assets=not no_render)
    for m in tl.motions:
        state = "[green]hazır[/green]" if m.asset else "[yellow]render yok[/yellow]"
        console.print(f"  {m.id} {m.template} ({state}): {json.dumps(m.params, ensure_ascii=False)[:110]}")
    if report.get("replaced_brolls"):
        console.print(f"[cyan]Anlatım animasyonunun yerini aldığı b-roll'lar:[/cyan] {report['replaced_brolls']}")
    for r in report.get("rejected", []):
        console.print(f"  [dim]elendi: {r}[/dim]")
    if report.get("render", {}).get("failed"):
        console.print(f"[yellow]Hatalar:[/yellow] {report['render']['failed']}")


gs_app = typer.Typer(help="Greenscreen kütüphanesi (assets/greenscreen)")
app.add_typer(gs_app, name="greenscreen")


@gs_app.command("list")
def gs_list():
    """Kütüphanedeki klipler ve otomatik tetikleyiciler (CTA şablonu dahil)."""
    from .generate.greenscreen import list_clips, trigger_sources

    for c in list_clips().values():
        console.print(f"[bold]{c.id}[/bold] {c.duration:.1f} sn, {c.spec.get('position', 'lower')} — "
                      f"tetikleyiciler: {', '.join(c.spec.get('triggers', [])) or '-'} [dim]{c.spec.get('license', '')}[/dim]")
    n = len([t for t in trigger_sources() if not t["template"].startswith("gs:")])
    console.print(f"[dim]+ şablon tetikleyicileri: {n} ifade (abone ol, beğen, takip et, yorum, paylaş, bildirim)[/dim]")


@gs_app.command("add")
def gs_add(
    file: Path,
    name: str = typer.Option(None, "--name"),
    triggers: str = typer.Option("", "--triggers", help="Virgülle: 'abone ol,subscribe'"),
    position: str = typer.Option("lower", "--position", help="lower|center|top|bottom-right|bottom-left|full"),
    scale: float = typer.Option(0.7, "--scale"),
    license: str = typer.Option(None, "--license"),
):
    """Yeşil/mavi perdeli bir videoyu kütüphaneye ekler ve perdeyi silinmiş halini hazırlar."""
    from .generate.greenscreen import add_clip, keyed

    c = add_clip(file, name, [t.strip() for t in triggers.split(",") if t.strip()], position, scale, license)
    k = keyed(c)
    console.print(f"[green]Eklendi:[/green] {c.id} (perde rengi {k['key_color']})")


@gs_app.command("fetch")
def gs_fetch(
    query: str,
    triggers: str = typer.Option("", "--triggers"),
    position: str = typer.Option("lower", "--position"),
):
    """Pixabay/Pexels'ten greenscreen klip indirir (ör. 'subscribe button')."""
    from .generate.greenscreen import fetch_stock, keyed

    c = fetch_stock(query, [t.strip() for t in triggers.split(",") if t.strip()], position)
    keyed(c)
    console.print(f"[green]İndirildi:[/green] {c.id} — {c.spec.get('source')}")


motion_app = typer.Typer(help="Hareketli grafik şablonları")
app.add_typer(motion_app, name="motion")


@motion_app.command("list")
def motion_list():
    """Şablon kütüphanesini listeler (motion/templates)."""
    from .generate.motion_gfx import list_templates

    for t in list_templates().values():
        console.print(f"[bold]{t.id}[/bold] ({t.layout}) — {t.name}: [dim]{t.spec.get('description', '')[:90]}[/dim]")


@motion_app.command("preview")
def motion_preview(template: str, out: Path = typer.Option(Path("motion_preview.mp4"), "--out")):
    """Şablonu örnek parametreleriyle render edip koyu zemin üstünde mp4 olarak kaydeder."""
    from .core import ffmpeg as ff
    from .generate.motion_gfx import list_templates, render as mg_render

    t = list_templates()[template]
    r = mg_render(template, t.spec.get("example", {}), t.duration_bounds()[0], Path(".cache/motion_preview"), 1080, 1920,
                  preview=False)
    ff.run_ffmpeg(["-f", "lavfi", "-i", "color=c=0x2a3040:s=1080x1920:r=30", "-i", str(r["mov"]),
                   "-filter_complex", "[0][1]overlay=format=auto:shortest=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   str(out)])
    console.print(f"[green]Önizleme:[/green] {out}")


@app.command()
def assets(project: str, all: bool = typer.Option(False, "--all", help="Var olanları da yeniden üret")):
    """Eksik b-roll varlıklarını üretir."""
    from .generate.router import fill_brolls

    p = Project.open(project)
    tl = p.timeline()
    with progress_ui() as cb:
        rep = fill_brolls(p, tl, cb, only_missing=not all)
    p.save_timeline(tl, note=f"B-roll üretimi: {len(rep.filled)} hazır")
    console.print({"hazır": rep.filled, "başarısız": rep.failed, "sağlayıcılar": rep.providers})


@app.command()
def chat(
    project: str,
    message: Optional[str] = typer.Argument(None, help="İstek; verilmezse etkileşimli sohbet açılır"),
    preview: bool = typer.Option(False, "--preview", help="Her değişiklikten sonra önizleme üret"),
):
    """Kurguyu sohbetle düzenler: kurgu chat <proje> "ilk 3 saniyeyi kes"."""
    from .agent.chat_agent import chat as run_chat

    p = Project.open(project)
    history: list[dict] = []
    messages = [message] if message else None
    while True:
        if messages is not None:
            if not messages:
                break
            msg = messages.pop(0)
        else:
            try:
                msg = console.input("[bold cyan]sen>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if msg.lower() in ("q", "çık", "exit", "quit", ""):
                break
        with console.status("Ajan çalışıyor..."):
            res = run_chat(p, msg, history)
        history = res.history
        console.print(f"[bold green]ajan>[/bold green] {res.reply}")
        for a in res.actions:
            console.print(f"  [dim]• {a}[/dim]")
        if res.version:
            console.print(f"  [dim]timeline v{res.version} kaydedildi (geri almak için: kurgu undo {p.id})[/dim]")
            if preview:
                _render(p, True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, reload: bool = False):
    """Web arayüzünü ve API'yi başlatır: http://127.0.0.1:8765"""
    import uvicorn

    console.print(f"[bold]AI Kurgu[/bold] → http://{host}:{port}")
    uvicorn.run("kurgu.api.server:app", host=host, port=port, reload=reload)


models_app = typer.Typer(help="Model indirme ve durum")
app.add_typer(models_app, name="models")


@models_app.command("pull")
def models_pull(kind: str = typer.Argument(..., help="image")):
    """Yerel modeli indirir (ör. kurgu models pull image)."""
    if kind != "image":
        raise typer.BadParameter("Şimdilik sadece 'image' destekleniyor; LLM/VLM için: ollama pull <model>")
    from .generate.image import pull

    console.print(f"İndiriliyor: {get_config().models.image} (büyük dosya, biraz sürebilir)")
    console.print(f"[green]Hazır:[/green] {pull()}")


@models_app.command("status")
def models_status():
    """B-roll sağlayıcılarının durumu."""
    from .generate.router import provider_status

    for k, v in provider_status().items():
        color = "green" if v == "hazır" else "yellow"
        console.print(f"[{color}]{k}[/{color}]: {v}")


def _render(project: Project, preview: bool):
    from .render.render import render as do_render

    with progress_ui() as cb:
        res = do_render(project, preview=preview, progress=cb)
    console.print(f"[green bold]Çıktı:[/green bold] {res.path}")
    if res.srt:
        console.print(f"Altyazı: {res.srt}")
    console.print(f"Süre {res.duration:.1f} sn · encoder {res.encoder} · {res.seconds:.0f} sn'de üretildi")


@app.command()
def render(
    project: str = typer.Argument(..., help="Proje ID'si (veya bir kısmı)"),
    preview: bool = typer.Option(False, "--preview"),
):
    """Geçerli timeline'ı video olarak üretir."""
    _render(Project.open(project), preview)


@app.command()
def analyze(project: str, force: bool = typer.Option(False, "--force")):
    """Analizi (yeniden) çalıştırır ve temizlik planını günceller."""
    p = Project.open(project)
    with progress_ui() as cb:
        stats = pipeline.analyze(p, cb, force=force)
    tl = pipeline.plan_cleanup(p)
    console.print(stats)
    _print_report(tl)


@app.command("list")
def list_projects():
    """Projeleri listeler."""
    t = Table("ID", "Ad", "Preset", "Durum", "Sürüm")
    for info in Project.list_all():
        t.add_row(info.id, info.name, info.preset, info.status, str(info.current_version))
    console.print(t)


@app.command()
def transcript(project: str, marks: bool = typer.Option(True, help="Kesilecek kelimeleri göster")):
    """Transkripti gösterir; kesilecek kelimeler üstü çizili."""
    p = Project.open(project)
    tr = p.transcript()
    tl = p.timeline() if p.has_timeline() else None
    tm = tl.time_map() if tl else None
    for s in tr.sentences:
        parts = []
        for i in s.word_ids:
            w = tr.words[i]
            cut = tm is not None and tm.to_out((w.start + w.end) / 2, "strict") is None
            if marks and cut:
                tag = "F" if w.is_filler else "K" if w.is_stutter else "R" if w.is_retake else ""
                parts.append(f"[red strike]{w.text}[/red strike][dim]{tag}[/dim]")
            else:
                parts.append(w.text)
        console.print(f"[dim]{s.start:7.2f}[/dim]  " + " ".join(parts))


@app.command()
def history(project: str):
    """Timeline sürüm geçmişi."""
    for h in Project.open(project).history():
        mark = "→" if h["current"] else " "
        console.print(f"{mark} v{h['version']}: {h['note'] or ''}")


@app.command()
def undo(project: str):
    """Bir önceki timeline sürümüne döner."""
    console.print(f"Geçerli sürüm: v{Project.open(project).undo()}")


@app.command()
def redo(project: str):
    """Geri alınan sürümü yeniden uygular."""
    console.print(f"Geçerli sürüm: v{Project.open(project).redo()}")


if __name__ == "__main__":
    app()
