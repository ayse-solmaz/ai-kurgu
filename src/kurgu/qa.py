"""Kurgu kalite denetimi (QA): ajanın ürettiği kurguyu her video için AYNI kurallarla ölçer.

Bir insanın çıktıyı izleyip yakaladığı hatalar burada koda dönüştürülür:

- kesim:    kelime ortasından kesilen ses (ses enerjisiyle doğrulanır), çıktıda kalan tekrar,
            önceki cümleye bağlı başlayıp öncülü kesilmiş cümle, yarım kalan cümle
- zamanlama: kartın yazdığı kelime konuşmada o anda söylenmiyor
- yerleşim: kartın GERÇEK (render edilmiş) kutusu konuşmacının yüzüyle çakışıyor, ikon bulunamamış
- akış:     animasyon/b-roll çakışması, animasyon yoğunluğu, Shorts hazırlık cümlesinde bitiyor

Sonuç `analysis/qa.json`'a yazılır; yönetmen ajanı düzeltilebilir hataları kendisi giderir
(`director.run_director` → `auto_fix`), `scripts/eval.py` tüm projelerde puan karşılaştırır.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass

import numpy as np

from .analyze.fillers import normalize
from .core import ffmpeg as ff

ERROR, WARN = "hata", "uyarı"


@dataclass
class Issue:
    kind: str  # cut_clip | leftover_repeat | fragment | timing | face | icon | overlap | density | story_end
    severity: str
    msg: str
    at: float | None = None  # çıktıdaki saniye
    item: str | None = None  # timeline öğe kimliği (m3, b1 ...)
    fixable: bool = False


# --------------------------------------------------------------------------- yardımcılar


def _kept_fraction(w, cuts) -> float:
    cut = sum(max(0.0, min(c.end, w.end) - max(c.start, w.start)) for c in cuts)
    return 1.0 - min(1.0, cut / max(w.end - w.start, 1e-3))


def _load_audio(project):
    import wave

    try:
        with wave.open(str(project.audio)) as f:
            sr = f.getframerate()
            a = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768
            if f.getnchannels() > 1:
                a = a.reshape(-1, f.getnchannels()).mean(1)
        return a, sr
    except Exception:
        return None, 0


def _speech_in(audio, sr, a: float, b: float, thr_db: float = -35.0) -> float:
    """[a, b] aralığında konuşma enerjisi olan sürenin oranı (40 ms pencereler)."""
    if audio is None or b - a < 0.04:
        return 0.0
    seg = audio[int(a * sr):int(b * sr)]
    n = int(0.04 * sr)
    if len(seg) < n:
        return 0.0
    frames = seg[: len(seg) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9)
    return float((db > thr_db).mean())


def card_box(mov, t: float, width: int = 270) -> tuple[float, float] | None:
    """Render edilmiş alfa kanallı kartın t anındaki dikey kapsamı (0-1, kare yüksekliğine oranla)."""
    try:
        probe = ff.probe(mov)
        st = next(s for s in probe["streams"] if s.get("codec_type") == "video")
        w, h = int(st["width"]), int(st["height"])
        hh = int(round(h * width / w / 2) * 2)
        raw = subprocess.run([ff.ffmpeg(), "-v", "error", "-ss", f"{t:.2f}", "-i", str(mov), "-vf",
                              f"alphaextract,scale={width}:{hh}", "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "gray", "-"], capture_output=True, env=ff.tool_env(), timeout=60).stdout
        if len(raw) < width * hh:
            return None
        alpha = np.frombuffer(raw[: width * hh], np.uint8).reshape(hh, width)
        rows = np.where((alpha > 40).sum(1) > width * 0.02)[0]
        if not len(rows):
            return None
        return float(rows[0] / hh), float((rows[-1] + 1) / hh)
    except Exception:
        return None


_DEPENDENT = re.compile(r"^(onlar\w*|onu|ona|bunu|buna|bunlar\w*|aynı zamanda|ayrıca|ama|ancak|fakat|çünkü|"
                        r"bu yüzden|bu nedenle|ise|değil)\b", re.I)


# --------------------------------------------------------------------------- denetim


def check(project, tl=None, measure_cards: bool = True) -> dict:
    from .agent.animator import _card_texts, content_anchor
    from .agent.planner import kept_sentences
    from .core.config import load_preset
    from .generate.motion_gfx import list_templates, motion_span

    tl = tl or project.timeline()
    tr = project.transcript()
    tm = tl.time_map()
    words = tr.word_map()
    sents = kept_sentences(tr, tm)
    tpls = list_templates()
    issues: list[Issue] = []
    audio, sr = _load_audio(project)

    # ---- kesim: kelime ortasından kesilen konuşma (ASR sınırları yanıltabilir: ses enerjisine bakılır)
    for w in tr.words:
        f = _kept_fraction(w, tl.cuts)
        if 0.05 < f < 0.95:
            cut_parts = [(max(c.start, w.start), min(c.end, w.end)) for c in tl.cuts if c.start < w.end and c.end > w.start]
            speech = max((_speech_in(audio, sr, a, b) for a, b in cut_parts), default=0.0)
            if speech > 0.5 and f > 0.2:
                at = tm.to_out(w.start, "next")
                issues.append(Issue("cut_clip", ERROR, f"'{w.text}' kelimesinin bir kısmı kesilmiş (sesli bölüm)", at))

    # ---- kesim: çıktıda yakın aralıkla tekrarlanan 3+ kelime (kalan tekrar çekim)
    kept = [w for w in tr.words if _kept_fraction(w, tl.cuts) > 0.5]
    toks = [(normalize(w.text), tm.to_out(w.start, "next") or 0.0) for w in kept]
    toks = [(t, at) for t, at in toks if t]
    seen: dict[tuple, float] = {}
    for i in range(len(toks) - 2):
        g = tuple(t for t, _ in toks[i:i + 3])
        at = toks[i][1]
        if g in seen and at - seen[g] < 10 and len("".join(g)) > 8:
            issues.append(Issue("leftover_repeat", WARN, f"'{' '.join(g)}' kısa aralıkla iki kez söyleniyor", at))
        seen[g] = at

    # ---- kesim: bağlamı kesilmiş / yarım cümle
    kept_ids = {s.id for s in sents}
    order = [s.id for s in tr.sentences]
    for s in sents:
        i = order.index(s.id)
        full = tr.sentences[i]
        prev_kept = i > 0 and order[i - 1] in kept_ids
        if s is not sents[0] and _DEPENDENT.match(s.text.strip()) and not prev_kept:
            issues.append(Issue("fragment", WARN, f"'{s.text[:40]}…' önceki cümleye bağlı ama öncülü kesilmiş", s.out_start))
        if len(s.word_ids) < len(full.word_ids) * 0.6 and len(full.word_ids) >= 4:
            issues.append(Issue("fragment", WARN, f"cümlenin çoğu kesilmiş: '{s.text[:40]}'", s.out_start))

    # ---- akış: Shorts hazırlık cümlesinde bitmesin
    preset = load_preset(tl.preset) if tl.preset else None
    if preset and preset.max_duration and sents:
        if tl.output_duration() > preset.max_duration + 1:
            issues.append(Issue("story_end", ERROR, f"süre {tl.output_duration():.0f} sn, sınır {preset.max_duration:.0f}"))
        if not re.search(r"[.!?…]\W*$", sents[-1].text.strip()):
            issues.append(Issue("story_end", WARN, "video yarım bir cümleyle bitiyor", sents[-1].out_start))

    # ---- zamanlama + yerleşim + ikon
    band = project.load_analysis("face_band") or {}
    spans = []
    for m in tl.motions:
        sp = motion_span(m, tl, words, tm)
        if not sp:
            continue
        spans.append((m, sp))
        tpl = tpls.get(m.template)
        texts = _card_texts(m.template, m.params)
        if texts:
            hit = content_anchor(tr, sents, texts, near=sp[0])
            if hit is None:
                issues.append(Issue("timing", WARN, f"{m.template} '{' '.join(texts)[:30]}' konuşmada geçmiyor", sp[0], m.id))
            else:
                said = tm.to_out(tr.words[hit[1]].start, "next")
                if said is not None and abs(said - sp[0]) > 1.5:
                    issues.append(Issue("timing", ERROR, f"{m.template} '{' '.join(texts)[:30]}' {said:.1f}. sn'de söyleniyor, "
                                                         f"kart {sp[0]:.1f}. sn'de", sp[0], m.id, fixable=True))
        icon_fields = [k for k, p in (tpl.spec.get("params") or {}).items() if p.get("type") == "icon"] if tpl else []
        if any(m.params.get(k) for k in icon_fields) and not m.icons:
            issues.append(Issue("icon", WARN, f"{m.template}: ikon bulunamadı (boş daire)", sp[0], m.id, fixable=True))
        if measure_cards and band.get("found") and m.asset and (tpl and tpl.layout != "full") and not m.template.startswith("gs:"):
            box = card_box(project.abs(m.asset), min(sp[1] - sp[0] - 0.3, 1.6))
            if box:
                ov = max(0.0, min(box[1], band["bottom"]) - max(box[0], band["top"]))
                if ov > 0.03:
                    issues.append(Issue("face", ERROR, f"{m.template} kartı yüzün %{ov / (band['bottom'] - band['top']) * 100:.0f}'ini "
                                                       f"kapatıyor (kart {box[0]:.2f}-{box[1]:.2f})", sp[0], m.id, fixable=True))

    # ---- akış: çakışmalar ve yoğunluk
    spans.sort(key=lambda x: x[1][0])
    for (m1, a), (m2, b) in zip(spans, spans[1:]):
        if b[0] < a[1] - 0.1:
            issues.append(Issue("overlap", WARN, f"{m1.id} ile {m2.id} aynı anda ekranda", b[0], m2.id))
    for b in tl.brolls:
        try:
            r = tm.range_to_out(*b.anchor.resolve(words))
        except ValueError:
            continue
        for m, sp in spans:
            if r and (tpls.get(m.template) and tpls[m.template].layout != "full") and sp[0] < r[1] and r[0] < sp[1]:
                issues.append(Issue("overlap", WARN, f"{m.id} kartı {b.id} b-roll'unun üstünde", sp[0], m.id))
    dur = max(tl.output_duration(), 1.0)
    per_min = len(spans) / dur * 60
    if dur > 20 and per_min < 2:
        issues.append(Issue("density", WARN, f"dakikada {per_min:.1f} animasyon (az)"))

    errors = sum(i.severity == ERROR for i in issues)
    warns = sum(i.severity == WARN for i in issues)
    score = max(0, 100 - 10 * errors - 3 * warns)
    metrics = {"duration": round(dur, 1), "motions": len(spans), "brolls": len(tl.brolls),
               "motions_per_min": round(per_min, 1), "errors": errors, "warnings": warns}
    out = {"score": score, "metrics": metrics, "issues": [asdict(i) for i in issues]}
    try:
        project.save_analysis("qa", out)
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- otomatik düzeltme


def auto_fix(project, tl, report: dict | None = None, rounds: int = 2) -> dict:
    """Düzeltilebilir QA hatalarını giderir: yanlış zamanlanmış kartı söylendiği ana taşır, yüzü kapatan kartı
    ölçülen gerçek boyutuna göre başka yuvaya alır veya kısaltır, ikonu bulunamayan kartı çıkarır.
    Değişen kartlar yeniden render edilir; en fazla `rounds` tur."""
    from .agent.animator import _card_texts, content_anchor
    from .agent.planner import kept_sentences
    from .core.timeline import Anchor
    from .generate.motion_gfx import fill_motions, list_templates, motion_span

    tpls = list_templates()
    log: list[str] = []
    res = check(project, tl)
    for _ in range(rounds):
        fixable = [i for i in res["issues"] if i["fixable"]]
        if not fixable:
            break
        tr = project.transcript()
        tm = tl.time_map()
        words = tr.word_map()
        sents = kept_sentences(tr, tm)
        band = project.load_analysis("face_band") or {}
        changed: list[str] = []
        by_id = {m.id: m for m in tl.motions}
        for iss in fixable:
            m = by_id.get(iss["item"])
            if m is None:
                continue
            if iss["kind"] == "timing":
                sp0 = motion_span(m, tl, words, tm)
                hit = content_anchor(tr, sents, _card_texts(m.template, m.params), near=sp0[0] if sp0 else None)
                if hit:
                    m.anchor = Anchor(word_start=hit[1], word_end=hit[1])
                    changed.append(m.id)
                    log.append(f"{m.id}: söylendiği ana taşındı")
            elif iss["kind"] == "icon":
                tl.motions = [x for x in tl.motions if x.id != m.id]
                log.append(f"{m.id}: ikonsuz kart çıkarıldı")
            elif iss["kind"] == "face" and band.get("found"):
                sp = motion_span(m, tl, words, tm)
                box = card_box(project.abs(m.asset), min(sp[1] - sp[0] - 0.3, 1.6)) if sp and m.asset else None
                if not box:
                    continue
                h = box[1] - box[0]
                spec = ((tpls.get(m.template).spec.get("params") or {}).get("position") or {}) if tpls.get(m.template) else {}
                # base.css yuvaları: üst 9vh'den aşağı, orta 32vh'den aşağı, alt 70vh'ye kadar yukarı
                slots = {"top": (0.09, 0.09 + h), "center": (0.32, 0.32 + h), "bottom": (0.70 - h, 0.70)}
                allowed = [p for p in spec.get("values", []) if p in slots]
                ov = lambda r: max(0.0, min(r[1], band["bottom"]) - max(r[0], band["top"]))
                best = min(allowed, key=lambda p: ov(slots[p])) if allowed else None
                if best and ov(slots[best]) < 0.03:
                    m.params = {**m.params, "position": best}
                    log.append(f"{m.id}: ölçülen kart boyuna göre '{best}' yuvasına alındı")
                else:
                    # hiçbir yuvaya sığmıyor: liste kartıysa madde azaltılır, değilse kart çıkarılır
                    lists = [k for k, v in m.params.items() if isinstance(v, list) and len(v) > 2]
                    if lists:
                        k = lists[0]
                        per = h / max(len(m.params[k]) + 1, 2)
                        keep = max(2, len(m.params[k]) - int(np.ceil(ov(slots[allowed[0] if allowed else "top"]) / per)))
                        m.params = {**m.params, k: m.params[k][:keep], "position": allowed[0] if allowed else "top"}
                        log.append(f"{m.id}: yüze inmesin diye {keep} maddeye indirildi")
                    else:
                        tl.motions = [x for x in tl.motions if x.id != m.id]
                        log.append(f"{m.id}: yüzü kapatmadan yerleşemediği için çıkarıldı")
                        continue
                m.asset = None
                changed.append(m.id)
        if changed:
            fill_motions(project, tl, ids=changed, only_missing=False)
        res = check(project, tl)
    if report is not None:
        report["qa_fixes"] = log
        report["qa"] = {"score": res["score"], "metrics": res["metrics"]}
    return res
