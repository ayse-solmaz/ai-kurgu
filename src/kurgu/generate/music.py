"""Arka plan müziği: üretim (ACE-Step 1.5) -> yerel kütüphane -> prosedürel yedek.

1) ACE-Step 1.5 (MIT, açık ağırlık): metinden enstrümantal müzik. Kendi sanal ortamında alt süreç olarak çalışır
   (tools/ACE-Step-1.5, kurulum: scripts/setup_music.ps1). Intel iGPU'da (XPU) turbo modeli 8 adımda üretir.
2) Kütüphane: assets/music/<mood>/*.mp3|wav|ogg  (mood: calm, upbeat, inspiring, dramatic, corporate)
   Lisansını bildiğiniz (ör. CC0) parçaları buraya koyun.
3) Prosedürel: hiçbiri yoksa ruh haline göre akor dizisiyle yumuşak bir fon (numpy ile sentez).
Müzik konuşma sırasında otomatik kısılır (render.py: sidechain ducking).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..core.config import get_config, project_root

EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


# --------------------------------------------------------------------------- kütüphane


def library() -> Path:
    return project_root() / "assets" / "music"


def pick(mood: str | None, seed: str = "") -> Path | None:
    root = library()
    if not root.exists():
        return None
    cands = []
    if mood and (root / mood).exists():
        cands = [p for p in (root / mood).iterdir() if p.suffix.lower() in EXTS]
    if not cands:
        cands = [p for p in root.rglob("*") if p.suffix.lower() in EXTS and "_generated" not in p.parts]
    if not cands:
        return None
    cands.sort()
    idx = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % len(cands)
    return cands[idx]


def fill_music(project, tl) -> str | None:
    """Render öncesi: müzik isteniyor ama dosyası yoksa kütüphaneden bağlar."""
    if not tl.music or (tl.music.asset and project.abs(tl.music.asset).exists()):
        return None
    src = pick(tl.music.mood, project.id)
    if not src:
        return None
    dst = project.assets_dir / f"music_{src.name}"
    if not dst.exists():
        shutil.copy2(src, dst)
    tl.music.asset = project.rel(dst)
    return tl.music.asset


# --------------------------------------------------------------------------- üretim


@dataclass
class MusicSpec:
    caption: str
    bpm: int
    duration: float
    mood: str
    seed: int = 42

    def key(self) -> str:
        return hashlib.sha1(json.dumps([self.caption, self.bpm, round(self.duration), self.seed]).encode()).hexdigest()[:12]


MOOD_WORDS = {
    "calm": "calm, warm, gentle, relaxed",
    "upbeat": "upbeat, bright, positive, energetic",
    "inspiring": "inspiring, hopeful, uplifting, light",
    "dramatic": "cinematic, tense, emotional, building",
    "corporate": "clean, modern, confident, minimal",
    "playful": "playful, quirky, light, bouncy",
    "mysterious": "mysterious, curious, atmospheric, subtle",
}


_EN_MUSIC = {"piano", "guitar", "synth", "pad", "pads", "drums", "ambient", "electronic", "pop", "cinematic", "orchestral",
             "lo-fi", "lofi", "hip", "hop", "strings", "bass", "acoustic", "jazz", "rock", "folk", "beat", "beats",
             "percussion", "chill", "house", "corporate", "upbeat", "soft", "warm", "light", "cello", "violin", "bells",
             "marimba", "plucks", "pluck", "clarinet", "flute", "orchestra", "downtempo", "indie", "chillhop", "trap"}


def _english(text: str) -> str:
    """Müzik modeli İngilizce açıklama ister: İngilizce müzik terimi içermiyorsa LLM ile çevirir."""
    text = text.strip()
    if not text:
        return ""
    if any(w in _EN_MUSIC for w in text.lower().replace(",", " ").split()):
        return text
    try:
        from pydantic import BaseModel

        from ..agent.llm import chat_json

        class En(BaseModel):
            english: str

        return chat_json([{"role": "user", "content": f"Translate to English, keep it short: {text}"}], En,
                         max_tokens=80, retries=0).english
    except Exception:
        return ""


def spec_for(mood: str, duration: float, music: dict | None = None, seed: str = "") -> MusicSpec:
    """Brif/profil önerisinden ACE-Step açıklaması (İngilizce caption) kurar."""
    m = music or {}
    mood = m.get("mood") or mood or "inspiring"
    genre = _english(str(m.get("genre") or "")) or {
        "calm": "ambient lo-fi", "upbeat": "indie pop electronic", "inspiring": "ambient electronic",
        "dramatic": "cinematic orchestral", "corporate": "modern corporate pop",
    }.get(mood, "ambient electronic")
    inst = _english(str(m.get("instruments") or "")) or "soft piano, warm synth pads, light percussion"
    bpm = int(m.get("bpm") or {"calm": 80, "upbeat": 118, "dramatic": 90, "corporate": 105}.get(mood, 96))
    bpm = min(max(bpm, 60), 140)
    caption = (f"{genre}, {MOOD_WORDS.get(mood, mood)}, {inst}, instrumental background music for a narrated video, "
               "no vocals, steady, unobtrusive, clean mix")
    s = int(hashlib.md5(seed.encode()).hexdigest(), 16) % 100000 if seed else 42
    return MusicSpec(caption=caption, bpm=bpm, duration=duration, mood=mood, seed=s)


def ace_dir() -> Path:
    return project_root() / "tools" / "ACE-Step-1.5"


def ace_python() -> Path | None:
    for v in ("venv_xpu", "venv", ".venv"):
        p = ace_dir() / v / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if p.exists():
            return p
    return None


def ace_status() -> dict:
    ck = ace_dir() / "checkpoints"
    ready = (ck / "acestep-v15-turbo" / "model.safetensors").exists() and (ck / "vae").exists()
    return {"installed": ace_python() is not None, "model": ready}


def generate_ace(spec: MusicSpec, out_dir: Path, timeout: int = 2400) -> Path:
    py = ace_python()
    st = ace_status()
    if not py or not st["model"]:
        raise RuntimeError("ACE-Step kurulu değil (scripts/setup_music.ps1).")
    out_dir.mkdir(parents=True, exist_ok=True)
    job = out_dir / f"ace_{spec.key()}.json"
    dev = os.environ.get("KURGU_MUSIC_DEVICE") or "auto"
    job.write_text(json.dumps({"ace_dir": str(ace_dir()), "out_dir": str(out_dir), "caption": spec.caption,
                               "bpm": spec.bpm, "duration": round(spec.duration, 1), "seed": spec.seed, "steps": 8,
                               "device": dev}), encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ace_dir()), "TOKENIZERS_PARALLELISM": "false",
           # ACE-Step'in Intel XPU başlatıcılarının ayarları (GPU doğruluk testini geçerse kullanılır)
           "SYCL_CACHE_PERSISTENT": "1", "SYCL_PI_LEVEL_ZERO_USE_IMMEDIATE_COMMANDLISTS": "1",
           "TORCH_COMPILE_BACKEND": "eager", "TORCHAUDIO_USE_BACKEND": "ffmpeg"}
    p = subprocess.run([str(py), str(project_root() / "scripts" / "ace_music.py"), str(job)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env, cwd=str(ace_dir()))
    last = next((ln for ln in reversed(p.stdout.strip().splitlines()) if ln.startswith("{")), "")
    try:
        res = json.loads(last)
    except json.JSONDecodeError:
        raise RuntimeError(f"ACE-Step çıktısı okunamadı: {(p.stderr or p.stdout)[-500:]}")
    if not res.get("ok") or not res.get("path"):
        raise RuntimeError(f"ACE-Step: {res.get('error')}")
    dst = out_dir / f"music_ace_{spec.key()}.wav"
    shutil.move(res["path"], dst)
    job.unlink(missing_ok=True)
    return dst


# ---- prosedürel yedek: yumuşak akor fonu

_PROG = {  # yarım ton cinsinden kökler ve majör/minör
    "calm": [(0, "M"), (9, "m"), (5, "M"), (7, "M")],
    "inspiring": [(0, "M"), (7, "M"), (9, "m"), (5, "M")],
    "upbeat": [(0, "M"), (5, "M"), (9, "m"), (7, "M")],
    "dramatic": [(9, "m"), (5, "M"), (0, "M"), (7, "M")],
    "corporate": [(0, "M"), (9, "m"), (5, "M"), (7, "M")],
}


def generate_procedural(spec: MusicSpec, out: Path, sr: int = 44100) -> Path:
    rng = np.random.default_rng(spec.seed)
    beat = 60.0 / spec.bpm
    bar = beat * 4
    n = int(spec.duration * sr)
    t = np.arange(n) / sr
    base = 220.0 * 2 ** (-9 / 12)  # C3 civarı
    prog = _PROG.get(spec.mood, _PROG["inspiring"])
    y = np.zeros(n, np.float32)
    for k in range(int(spec.duration / (bar * 2)) + 1):
        root, q = prog[k % len(prog)]
        a, b = int(k * bar * 2 * sr), min(int((k + 1) * bar * 2 * sr), n)
        if a >= n:
            break
        tt = t[a:b] - t[a]
        env = np.minimum(1, tt / 1.2) * np.minimum(1, (tt[-1] - tt + 1e-3) / 1.0)
        for semi in (0, 4 if q == "M" else 3, 7, 12):
            f = base * 2 ** ((root + semi) / 12)
            det = 1 + rng.uniform(-0.002, 0.002)
            y[a:b] += 0.06 * env * (np.sin(2 * np.pi * f * det * tt) + 0.3 * np.sin(2 * np.pi * 2 * f * tt))
        f_bass = base / 2 * 2 ** (root / 12)
        y[a:b] += 0.08 * env * np.sin(2 * np.pi * f_bass * tt)
    if spec.mood in ("upbeat", "corporate", "inspiring"):  # hafif ritim: yumuşak tık + shaker
        for i in range(int(spec.duration / beat)):
            s = int(i * beat * sr)
            m = min(int(0.08 * sr), n - s)
            if m <= 0:
                break
            e = np.exp(-np.arange(m) / (0.012 * sr))
            y[s:s + m] += (0.05 if i % 2 else 0.08) * e * rng.standard_normal(m).astype(np.float32) * 0.5
    y = np.convolve(y, np.ones(24) / 24, "same")  # yumuşatma (alçak geçiren)
    fade = int(2.0 * sr)
    y[:fade] *= np.linspace(0, 1, fade)
    y[-fade:] *= np.linspace(1, 0, fade)
    y = y / (np.abs(y).max() + 1e-9) * 0.7
    st = np.stack([y, np.roll(y, int(0.012 * sr))], 1)  # hafif stereo genişlik
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((st * 32767).astype("<i2").tobytes())
    return out


ACE_MAX_SECONDS = 60.0  # üretim süresi uzunlukla orantısız artar (CPU'da 130 sn ~15 dk); uzun videoda döngü


def loop_to(src: Path, total: float, out: Path, xfade: float = 3.0) -> Path:
    """Parçayı yumuşak geçişlerle (crossfade) videonun süresine uzatır."""
    from ..core import ffmpeg as ff

    try:
        d = float(ff.probe(src)["format"]["duration"])
    except Exception:
        d = 0.0
    if d <= 0 or d >= total:
        return src
    n = int(np.ceil((total - xfade) / (d - xfade))) + 1
    args: list[str] = []
    for _ in range(n):
        args += ["-i", str(src)]
    chain, cur = [], "[0:a]"
    for i in range(1, n):
        nxt = f"[x{i}]"
        chain.append(f"{cur}[{i}:a]acrossfade=d={xfade}:c1=tri:c2=tri{nxt}")
        cur = nxt
    chain.append(f"{cur}atrim=duration={total:.2f},afade=t=out:st={max(total - 2.5, 0):.2f}:d=2.5[out]")
    ff.run_ffmpeg([*args, "-filter_complex", ";".join(chain), "-map", "[out]", "-c:a", "pcm_s16le", str(out)])
    return out


def score_timeline(project, tl, brief, profile: dict | None = None, progress=None) -> dict:
    """Besteci: videoya fon müziği üretir/seçer ve timeline'a bağlar. Dönen: rapor."""
    from ..core.timeline import Music

    rep = progress or (lambda *a: None)
    mood = getattr(brief, "music_mood", None) or "inspiring"
    total = tl.output_duration() + 1.5
    dur = min(total, ACE_MAX_SECONDS)
    spec = spec_for(mood, dur, (profile or {}).get("music"), seed=project.id)
    out_dir = project.assets_dir / "music"
    cached = out_dir / f"music_ace_{spec.key()}.wav"
    report = {"caption": spec.caption, "bpm": spec.bpm, "seconds": round(dur, 1)}
    path = cached if cached.exists() else None
    if path:
        report["provider"] = "ace-step (önbellek)"
    elif ace_status()["model"]:
        rep("music", 0.1, f"Besteci {dur:.0f} sn müzik üretiyor (ACE-Step)")
        try:
            path = generate_ace(spec, out_dir)
            report["provider"] = "ace-step"
        except Exception as e:
            report["ace_error"] = str(e)[:300]
    if path and total > dur + 1:
        path = loop_to(path, total, out_dir / f"{path.stem}_loop{total:.0f}.wav")
        report["looped_to"] = round(total, 1)
    if not path:
        lib = pick(mood, project.id)
        if lib:
            path = out_dir / f"music_{lib.name}"
            out_dir.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                shutil.copy2(lib, path)
            report["provider"] = "kütüphane"
    if not path:
        path = generate_procedural(spec, out_dir / f"music_proc_{spec.key()}.wav")
        report["provider"] = "prosedürel"
    tl.music = Music(asset=project.rel(path), mood=mood, volume_db=-21.0 if report["provider"].startswith("ace") else -24.0)
    rep("music", 1.0, f"müzik: {report['provider']}")
    return report
