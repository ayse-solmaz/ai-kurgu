"""Hareketli grafik şablonları: yükleme, parametre doğrulama, HTML oluşturma ve render.

Şablon = motion/templates/<id>/ klasörü:
  template.json  ad, LLM için açıklama, yerleşim (card|full), süre, parametre şeması, örnek
  index.html     HTML/CSS/JS animasyon (motion/runtime.js ve base.css'i kullanır)

Render: Electron görünmez pencerede kareleri şeffaf PNG olarak üretir (desktop/motion-render.js),
ffmpeg bunları alfa kanallı .mov'a (son render için) ve .webm'e (arayüz önizlemesi için) çevirir.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core import ffmpeg as ff
from ..core.config import project_root
from . import icons


class MotionError(RuntimeError):
    pass


@dataclass
class Template:
    id: str
    dir: Path
    spec: dict

    @property
    def name(self) -> str:
        return self.spec.get("name", self.id)

    @property
    def layout(self) -> str:
        return self.spec.get("layout", "card")

    def duration_bounds(self) -> tuple[float, float, float]:
        d = self.spec.get("duration", {})
        return float(d.get("default", 3.5)), float(d.get("min", 2.0)), float(d.get("max", 6.0))


def motion_root() -> Path:
    return project_root() / "motion"


def list_templates() -> dict[str, Template]:
    out: dict[str, Template] = {}
    root = motion_root() / "templates"
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        spec_f, html_f = d / "template.json", d / "index.html"
        if spec_f.exists() and html_f.exists():
            try:
                spec = json.loads(spec_f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            tid = spec.get("id") or d.name
            out[tid] = Template(tid, d, spec)
    return out


def catalog_for_llm(templates: dict[str, Template] | None = None) -> str:
    """LLM'e şablonları tanıtan kısa metin (ad, ne zaman kullanılır, parametreler)."""
    lines = []
    for t in (templates or list_templates()).values():
        if t.spec.get("auto"):  # tetikleyicilerle otomatik eklenen şablonlar (ör. cta) LLM kataloğunda yer almaz
            continue
        params = []
        for name, p in t.spec.get("params", {}).items():
            if p.get("type") == "list":
                if "item" in p:
                    fields = ", ".join(f"{k}:{v.get('type')}" for k, v in p["item"].items())
                    params.append(f"{name}: [{{{fields}}}] ({p.get('min', 1)}-{p.get('max', 4)} öğe)")
                else:
                    params.append(f"{name}: [metin] ({p.get('min', 1)}-{p.get('max', 4)})")
            elif p.get("type") == "enum":
                params.append(f"{name}: {'|'.join(p.get('values', []))}")
            else:
                req = "" if p.get("required") else "?"
                params.append(f"{name}{req}: {p.get('type')}")
        lines.append(f"- {t.id} ({t.name}): {t.spec.get('description', '')}\n  parametreler: {'; '.join(params)}\n"
                     f"  örnek: {json.dumps(t.spec.get('example', {}), ensure_ascii=False)}")
    return "\n".join(lines)


def catalog_short(templates: dict[str, Template] | None = None) -> str:
    """Senarist için kısa katalog: kimlik + ne zaman kullanılır (parametresiz; bağlamı küçük tutar)."""
    out = []
    for t in (templates or list_templates()).values():
        if t.spec.get("auto"):
            continue
        d = t.spec.get("description", "")
        d = d.split(" (ör.")[0].split("; ")[0]
        out.append(f"- {t.id}: {d[:110]}")
    return "\n".join(out)


# --------------------------------------------------------------------------- doğrulama


def _coerce(spec: dict, value):
    typ = spec.get("type", "string")
    if typ == "number":
        if isinstance(value, (int, float)):
            return value
        s = str(value).replace("%", "").replace(",", ".").strip()
        try:
            return float(s) if "." in s else int(s)
        except ValueError:
            raise MotionError(f"sayı değil: {value!r}")
    if typ == "enum":
        vals = spec.get("values", [])
        return value if value in vals else spec.get("default", vals[0] if vals else value)
    if typ in ("string", "icon", "color"):
        s = str(value).strip()
        mx = spec.get("max")
        if mx and len(s) > mx:
            cut = s[:mx].rsplit(" ", 1)[0] if " " in s[:mx] else s[:mx]
            s = cut.rstrip(",;:-— ")
        return s
    return value


def validate_params(tpl: Template, params: dict) -> dict:
    """Parametreleri şemaya göre düzeltir (tip dönüşümü, uzunluk, liste sınırları). Eksik zorunlu alan -> hata."""
    out: dict = {}
    for name, spec in tpl.spec.get("params", {}).items():
        val = params.get(name, spec.get("default"))
        if val is None or val == "" or val == []:
            if spec.get("required"):
                raise MotionError(f"{tpl.id}: '{name}' gerekli")
            continue
        if spec.get("type") == "list":
            items = val if isinstance(val, list) else [val]
            mx = spec.get("max", 4)
            if len(items) > mx and "item" not in spec and all(isinstance(x, str) for x in items):
                # metin listesi fazlaysa kesme (anlam kaybolur): fazlalığı son öğede birleştir
                items = items[: mx - 1] + [" ".join(items[mx - 1:])]
            items = items[:mx]
            if len(items) < spec.get("min", 1):
                raise MotionError(f"{tpl.id}: '{name}' en az {spec.get('min', 1)} öğe olmalı")
            if "item" in spec:
                fixed = []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    row = {}
                    for fname, fspec in spec["item"].items():
                        fv = it.get(fname)
                        if fv in (None, ""):
                            if fspec.get("required"):
                                break
                            continue
                        row[fname] = _coerce(fspec, fv)
                    else:
                        fixed.append(row)
                if len(fixed) < spec.get("min", 1):
                    raise MotionError(f"{tpl.id}: '{name}' öğeleri eksik")
                items = fixed
            else:
                items = [_coerce({"type": spec.get("item_type", "string"), "max": spec.get("item_max")}, x) for x in items]
            out[name] = items
        else:
            out[name] = _coerce(spec, val)
    return out


def _attach_icons(tpl: Template, params: dict, theme: str = "apple") -> tuple[dict, list[str]]:
    """İkon tipindeki alanlar için SVG'yi indirir, '<alan>_svg' olarak ekler. Kullanılan ikon kimliklerini döndürür."""
    used: list[str] = []
    mono = theme == "apple"  # sade tema: renkli emoji yerine tek renk çizgi ikon
    p = json.loads(json.dumps(params))
    for name, spec in tpl.spec.get("params", {}).items():
        if spec.get("type") == "icon" and p.get(name):
            svg, iid = icons.resolve(p[name], mono=mono)
            p[f"{name}_svg"] = svg
            if iid:
                used.append(iid)
        if spec.get("type") == "list" and "item" in spec:
            for it in p.get(name, []):
                for fname, fspec in spec["item"].items():
                    if fspec.get("type") == "icon" and it.get(fname):
                        svg, iid = icons.resolve(it[fname], mono=mono)
                        it[f"{fname}_svg"] = svg
                        if iid:
                            used.append(iid)
    return p, used


# --------------------------------------------------------------------------- render


def _electron() -> list[str]:
    """Electron komutu: kurulu sürümde uygulamanın kendisi, geliştirmede desktop/node_modules."""
    exe = os.environ.get("KURGU_ELECTRON")
    app_dir = os.environ.get("KURGU_ELECTRON_APP")
    if exe and Path(exe).exists():
        return [exe, app_dir] if app_dir else [exe]
    desk = project_root() / "desktop"
    cand = desk / "node_modules" / "electron" / "dist" / ("electron.exe" if os.name == "nt" else "electron")
    if cand.exists():
        return [str(cand), str(desk)]
    raise MotionError("Electron bulunamadı (desktop klasöründe 'npm install' gerekli).")


def compose_html(tpl: Template, params: dict, duration: float, out: Path, theme: str = "apple") -> Path:
    html = (tpl.dir / "index.html").read_text(encoding="utf-8")
    base = tpl.dir.resolve().as_uri() + "/"
    inject = (
        f'<base href="{base}">\n<script>window.KURGU_RENDER=true;'
        f"window.KURGU_DURATION={duration:.3f};window.KURGU_THEME={json.dumps(theme)};"
        f"window.KURGU_PARAMS={json.dumps(params, ensure_ascii=False)};</script>\n"
    )
    html = html.replace("<head>", "<head>\n" + inject, 1)
    out.write_text(html, encoding="utf-8")
    return out


def render(template_id: str, params: dict, duration: float, out_dir: Path, width: int, height: int,
           fps: int = 30, preview: bool = True, theme: str = "apple") -> dict:
    """Şablonu render eder. Dönen: {'mov': yol, 'webm': yol|None, 'icons': [...], 'params': doğrulanmış}."""
    tpls = list_templates()
    if template_id not in tpls:
        raise MotionError(f"Şablon yok: {template_id}. Mevcut: {', '.join(tpls)}")
    tpl = tpls[template_id]
    dflt, lo, hi = tpl.duration_bounds()
    duration = min(max(duration or dflt, lo), hi)
    params = validate_params(tpl, params)
    root = motion_root()
    # ikonlar önce çözülür ve anahtara girer: ikon seçimi değişince (ör. marka logosu filtresi) önbellek tazelenir
    full, used = _attach_icons(tpl, params, theme)
    key = hashlib.sha1(json.dumps([template_id, params, used, round(duration, 2), width, height, fps, theme,
                                   (tpl.dir / "index.html").stat().st_mtime, (root / "base.css").stat().st_mtime,
                                   (root / "runtime.js").stat().st_mtime], ensure_ascii=False, sort_keys=True)
                       .encode("utf-8")).hexdigest()[:14]
    out_dir.mkdir(parents=True, exist_ok=True)
    mov = out_dir / f"mg_{template_id}_{key}.mov"
    webm = out_dir / f"mg_{template_id}_{key}.webm"
    if mov.exists() and (webm.exists() or not preview):
        return {"mov": mov, "webm": webm if webm.exists() else None, "params": params, "duration": duration,
                "icons": used, "layout": tpl.layout}

    work = out_dir / f"_work_{key}"
    shutil.rmtree(work, ignore_errors=True)
    frames = work / "frames"
    frames.mkdir(parents=True)
    html = compose_html(tpl, full, duration, work / "index.html", theme)
    job = work / "job.json"
    job.write_text(json.dumps({"html": str(html), "width": width, "height": height, "fps": fps,
                               "duration": duration, "out_dir": str(frames)}), encoding="utf-8")
    cmd = _electron() + ["--render-motion", str(job)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if proc.returncode != 0 or not (frames / "done.json").exists():
        raise MotionError(f"Animasyon render edilemedi ({proc.returncode}): {(proc.stderr or proc.stdout)[-600:]}")

    pattern = str(frames / "%05d.png")
    ff.run_ffmpeg(["-framerate", str(fps), "-i", pattern, "-c:v", "png", "-pix_fmt", "rgba", str(mov)])
    if preview:  # arayüz önizlemesi: düşük çözünürlüklü, alfa kanallı VP9
        pw = 360 if width < height else 640
        ff.run_ffmpeg(["-framerate", str(fps), "-i", pattern, "-vf", f"scale={pw}:-2",
                       "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "36",
                       "-deadline", "realtime", "-cpu-used", "8", "-an", str(webm)])
    shutil.rmtree(work, ignore_errors=True)
    return {"mov": mov, "webm": webm if preview else None, "params": params, "duration": duration,
            "icons": used, "layout": tpl.layout}


# --------------------------------------------------------------------------- timeline entegrasyonu


def motion_span(item, tl, words: dict, tm) -> tuple[float, float] | None:
    """Animasyonun çıktıdaki (başlangıç, bitiş) zamanı: kelimede başlar, şablon süresi kadar sürer."""
    tpl = list_templates().get(item.template)
    try:
        a, _ = item.anchor.resolve(words)
    except ValueError:
        return None
    s = tm.to_out(a, "next")
    if s is None:
        return None
    if item.template.startswith("gs:"):  # greenscreen klibi: kendi süresi kadar
        from .greenscreen import get_clip

        clip = get_clip(item)
        cd = clip.duration if clip else 3.0
        d = min(item.duration or cd, cd)
    else:
        dflt, lo, hi = tpl.duration_bounds() if tpl else (3.5, 2.0, 6.0)
        d = min(max(item.duration or dflt, lo), hi)
    return s, min(s + d, tm.duration)


def _link(src: Path, out_dir: Path) -> Path:
    """Kütüphane dosyasını proje klasörüne bağlar (aynı diskte sabit bağlantı, değilse kopya)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / src.name
    if not dst.exists():
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    return dst


def fill_motions(project, tl, progress=None, ids: list[str] | None = None, only_missing: bool = True) -> dict:
    """Timeline'daki animasyonları render eder (önbellekli). Dönen: {'filled': [...], 'failed': {...}}."""
    try:
        words = project.transcript().word_map()
    except FileNotFoundError:
        words = {}
    tm = tl.time_map()
    out_dir = project.assets_dir / "motion"
    done, failed = [], {}
    todo = [m for m in tl.motions if ids is None or m.id in ids]
    for k, m in enumerate(todo):
        if only_missing and m.asset and project.abs(m.asset).exists() and m.theme == tl.theme:
            continue
        span = motion_span(m, tl, words, tm)
        if not span:
            failed[m.id] = "kesilmiş bölgede"
            continue
        if progress:
            progress("assets", k / max(len(todo), 1), f"animasyon {m.id}: {m.template}")
        if m.template.startswith("gs:"):
            from .greenscreen import get_clip, keyed

            clip = get_clip(m)
            if not clip:
                failed[m.id] = "greenscreen klibi kütüphanede yok"
                continue
            try:
                r = keyed(clip)
            except Exception as e:
                failed[m.id] = str(e)[:300]
                continue
            m.asset = project.rel(_link(r["mov"], out_dir))
            m.preview = project.rel(_link(r["webm"], out_dir)) if r.get("webm") else None
            m.layout, m.theme = "card", tl.theme
            done.append(m.id)
            continue
        try:
            r = render(m.template, m.params, span[1] - span[0], out_dir, tl.output.width, tl.output.height,
                       int(round(tl.output.fps)), theme=tl.theme)
        except Exception as e:  # tek animasyon hatası diğerlerini durdurmasın
            failed[m.id] = str(e)[:300]
            continue
        m.asset = project.rel(r["mov"])
        m.preview = project.rel(r["webm"]) if r.get("webm") else None
        m.params = r["params"]
        m.layout = r["layout"]
        m.icons = r["icons"] or m.icons
        m.theme = tl.theme
        done.append(m.id)
    if progress:
        progress("assets", 1.0, f"{len(done)} animasyon hazır")
    return {"filled": done, "failed": failed}


# Kart yuvalarının yaklaşık dikey aralıkları (base.css .pos-*; kart ~25vh yüksekliğinde)
CARD_SLOTS = {"top": (0.09, 0.36), "center": (0.32, 0.60), "bottom": (0.43, 0.70)}


def best_card_position(band: dict | None, allowed: list[str]) -> str | None:
    """Yüz bandıyla en az çakışan kart yuvası (eşitlikte üst tercih edilir)."""
    if not band or not band.get("found"):
        return None
    ft, fb = band["top"] - 0.02, band["bottom"] + 0.02
    opts = [p for p in ("top", "center", "bottom") if p in allowed]
    if not opts:
        return None

    def overlap(p):
        a, b = CARD_SLOTS[p]
        return max(0.0, min(b, fb) - max(a, ft))

    return min(opts, key=lambda p: (round(overlap(p), 2), opts.index(p)))


def place_cards_off_face(project, tl) -> dict:
    """Kart düzenindeki animasyonların `position` parametresini yüzü kapatmayacak yuvaya çeker."""
    band = project.load_analysis("face_band")
    if band is None:
        try:
            from ..analyze.faces import face_band

            band = face_band(project.proxy if project.proxy.exists() else project.source)
        except Exception as e:
            band = {"found": 0, "error": str(e)[:200]}
        project.save_analysis("face_band", band)
    tpls = list_templates()
    moved = {}
    for m in tl.motions:
        t = tpls.get(m.template)
        if not t or m.layout == "full" or t.layout == "full" or m.template.startswith("gs:"):
            continue
        spec = (t.spec.get("params") or {}).get("position") or {}
        pos = best_card_position(band, list(spec.get("values") or []))
        if pos and m.params.get("position") != pos:
            m.params = {**m.params, "position": pos}
            m.asset = None  # yeniden render
            moved[m.id] = pos
        # uzun liste kartı üst yuvadan aşağı uzayıp yüze iner: sığacak kadar madde bırakılır (en az 2)
        if m.template == "bullet_list" and (m.params.get("position") or "top") == "top" and band.get("found"):
            items = list(m.params.get("items") or [])
            fit = max(2, int((band["top"] - 0.02 - 0.19) / 0.075))
            if len(items) > fit:
                m.params = {**m.params, "items": items[:fit]}
                m.asset = None
                moved[m.id] = f"top, {fit} madde"
    # ekran yazıları (hook, pop): altyazı bölgesine (alt) inmeden üst veya orta, yüzle en az çakışan
    for t in tl.texts:
        if t.style not in ("hook", "pop"):
            continue
        pos = best_text_position(band)
        if pos and t.position != pos:
            t.position = pos
            moved[t.id] = pos
    return {"band": band, "moved": moved}


# ekran yazılarının yaklaşık dikey aralıkları (captions_ass: hook ve pop yazıları)
TEXT_SLOTS = {"top": (0.04, 0.30), "center": (0.38, 0.54)}


def best_text_position(band: dict | None) -> str | None:
    if not band or not band.get("found"):
        return None
    ft, fb = band["top"] - 0.02, band["bottom"] + 0.02

    def overlap(p):
        a, b = TEXT_SLOTS[p]
        return max(0.0, min(b, fb) - max(a, ft))

    return min(("top", "center"), key=lambda p: (round(overlap(p), 2), p != "top"))
