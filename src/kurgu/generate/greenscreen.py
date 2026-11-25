"""Greenscreen kütüphanesi ve otomatik yerleştirme.

assets/greenscreen/<ad>.(mp4|mov|webm|mkv) + isteğe bağlı <ad>.json yan dosya:
  {
    "name": "Abone ol butonu",
    "triggers": ["abone ol", "subscribe"],   # konuşmada geçince otomatik eklenir
    "position": "lower",                    # lower | center | top | bottom-right | bottom-left | full
    "scale": 0.7,                           # çıktı genişliğine oranı (full'da yok sayılır)
    "key_color": "auto",                    # auto | "#00FF00" | "#0000FF"
    "similarity": 0.16, "blend": 0.08,      # chromakey eşik ve yumuşaklık
    "license": "CC0", "source": "https://..."
  }

Klip ilk kullanımda ffmpeg chromakey + despill ile alfa kanallı .mov'a çevrilir ve önbelleğe alınır
(assets/greenscreen/_keyed). Timeline'da MotionItem(template="gs:<ad>") olarak yer alır.

Ayrıca motion/templates içindeki "triggers" alanı olan şablonlar (ör. cta: abone ol / beğen / takip et)
aynı tetikleyici sistemiyle otomatik eklenir.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..core import ffmpeg as ff
from ..core.config import project_root
from ..core.timeline import Anchor, MotionItem, Timeline, Transcript

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
PREFIX = "gs:"


@dataclass
class GreenClip:
    id: str
    path: Path
    spec: dict

    @property
    def name(self) -> str:
        return self.spec.get("name", self.id)

    @property
    def duration(self) -> float:
        d = self.spec.get("_duration")
        if d is None:
            try:
                d = float(ff.probe(self.path)["format"]["duration"])
            except Exception:
                d = 3.0
            self.spec["_duration"] = d
        return float(d)


def library_dir() -> Path:
    d = project_root() / "assets" / "greenscreen"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_clips() -> dict[str, GreenClip]:
    out: dict[str, GreenClip] = {}
    for f in sorted(library_dir().iterdir()):
        if f.suffix.lower() not in VIDEO_EXT or f.name.startswith("_"):
            continue
        side = f.with_suffix(".json")
        spec = {}
        if side.exists():
            try:
                spec = json.loads(side.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                spec = {}
        out[f.stem] = GreenClip(f.stem, f, spec)
    return out


def get_clip(item_or_id) -> GreenClip | None:
    cid = item_or_id.template if isinstance(item_or_id, MotionItem) else str(item_or_id)
    cid = cid[len(PREFIX):] if cid.startswith(PREFIX) else cid
    return list_clips().get(cid)


def is_greenscreen(item: MotionItem) -> bool:
    return item.template.startswith(PREFIX)


# --------------------------------------------------------------------------- anahtarlama


def detect_key_color(path: Path) -> str:
    """Kenar piksellerinin medyanından perde rengini bulur (yeşil/mavi)."""
    w, h = 64, 36
    raw = subprocess.run([ff.find_tool("ffmpeg"), "-v", "error", "-ss", "0.3", "-i", str(path), "-frames:v", "1",
                          "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, env=ff.tool_env()).stdout
    if len(raw) < w * h * 3:
        return "#00B140"
    img = np.frombuffer(raw, np.uint8)[: w * h * 3].reshape(h, w, 3).astype(int)
    border = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    r, g, b = np.median(border, axis=0).astype(int)
    if g < max(r, b) + 25 and b < max(r, g) + 25:  # belirgin perde yok: standart yeşil
        return "#00B140"
    return f"#{r:02X}{g:02X}{b:02X}"


def keyed(clip: GreenClip, preview: bool = True) -> dict:
    """Perde rengini siler; alfa kanallı .mov (+ önizleme .webm) döndürür (önbellekli)."""
    s = clip.spec
    color = s.get("key_color", "auto")
    if not color or color == "auto":
        color = s.get("_key") or detect_key_color(clip.path)
        s["_key"] = color
    sim, blend = float(s.get("similarity", 0.16)), float(s.get("blend", 0.08))
    st = clip.path.stat()
    key = hashlib.sha1(json.dumps([clip.id, st.st_size, st.st_mtime, color, sim, blend]).encode()).hexdigest()[:12]
    cache = library_dir() / "_keyed"
    cache.mkdir(exist_ok=True)
    mov, webm = cache / f"{clip.id}_{key}.mov", cache / f"{clip.id}_{key}.webm"
    hexc = color.lstrip("#")
    r, g, b = int(hexc[0:2], 16), int(hexc[2:4], 16), int(hexc[4:6], 16)
    spill = "blue" if b > g else "green"
    chain = f"chromakey=0x{hexc}:{sim}:{blend},despill=type={spill}:mix=0.5:expand=0.1,format=rgba"
    if not mov.exists():
        ff.run_ffmpeg(["-i", str(clip.path), "-vf", chain, "-an", "-c:v", "png", "-pix_fmt", "rgba", str(mov)])
    if preview and not webm.exists():
        ff.run_ffmpeg(["-i", str(mov), "-vf", "scale=360:-2", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                       "-b:v", "0", "-crf", "38", "-deadline", "realtime", "-cpu-used", "8", "-an", str(webm)])
    return {"mov": mov, "webm": webm if webm.exists() else None, "key_color": color}


def add_clip(src: Path, name: str | None = None, triggers: list[str] | None = None, position: str = "lower",
             scale: float = 0.7, license: str | None = None, source: str | None = None) -> GreenClip:
    """Kütüphaneye klip ekler (kopyalar + yan dosya yazar)."""
    cid = re.sub(r"[^a-z0-9_-]+", "-", (name or src.stem).lower()).strip("-") or "klip"
    dst = library_dir() / f"{cid}{src.suffix.lower()}"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    spec = {"name": name or src.stem, "triggers": triggers or [], "position": position, "scale": scale,
            "key_color": "auto", "similarity": 0.16, "blend": 0.08}
    if license:
        spec["license"] = license
    if source:
        spec["source"] = source
    dst.with_suffix(".json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return GreenClip(cid, dst, spec)


def overlay_geometry(clip: GreenClip, W: int, H: int) -> tuple[str, str]:
    """(ölçek filtresi, overlay konumu) — render grafiği için."""
    pos = clip.spec.get("position", "lower")
    if pos == "full":
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", "0:0"
    cw = int(W * float(clip.spec.get("scale", 0.7))) // 2 * 2
    m = int(W * 0.05)
    xy = {
        "lower": f"(W-w)/2:H*0.58-h/2",
        "center": "(W-w)/2:(H-h)/2",
        "top": f"(W-w)/2:H*0.1",
        "bottom-right": f"W-w-{m}:H*0.62-h/2",
        "bottom-left": f"{m}:H*0.62-h/2",
    }.get(pos, "(W-w)/2:H*0.58-h/2")
    return f"scale={cw}:-2", xy


# --------------------------------------------------------------------------- otomatik yerleştirme

_TR = str.maketrans("İIÇĞÖŞÜ", "iıçğöşü")


def _norm(s: str) -> str:
    return re.sub(r"[^\wçğıöşü ]", "", s.translate(_TR).lower())


def _phrase_hits(words: list, phrase: str) -> list[int]:
    """İfadenin başladığı kelime indeksleri. Her kelime ön ek olarak eşleşir (Türkçe ekler: 'beğen' ~ 'beğenmeyi')."""
    toks = _norm(phrase).split()
    if not toks:
        return []
    norm = [_norm(w.text) for w in words]
    hits = []
    for i in range(len(words) - len(toks) + 1):
        if all(norm[i + k].startswith(t) for k, t in enumerate(toks)):
            hits.append(i)
    return hits


def trigger_sources() -> list[dict]:
    """Tetikleyici listesi: kullanıcı greenscreen klipleri önce (aynı tetikleyicide önceliklidir), sonra şablonlar."""
    from .motion_gfx import list_templates

    out = []
    for c in list_clips().values():
        for ph in c.spec.get("triggers", []):
            out.append({"template": PREFIX + c.id, "params": {}, "phrase": ph, "group": _norm(ph).split()[0][:5]})
    for t in list_templates().values():
        for variant, phrases in (t.spec.get("triggers") or {}).items():
            for ph in phrases:
                out.append({"template": t.id, "params": {"variant": variant}, "phrase": ph,
                            "group": _norm(ph).split()[0][:5]})
    return out


def auto_place(tl: Timeline, tr: Transcript, max_items: int = 3, min_gap: float = 15.0) -> list[MotionItem]:
    """Konuşmadaki çağrılara ('abone olun', 'beğenmeyi unutmayın' ...) greenscreen/CTA ekler.
    Kesilmiş kelimeler, mevcut animasyonlarla çakışan anlar ve birbirine çok yakın tekrarlar atlanır."""
    from .motion_gfx import motion_span

    tm = tl.time_map()
    words = [w for w in tr.words if tm.to_out((w.start + w.end) / 2, "strict") is not None]
    wmap = tr.word_map()
    busy = [sp for m in tl.motions for sp in [motion_span(m, tl, wmap, tm)] if sp]
    taken: set[str] = set()
    placed: list[MotionItem] = []
    starts: list[float] = []
    hits = []
    for src in trigger_sources():
        for i in _phrase_hits(words, src["phrase"]):
            hits.append((words[i], src))
    # aynı anda hem kullanıcı klibi hem şablon eşleşirse kullanıcı klibi önce gelir
    hits.sort(key=lambda h: (h[0].start, not h[1]["template"].startswith(PREFIX)))
    n = len([m for m in tl.motions if m.id.startswith("gs")])
    for w, src in hits:
        if len(placed) >= max_items:
            break
        key = src["template"] + json.dumps(src["params"], sort_keys=True)
        if key in taken:  # aynı çağrı (ör. abone ol) bir kez
            continue
        item = MotionItem(id=f"gs{n + len(placed) + 1}", anchor=Anchor(word_start=w.id), template=src["template"],
                          params=dict(src["params"]))
        sp = motion_span(item, tl, wmap, tm)
        if not sp or any(a < sp[1] + 0.3 and sp[0] < b + 0.3 for a, b in busy):
            continue
        if any(abs(sp[0] - s) < min_gap for s in starts):
            continue
        placed.append(item)
        busy.append(sp)
        starts.append(sp[0])
        taken.add(key)
    tl.motions.extend(placed)
    return placed


def fetch_stock(query: str, triggers: list[str] | None = None, position: str = "lower", scale: float = 0.7) -> GreenClip:
    """Pixabay / Pexels'ten greenscreen klip indirip kütüphaneye ekler (ücretsiz API anahtarı gerekir).
    Her iki sitenin içerikleri ticari kullanılabilir; kaynak bağlantısı yan dosyaya yazılır."""
    import os

    import httpx

    from ..core.config import get_config
    from .stock import _download, _load_dotenv

    _load_dotenv()
    a = get_config().assets
    q = f"green screen {query}".strip()
    tmp = library_dir() / "_dl"
    tmp.mkdir(exist_ok=True)
    key = os.environ.get(a.pixabay_api_key_env)
    if key:
        r = httpx.get("https://pixabay.com/api/videos/", params={"key": key, "q": q[:100], "per_page": 10,
                                                                  "safesearch": "true"}, timeout=30)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if hits:
            hit = max(hits, key=lambda h: h.get("likes", 0))
            v = hit["videos"].get("medium") or hit["videos"].get("large")
            raw = _download(v["url"], tmp / f"pixabay_{hit['id']}.mp4")
            return add_clip(raw, name=f"{query or 'greenscreen'}-{hit['id']}", triggers=triggers, position=position,
                            scale=scale, license="Pixabay Content License", source=hit.get("pageURL"))
    key = os.environ.get(a.pexels_api_key_env)
    if key:
        r = httpx.get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                      params={"query": q, "per_page": 10}, timeout=30)
        r.raise_for_status()
        vids = r.json().get("videos", [])
        if vids:
            v = vids[0]
            f = min((x for x in v["video_files"] if x.get("width")), key=lambda x: abs(x["width"] - 1280))
            raw = _download(f["link"], tmp / f"pexels_{v['id']}.mp4")
            return add_clip(raw, name=f"{query or 'greenscreen'}-{v['id']}", triggers=triggers, position=position,
                            scale=scale, license="Pexels License", source=v.get("url"))
    raise RuntimeError("Sonuç yok ya da API anahtarı eksik (.env: PIXABAY_API_KEY / PEXELS_API_KEY).")
