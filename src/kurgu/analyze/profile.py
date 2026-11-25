"""Video profili: "bu nasıl bir video?"

Birkaç ölçüm aracı videonun sesini, konuşmasını ve görüntüsünü sayılarla çıkarır; ardından LLM bu ölçümleri,
sahne tarifini (Qwen3-VL) ve transkripti okuyarak videoyu betimler (tür, konu, kitle, ton, enerji,
önerilen müzik ve tema). Senarist bu profile bakarak kurgu senaryosunu yazar.
"""

from __future__ import annotations

import re
import subprocess
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from ..core import ffmpeg as ff
from ..core.project_store import Project
from ..core.timeline import Transcript

_NUM = re.compile(r"\d|%|\b(iki|üç|dört|beş|altı|yedi|sekiz|dokuz|yüz|bin|milyon|milyar|yüzde|derece)\b", re.I)


# --------------------------------------------------------------------------- ölçümler


def speech_stats(tr: Transcript, vad: list, duration: float) -> dict:
    words = tr.words
    speech = sum(b - a for a, b in vad) if vad else sum(w.end - w.start for w in words)
    gaps = [b.start - a.end for a, b in zip(words, words[1:])]
    long_p = [g for g in gaps if g > 1.0]
    sents = tr.sentences
    return {
        "words": len(words),
        "wpm": round(len(words) / max(speech / 60, 1e-6)) if words else 0,
        "speech_ratio": round(min(speech / max(duration, 1e-6), 1.0), 2),
        "long_pauses": len(long_p),
        "avg_pause": round(float(np.mean([g for g in gaps if g > 0.15])) if gaps else 0.0, 2),
        "fillers": sum(w.is_filler for w in words),
        "stutters": sum(w.is_stutter for w in words),
        "retake_words": sum(w.is_retake for w in words),
        "questions": sum(s.text.strip().endswith("?") for s in sents),
        "numeric_sentences": [s.id for s in sents if _NUM.search(s.text)][:20],
        "language": tr.language,
    }


def audio_stats(project: Project) -> dict:
    out: dict = {}
    try:
        from ..render.render import _measure_loudness

        m = _measure_loudness(project.audio, -14, -1.5)
        if m:
            out["lufs"] = round(float(m["input_i"]), 1)
            out["loudness_range"] = round(float(m["input_lra"]), 1)
            out["true_peak"] = round(float(m["input_tp"]), 1)
    except Exception:
        pass
    try:
        from .audio_io import SR, load_wav16k

        x = load_wav16k(project.audio)
        hop = SR // 2
        rms = np.sqrt(np.convolve(x.astype(np.float32) ** 2, np.ones(hop) / hop, "valid")[::hop] + 1e-12)
        active = rms[rms > np.percentile(rms, 30)]
        out["energy_variation"] = round(float(np.std(active) / (np.mean(active) + 1e-9)), 2) if len(active) else 0.0
    except Exception:
        pass
    return out


def visual_stats(project: Project, duration: float) -> dict:
    """Sahne değişimi sıklığı, parlaklık, renk canlılığı ve baskın renkler (düşük çözünürlükte, hızlı)."""
    src = project.proxy if project.proxy.exists() else project.source
    out: dict = {}
    exe = ff.find_tool("ffmpeg")
    try:
        p = subprocess.run([exe, "-hide_banner", "-i", str(src), "-an", "-vf",
                            "fps=6,scale=160:-2,select='gt(scene,0.32)',showinfo", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=ff.tool_env(),
                           timeout=600)
        cuts = len(re.findall(r"pts_time:", p.stderr))
        out["scene_changes_per_min"] = round(cuts / max(duration / 60, 1e-6), 1)
    except Exception:
        pass
    try:
        n = 8
        w, h = 48, 27
        times = [duration * (i + 0.5) / n for i in range(n)]
        px = []
        for t in times:
            raw = subprocess.run([exe, "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-vf",
                                  f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 capture_output=True, env=ff.tool_env(), timeout=60).stdout
            if len(raw) >= w * h * 3:
                px.append(np.frombuffer(raw, np.uint8)[: w * h * 3].reshape(-1, 3))
        if px:
            a = np.concatenate(px).astype(np.float32)
            out["brightness"] = round(float(a.mean() / 255), 2)
            rg, yb = a[:, 0] - a[:, 1], 0.5 * (a[:, 0] + a[:, 1]) - a[:, 2]
            out["colorfulness"] = round(float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)) / 100, 2)
            out["palette"] = _palette(a)
    except Exception:
        pass
    faces = project.load_analysis("faces")
    if faces:
        out["face_tracked"] = True
    return out


def _palette(a: np.ndarray, k: int = 4, iters: int = 12) -> list[str]:
    rng = np.random.default_rng(0)
    c = a[rng.choice(len(a), k, replace=False)]
    for _ in range(iters):
        lab = np.argmin(((a[:, None, :] - c[None]) ** 2).sum(-1), 1)
        c = np.array([a[lab == i].mean(0) if (lab == i).any() else c[i] for i in range(k)])
    counts = np.bincount(lab, minlength=k)
    return [f"#{int(r):02X}{int(g):02X}{int(b):02X}" for r, g, b in c[np.argsort(-counts)]]


# --------------------------------------------------------------------------- LLM betimlemesi


class MusicSuggestion(BaseModel):
    genre: str = Field(description="İNGİLİZCE müzik türü, ör. 'lo-fi hip hop', 'ambient electronic', 'cinematic orchestral'")
    mood: Literal["calm", "upbeat", "inspiring", "dramatic", "corporate", "playful", "mysterious"]
    bpm: int = Field(description="60-140 arası tempo")
    instruments: str = Field(description="İNGİLİZCE, virgülle 2-4 enstrüman, ör. 'soft piano, warm pads, light drums'")


class VideoProfile(BaseModel):
    kind: Literal["eğitici", "bilim", "vlog", "röportaj", "ürün tanıtımı", "haber", "hikaye", "motivasyon",
                  "eğlence", "oyun", "yemek", "spor", "diğer"]
    topic: str = Field(description="Videonun konusu, tek cümle (videonun dilinde)")
    audience: str = Field(description="Hedef kitle, kısa")
    tone: str = Field(description="Anlatım tonu, 2-4 kelime")
    energy: Literal["düşük", "orta", "yüksek"]
    description: str = Field(description="Videonun 2-3 cümlelik betimlemesi: ne anlatılıyor, nasıl anlatılıyor, görüntü nasıl")
    key_topics: list[str] = Field(description="3-6 anahtar kavram (videonun dilinde)", max_length=6)
    music: MusicSuggestion
    theme: Literal["apple", "bold", "neon", "minimal"] = Field(description=(
        "Görsel tema: apple zarif/modern (çoğu eğitici ve kişisel video), bold enerjik/eğlence, "
        "neon teknoloji/oyun, minimal sakin/belgesel"))


def _fmt_metrics(m: dict) -> str:
    s, a, v = m.get("speech", {}), m.get("audio", {}), m.get("visual", {})
    lines = [
        f"Süre: {m.get('duration', 0):.0f} sn; konuşma oranı %{s.get('speech_ratio', 0) * 100:.0f}; "
        f"tempo {s.get('wpm', 0)} kelime/dk; uzun duraklama {s.get('long_pauses', 0)}; soru cümlesi {s.get('questions', 0)}",
        f"Dolgu {s.get('fillers', 0)}, kekeleme {s.get('stutters', 0)}, tekrar çekim kelimesi {s.get('retake_words', 0)}",
        f"Ses: {a.get('lufs', '?')} LUFS, dinamik aralık {a.get('loudness_range', '?')} LU, "
        f"enerji değişkenliği {a.get('energy_variation', '?')}",
        f"Görüntü: dakikada {v.get('scene_changes_per_min', '?')} sahne değişimi, parlaklık {v.get('brightness', '?')}, "
        f"renk canlılığı {v.get('colorfulness', '?')}, baskın renkler {', '.join(v.get('palette', []))}",
    ]
    if m.get("scene"):
        sc = m["scene"]
        lines.append(f"Sahne (görsel model): {sc.get('video_type')}, {sc.get('setting')}, {sc.get('people')} kişi, "
                     f"{sc.get('visual_style')}")
    return "\n".join(lines)


def build_profile(project: Project, progress=None, use_llm: bool = True, force: bool = False) -> dict:
    """Ölçümleri ve LLM betimlemesini üretir, analysis/profile.json'a kaydeder."""
    if not force:
        cached = project.load_analysis("profile")
        if cached and (cached.get("llm") or not use_llm):
            return cached
    rep = progress or (lambda *a: None)
    from ..pipeline import source_info

    info = source_info(project)
    tr = project.transcript()
    vad = [tuple(r) for r in project.load_analysis("vad", [])]
    rep("profile", 0.05, "konuşma ve ses ölçülüyor")
    m: dict = {"duration": info.duration, "speech": speech_stats(tr, vad, info.duration), "audio": audio_stats(project)}
    rep("profile", 0.35, "görüntü ölçülüyor")
    m["visual"] = visual_stats(project, info.duration)
    m["scene"] = project.load_analysis("vision")
    if m["scene"] is None:
        try:
            from ..core.config import get_config
            from .vision import describe_video

            rep("profile", 0.5, "görsel model sahneyi tarif ediyor")
            m["scene"] = describe_video(project, info.duration, max(get_config().ollama.vision_frames, 2)).model_dump()
            project.save_analysis("vision", m["scene"])
        except Exception as e:
            m["scene_error"] = str(e)[:200]
    if use_llm:
        rep("profile", 0.7, "LLM videoyu betimliyor")
        try:
            from ..agent.llm import chat_json

            text = " ".join(w.text for w in tr.words if not w.removable)
            if len(text) > 6000:  # uzun videolarda baş, orta ve sondan örnek
                text = text[:2600] + " … " + text[len(text) // 2 - 700: len(text) // 2 + 700] + " … " + text[-1800:]
            prof = chat_json([
                {"role": "system", "content": (
                    "Sen bir video analistisin. Ölçüm araçlarının çıktısını, görsel modelin sahne tarifini ve "
                    "transkripti okuyup videoyu betimlersin. Yalnızca verilen bilgilere dayan.\n"
                    "- Tür: konuşmacı bir konuyu açıklıyor/bilgi veriyorsa 'eğitici' ya da 'bilim'; 'vlog' yalnızca "
                    "günlük hayat paylaşımıdır.\n"
                    "- Müzik: konuşmanın altında çalacak, sözü bastırmayan bir fon müziği öner. genre ve instruments "
                    "İNGİLİZCE olmalı (ör. 'ambient electronic', 'soft piano, warm pads'). Eğitici videolarda "
                    "genellikle 'inspiring' veya 'calm', 80-110 BPM.")},
                {"role": "user", "content": f"ÖLÇÜMLER\n{_fmt_metrics(m)}\n\nTRANSKRİPT\n{text}\n\nVideo profilini JSON olarak ver."},
            ], VideoProfile, max_tokens=700)
            m["llm"] = prof.model_dump()
        except Exception as e:
            m["llm_error"] = str(e)[:300]
    project.save_analysis("profile", m)
    rep("profile", 1.0, (m.get("llm") or {}).get("kind", "profil hazır"))
    return m


def profile_text(m: dict) -> str:
    """Senaristin ve diğer ajanların okuyacağı kısa özet."""
    p = m.get("llm") or {}
    head = (f"Tür: {p.get('kind', '?')} · Konu: {p.get('topic', '?')} · Kitle: {p.get('audience', '?')} · "
            f"Ton: {p.get('tone', '?')} · Enerji: {p.get('energy', '?')}\n{p.get('description', '')}")
    return head + "\n" + _fmt_metrics(m)
