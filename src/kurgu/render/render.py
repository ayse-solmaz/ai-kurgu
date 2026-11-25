"""Timeline -> video. Tek ffmpeg grafiğiyle kesme, kırpma, zoom, b-roll, altyazı ve ses.

Adımlar:
  1. Ses: kesilmiş konuşma + temizlik + dış ses + müzik (ducking) -> pre.wav
  2. loudnorm iki geçiş -> audio.wav (hedef LUFS)
  3. Görüntü: kesim + en-boy dönüşümü + zoom + b-roll + ASS altyazı -> mp4
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core import ffmpeg as ff
from ..core.config import get_config
from ..core.project_store import Project
from ..core.timeline import Timeline, TimeMap, Transcript
from .captions_ass import OutText, build_ass, build_srt, map_words
from .reframe import fit_filter

Progress = Callable[[str, float, str], None]
FADE = 0.012  # kesim noktalarında tık sesini önleyen mikro geçiş


@dataclass
class RenderResult:
    path: Path
    srt: Path | None
    duration: float
    encoder: str
    seconds: float


def _even(x: float) -> int:
    v = int(round(x))
    return max(2, v - (v % 2))


# --------------------------------------------------------------------------- yardımcılar


def resolve_texts(tl: Timeline, tr: Transcript | None, tm: TimeMap) -> list[OutText]:
    words = tr.word_map() if tr else {}
    out = []
    for t in tl.texts:
        try:
            a, b = t.anchor.resolve(words)
        except ValueError:
            continue
        r = tm.range_to_out(a, b)
        if r:
            out.append(OutText(t.text, r[0], max(r[1], r[0] + 0.6), t.style, t.position))
    for ch in tl.meta.chapters:
        try:
            a, b = ch.anchor.resolve(words)
        except ValueError:
            continue
        o = tm.to_out(a, "next")
        if o is not None and tl.output.width > tl.output.height:
            out.append(OutText(ch.title, o, o + 3.0, "chapter", "top"))
    return out


def zoom_expr(tl: Timeline, tr: Transcript | None, tm: TimeMap) -> str | None:
    words = tr.word_map() if tr else {}
    terms = []
    for z in tl.zooms:
        try:
            a, b = z.anchor.resolve(words)
        except ValueError:
            continue
        r = tm.range_to_out(a, b)
        if not r:
            continue
        s, e = r
        k = max(z.ease, 0.04)
        terms.append(
            f"{z.scale - 1:.3f}*clip((it-{s:.3f})/{k:.3f},0,1)*clip(({e:.3f}-it)/{k:.3f},0,1)"
        )
    # Kesimlerde dönüşümlü yakınlaştırma: her ikinci parça biraz daha yakın çekilir, böylece
    # konuşan kafa videolarındaki "jump cut" sıçramaları bilinçli bir kurgu geçişi gibi görünür.
    if tl.cut_punch and tl.cut_punch > 1.0:
        for idx, (a, b, o) in enumerate(tm.spans):
            if idx % 2 == 1:
                terms.append(f"{tl.cut_punch - 1:.3f}*between(it,{o:.3f},{o + (b - a):.3f})")
    if not terms:
        return None
    return "1+" + "+".join(terms)


def sfx_events(tl: Timeline, tr: Transcript | None, tm: TimeMap) -> dict[str, list[float]]:
    """Ses efektlerinin çıktı zamanları: b-roll girişleri (whoosh), ekran yazıları (pop)."""
    words = tr.word_map() if tr else {}
    ev: dict[str, list[float]] = {"whoosh": [], "pop": []}
    for br in tl.brolls:
        if not br.asset:
            continue
        try:
            r = tm.range_to_out(*br.anchor.resolve(words))
        except ValueError:
            continue
        if r:
            ev["whoosh"].append(max(0.0, r[0] - 0.28))  # geçişten hemen önce başlasın
    for t in tl.texts:
        try:
            r = tm.range_to_out(*t.anchor.resolve(words))
        except ValueError:
            continue
        if r:
            ev["pop"].append(r[0])
    from ..generate.motion_gfx import motion_span

    for mg in tl.motions:  # tam ekran anlatımda whoosh, kartlarda pop
        if not mg.asset:
            continue
        span = motion_span(mg, tl, words, tm)
        if span:
            ev["whoosh" if mg.layout == "full" else "pop"].append(max(0.0, span[0] - (0.28 if mg.layout == "full" else 0.0)))
    return ev


GLASS_THEMES = {"apple"}


def motion_glass(tl: Timeline, mg, tpls: dict) -> bool:
    """Bu animasyonun arkasındaki video buzlu cam gibi bulanıklaştırılsın mı?
    Cam yüzeyli temada açık; şablon `"glass": false` ile kapatabilir."""
    if (mg.theme or tl.theme) not in GLASS_THEMES:
        return False
    t = tpls.get(mg.template)
    return not (t and t.spec.get("glass") is False)


def glass_backdrop(cur: str, mg: str, i: int, s: float, e: float, W: int) -> list[str]:
    """Gerçek "backdrop blur": animasyonun yarı saydam yüzeylerinin altındaki videoyu bulanıklaştırır.

    Maske animasyonun alfa kanalından çıkarılır (cam paneller ~%45 opak, gölgeler daha saydam):
    alfa rampası gölgeleri dışarıda bırakır, giriş/çıkış animasyonunda maske de yumuşakça açılıp kapanır.
    Bulanıklık yalnızca animasyonun süresi boyunca hesaplanır (trim)."""
    sigma = max(8.0, W * 0.028)
    return [
        f"[{mg}]split[{mg}o][{mg}m]",
        f"[{mg}m]alphaextract,lut=y='clip((val-40)*3,0,255)',gblur=sigma={W * 0.003:.1f}[gm{i}]",
        f"[{cur}]split[gb{i}][gc{i}]",
        f"[gc{i}]trim=start={s:.3f}:end={e:.3f},gblur=sigma={sigma:.1f}:steps=2,"
        f"eq=saturation=1.35:brightness=0.03,format=yuva420p[gl{i}]",
        f"[gl{i}][gm{i}]alphamerge[ga{i}]",
        f"[gb{i}][ga{i}]overlay=0:0:eof_action=pass:enable='between(t,{s:.3f},{e:.3f})'[vg{i}]",
    ]


def cut_lines(keep: list[tuple[float, float]], stream: str, kind: str) -> tuple[list[str], str]:
    """Tutulan aralıkları kesip birleştiren filtre satırları. kind: v | a"""
    lines, labels = [], []
    for i, (a, b) in enumerate(keep):
        d = b - a
        lab = f"{kind}{i}"
        if kind == "v":
            lines.append(f"[{stream}]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS[{lab}]")
        else:
            f = min(FADE, d / 4)
            lines.append(
                f"[{stream}]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={f:.4f},afade=t=out:st={max(d - f, 0):.4f}:d={f:.4f}[{lab}]"
            )
        labels.append(f"[{lab}]")
    out = f"{kind}cat"
    if len(labels) == 1:
        lines.append(f"{labels[0]}{'null' if kind == 'v' else 'anull'}[{out}]")
    else:
        flags = "v=1:a=0" if kind == "v" else "v=0:a=1"
        lines.append(f"{''.join(labels)}concat=n={len(labels)}:{flags}[{out}]")
    return lines, out


# --------------------------------------------------------------------------- ses


def _measure_loudness(wav: Path, target: float, tp: float) -> dict | None:
    err = ff.run_ffmpeg(
        ["-i", str(wav), "-af", f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"]
    )
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        return None
    data = json.loads(m.group(0))
    if data.get("input_i") in ("-inf", None):
        return None
    return data


def render_audio(
    project: Project,
    tl: Timeline,
    tr: Transcript | None,
    tm: TimeMap,
    keep: list[tuple[float, float]],
    work: Path,
    progress: Callable[[float, str], None] | None = None,
) -> Path:
    cfg = tl.audio
    inputs: list[str] = ["-i", str(project.source)]
    lines, acat = cut_lines(keep, "0:a", "a")
    chain = [f"highpass=f={cfg.highpass}"]
    if cfg.denoise:
        chain.append("afftdn=nr=10:nf=-40:tn=1")
    lines.append(f"[{acat}]{','.join(chain)},aresample=48000[speech]")
    cur = "speech"
    total = tm.duration
    words = tr.word_map() if tr else {}

    # Dış ses (voiceover): zamanında başlat, konuşmayı altında kıs
    vo_labels = []
    for i, vo in enumerate(v for v in tl.voiceovers if v.asset):
        try:
            a, _ = vo.anchor.resolve(words)
        except ValueError:
            continue
        o = tm.to_out(a, "next")
        if o is None:
            continue
        idx = inputs.count("-i")
        inputs += ["-i", str(project.abs(vo.asset))]
        ms = int(o * 1000)
        lines.append(
            f"[{idx}:a]aresample=48000,aformat=channel_layouts=mono,volume={vo.volume_db}dB,"
            f"adelay={ms}|{ms},apad=whole_dur={total:.3f}[vo{i}]"
        )
        vo_labels.append(f"[vo{i}]")
    if vo_labels:
        if len(vo_labels) > 1:
            lines.append(f"{''.join(vo_labels)}amix=inputs={len(vo_labels)}:normalize=0[voall]")
        else:
            lines.append(f"{vo_labels[0]}anull[voall]")
        lines.append("[voall]asplit=2[vomix][vosc]")
        lines.append(f"[{cur}][vosc]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=400[speechd]")
        lines.append("[speechd][vomix]amix=inputs=2:normalize=0:duration=first[withvo]")
        cur = "withvo"

    # Müzik: döngü, ses seviyesi, konuşma varken otomatik kısma (ducking)
    if tl.music and tl.music.asset:
        idx = inputs.count("-i")
        inputs += ["-stream_loop", "-1", "-i", str(project.abs(tl.music.asset))]
        lines.append(f"[{cur}]asplit=2[mainmix][mainsc]")
        lines.append(
            f"[{idx}:a]aresample=48000,aformat=channel_layouts=mono,volume={tl.music.volume_db}dB,"
            f"atrim=duration={total:.3f},afade=t=out:st={max(total - 2, 0):.3f}:d=2[mus]"
        )
        lines.append("[mus][mainsc]sidechaincompress=threshold=0.015:ratio=8:attack=30:release=600[musd]")
        lines.append("[mainmix][musd]amix=inputs=2:normalize=0:duration=first[withmus]")
        cur = "withmus"

    # Ses efektleri (b-roll whoosh, yazı pop): tek dosya, asplit ile çoğaltılıp zamanına yerleştirilir
    if getattr(cfg, "sfx", False):
        from ..generate.sfx import sfx_path

        sfx_labels = []
        for name, times in sfx_events(tl, tr, tm).items():
            times = [t for t in times if t < total - 0.2]
            if not times:
                continue
            idx = inputs.count("-i")
            inputs += ["-i", str(sfx_path(name))]
            n = len(times)
            split = "".join(f"[{name}{k}]" for k in range(n))
            lines.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=mono,volume={cfg.sfx_db}dB,"
                         f"asplit={n}{split}" if n > 1 else
                         f"[{idx}:a]aresample=48000,aformat=channel_layouts=mono,volume={cfg.sfx_db}dB[{name}0]")
            for k, t in enumerate(times):
                ms = int(t * 1000)
                lines.append(f"[{name}{k}]adelay={ms}|{ms},apad=whole_dur={total:.3f}[{name}d{k}]")
                sfx_labels.append(f"[{name}d{k}]")
        if sfx_labels:
            lines.append(f"{''.join(sfx_labels)}amix=inputs={len(sfx_labels)}:normalize=0[sfxall]"
                         if len(sfx_labels) > 1 else f"{sfx_labels[0]}anull[sfxall]")
            lines.append(f"[{cur}][sfxall]amix=inputs=2:normalize=0:duration=first[withsfx]")
            cur = "withsfx"

    lines.append(f"[{cur}]anull[aout]")
    script = work / "audio.graph"
    script.write_text(";\n".join(lines), encoding="utf-8")
    pre = work / "pre.wav"
    ff.run_ffmpeg(
        [*inputs, *ff.filter_script_args(script), "-map", "[aout]", "-ac", "1", "-c:a", "pcm_s16le", str(pre)],
        duration=total,
        progress=(lambda f, m: progress(f * 0.7, m)) if progress else None,
    )

    final = work / "audio.wav"
    measured = _measure_loudness(pre, cfg.loudness, cfg.true_peak)
    if measured:
        ln = (
            f"loudnorm=I={cfg.loudness}:TP={cfg.true_peak}:LRA=11:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true"
        )
    else:
        ln = "anull"
    ff.run_ffmpeg(["-i", str(pre), "-af", f"{ln},aresample=48000", "-ac", "2", "-c:a", "pcm_s16le", str(final)])
    if progress:
        progress(1.0, "ses hazır")
    return final


# --------------------------------------------------------------------------- görüntü


def render(
    project: Project,
    tl: Timeline | None = None,
    preview: bool = False,
    progress: Progress | None = None,
    out_path: Path | None = None,
) -> RenderResult:
    t0 = time.time()
    cfg = get_config()
    if tl is None:
        tl = project.timeline()
        # Eksik dış ses ve müziği hazırla (sadece kayıtlı timeline için)
        from ..generate.music import fill_music
        from ..generate.tts import fill_voiceovers

        vo = fill_voiceovers(project, tl) if tl.voiceovers else {"filled": []}
        mus = fill_music(project, tl)
        if vo["filled"] or mus:
            project.save_timeline(tl, note="Dış ses / müzik hazırlandı")
        if vo.get("failed") and progress:
            progress("audio", 0.0, f"dış ses üretilemedi: {list(vo['failed'].values())[0][:50]}")
    try:
        tr = project.transcript()
    except FileNotFoundError:
        tr = None
    tm = tl.time_map()
    keep = [(a, b) for a, b, _ in tm.spans]
    if not keep:
        raise ValueError("Tüm video kesilmiş; çıktı boş olur.")

    W, H, fps = tl.output.width, tl.output.height, tl.output.fps
    if preview:
        k = cfg.render.preview_height / min(W, H)
        if k < 1:
            W, H = _even(W * k), _even(H * k)
    W, H = _even(W), _even(H)

    work = project.renders_dir / f"work-{int(time.time() * 1000)}"
    work.mkdir(parents=True, exist_ok=True)

    def rep(stage: str, frac: float, msg: str = ""):
        if progress:
            progress(stage, frac, msg)

    # 1) altyazı (altyazıyı gizleyen animasyonlar ekrandayken o kelimeler atlanır: ör. kelime
    #    patlaması zaten konuşulanı gösterir, tam ekran anlatımda altyazı içeriğin üstüne düşer)
    words = map_words(tr, tm) if tr else []
    if words and tl.motions:
        from ..generate.motion_gfx import list_templates, motion_span

        tpls = list_templates()
        wm = tr.word_map()
        hide = [sp for m in tl.motions if tpls.get(m.template) and tpls[m.template].spec.get("hide_captions")
                for sp in [motion_span(m, tl, wm, tm)] if sp]
        if hide:
            words = [w for w in words if not any(a - 0.05 <= w.start < b for a, b in hide)]
    texts = resolve_texts(tl, tr, tm)
    ass = build_ass(words, tl.captions, tl.output.width, tl.output.height, texts)
    (work / "captions.ass").write_text(ass, encoding="utf-8")
    has_subs = bool(words and tl.captions.enabled) or bool(texts)

    # 2) ses
    audio = None
    if tl.source.has_audio:
        rep("audio", 0.0, "ses işleniyor")
        audio = render_audio(project, tl, tr, tm, keep, work, lambda f, m: rep("audio", f, m))

    # 3) görüntü grafiği
    inputs = ["-i", str(project.source)]
    if audio:
        inputs += ["-i", str(audio)]
    lines, vcat = cut_lines(keep, "0:v", "v")
    lines.append(f"[{vcat}]fps={fps}[vfps]")

    # en-boy dönüşümü (yüz takip noktaları kaynak zamandan çıktı zamanına çevrilir)
    centers = None
    if tl.reframe.enabled and tl.reframe.keys:
        centers = []
        for k in tl.reframe.keys:
            o = tm.to_out(k.t, "strict")
            if o is not None:
                centers.append((o, k.cx))
    mode = "crop" if tl.reframe.enabled else tl.reframe.fill
    lines += fit_filter(tl.source.width, tl.source.height, W, H, mode, centers, "vfps", "vfit")
    cur = "vfit"

    # zoom-punch
    z = zoom_expr(tl, tr, tm)
    if z:
        lines.append(
            f"[{cur}]zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps}[vzoom]"
        )
        cur = "vzoom"

    # b-roll katmanları
    wmap = tr.word_map() if tr else {}
    for i, br in enumerate(tl.brolls):
        if not br.asset or not project.abs(br.asset).exists():
            continue
        try:
            a, b = br.anchor.resolve(wmap)
        except ValueError:
            continue
        r = tm.range_to_out(a, b)
        if not r:
            continue
        s, e = r
        d = e - s
        idx = inputs.count("-i")
        asset = project.abs(br.asset)
        if asset.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            inputs += ["-loop", "1", "-t", f"{d:.3f}", "-i", str(asset)]
        else:
            inputs += ["-i", str(asset)]
        fd = min(0.18, d / 4)
        if br.mode == "pip":
            pw = _even(W * (0.8 if W < H else 0.38))
            ph = _even(pw * 9 / 16)
            size = f"scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph}"
            pos = f"x=W-w-{int(W * 0.05)}:y={int(H * 0.08)}"
        else:
            size = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
            pos = "x=0:y=0"
        lines.append(
            f"[{idx}:v]{size},setsar=1,fps={fps},trim=duration={d:.3f},format=yuva420p,"
            f"fade=t=in:st=0:d={fd:.3f}:alpha=1,fade=t=out:st={max(d - fd, 0):.3f}:d={fd:.3f}:alpha=1,"
            f"setpts=PTS-STARTPTS+{s:.3f}/TB[br{i}]"
        )
        lines.append(f"[{cur}][br{i}]overlay={pos}:eof_action=pass:enable='between(t,{s:.3f},{e:.3f})'[vb{i}]")
        cur = f"vb{i}"

    # hareketli grafikler (alfa kanallı .mov): b-roll'un da üstünde, altyazının altında
    from ..generate.motion_gfx import list_templates as _lt, motion_span

    tpls_all = _lt() if tl.motions else {}

    for i, mg in enumerate(tl.motions):
        if not mg.asset or not project.abs(mg.asset).exists():
            continue
        span = motion_span(mg, tl, wmap, tm)
        if not span:
            continue
        s, e = span
        idx = inputs.count("-i")
        inputs += ["-i", str(project.abs(mg.asset))]
        size, pos = f"scale={W}:{H}", "0:0"
        if mg.template.startswith("gs:"):  # greenscreen: klibin kendi boyutu/konumu, yumuşak giriş-çıkış
            from ..generate.greenscreen import get_clip, overlay_geometry

            clip = get_clip(mg)
            if clip:
                size, pos = overlay_geometry(clip, W, H)
            fd = min(0.2, (e - s) / 4)
            size += (f",format=yuva420p,fade=t=in:st=0:d={fd:.2f}:alpha=1,"
                     f"fade=t=out:st={max(e - s - fd, 0):.3f}:d={fd:.2f}:alpha=1")
        lines.append(f"[{idx}:v]{size},setsar=1,fps={fps},trim=duration={e - s:.3f},format=yuva420p,"
                     f"setpts=PTS-STARTPTS+{s:.3f}/TB[mg{i}]")
        top = f"mg{i}"
        if not mg.template.startswith("gs:") and motion_glass(tl, mg, tpls_all):
            lines += glass_backdrop(cur, top, i, s, e, W)
            cur, top = f"vg{i}", f"mg{i}o"
        lines.append(f"[{cur}][{top}]overlay={pos}:eof_action=pass:enable='between(t,{s:.3f},{e:.3f})'[vm{i}]")
        cur = f"vm{i}"

    post = []
    if has_subs:
        fonts = cfg.path(cfg.render.fonts_dir)
        sub = "subtitles=filename=captions.ass"
        if fonts.exists() and any(fonts.iterdir()):
            sub += f":fontsdir='{ff.escape_filter_path(str(fonts))}'"
        post.append(sub)

    out = out_path or project.new_render_path("preview" if preview else "final")
    script = work / "video.graph"

    def encode(encoder: str):
        pix = "nv12" if encoder.endswith("_qsv") else "yuv420p"
        graph = lines + [f"[{cur}]{','.join(post + [f'format={pix}'])}[vout]"]
        script.write_text(";\n".join(graph), encoding="utf-8")
        args = [*inputs, *ff.filter_script_args(script), "-map", "[vout]"]
        if audio:
            args += ["-map", "1:a", "-c:a", "aac", "-b:a", cfg.render.audio_bitrate]
        args += [*ff.video_encode_args(encoder, cfg.render.quality, preview), "-r", str(fps),
                 "-movflags", "+faststart", "-t", f"{tm.duration:.3f}", str(out.resolve())]
        rep("video", 0.0, f"görüntü işleniyor ({encoder})")
        ff.run_ffmpeg(args, duration=tm.duration, progress=lambda f, m: rep("video", f, m), cwd=work)

    encoder = ff.pick_encoder(cfg.render.encoder)
    try:
        encode(encoder)
    except ff.FFmpegError:
        if encoder == "libx264":
            raise
        encoder = "libx264"  # donanım encoder'ı başarısız olursa yazılım encoder'ına düş
        encode(encoder)

    srt = None
    if words:
        srt = out.with_suffix(".srt")
        srt.write_text(build_srt(words), encoding="utf-8")
    shutil.copy2(work / "captions.ass", out.with_suffix(".ass"))
    if not preview:
        shutil.rmtree(work, ignore_errors=True)
    rep("done", 1.0, str(out))
    return RenderResult(out, srt, tm.duration, encoder, time.time() - t0)
