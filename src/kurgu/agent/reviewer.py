"""Öz-denetim: üretilen b-roll'ları görsel modelle kontrol edip zayıf olanları düzeltir.

Her b-roll için kaynak görsel (veya klipten bir kare) + o anda söylenen cümle Qwen3-VL'e
verilir; 1-5 arası uygunluk puanı alınır. Puan eşiğin altındaysa LLM yeni bir prompt yazar
ve görsel yeniden üretilir (en fazla `max_fixes` b-roll).
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field

from ..core.config import get_config
from ..core.project_store import Project
from ..core.timeline import Timeline
from ..ingest.probe import extract_frame
from .llm import chat_json, describe_images, has_model


class Relevance(BaseModel):
    score: int = Field(ge=1, le=5, description="1: alakasız, 5: konuşmayı mükemmel destekliyor")
    problem: str = Field(description="Varsa kısa sorun açıklaması (Türkçe)")


class BetterPrompt(BaseModel):
    visual: str = Field(description="Daha uygun, somut, İngilizce görsel sahne tarifi")


def review_brolls(
    project: Project,
    tl: Timeline,
    progress: Callable[[str, float, str], None] | None = None,
    threshold: int = 3,
    max_fixes: int = 5,
    style_hint: str | None = None,
) -> dict:
    cfg = get_config()
    if not has_model(cfg.models.vlm):
        return {"skipped": "görsel model yok"}
    try:
        tr = project.transcript()
        words = tr.word_map()
    except FileNotFoundError:
        return {"skipped": "transkript yok"}

    results, to_fix = {}, []
    ready = [b for b in tl.brolls if b.asset and project.abs(b.asset).exists()]
    for i, br in enumerate(ready):
        if progress:
            progress("review", i / max(len(ready), 1), f"{br.id} kontrol ediliyor")
        clip = project.abs(br.asset)
        frame = clip.with_suffix(".review.jpg")
        if not frame.exists():
            extract_frame(clip, 0.5, frame, width=448)  # küçük kare = daha az görsel token = daha hızlı
        try:
            a, b = br.anchor.resolve(words)
        except ValueError:
            continue
        said = tr.text_between(a - 1.0, b + 1.0)
        rel = describe_images(
            [frame],
            f'Videoda bu anda konuşmacı şunu söylüyor: "{said}". Bu ara görüntü (b-roll) söyleneni ne kadar '
            "destekliyor? Bozuk/anlamsız görüntü, yazı veya filigran varsa düşük puan ver. JSON döndür.",
            schema=Relevance,
        )
        results[br.id] = {"score": rel.score, "problem": rel.problem}
        if rel.score < threshold:
            to_fix.append((br, said, rel.problem))

    fixed = []
    if to_fix:
        from ..generate.router import fill_brolls

        for br, said, problem in to_fix[:max_fixes]:
            msgs = [{"role": "user", "content": (
                f'Konuşma: "{said}"\nÖnceki görsel tarifi: "{br.prompt}"\nSorun: {problem}\n'
                "Bu konuşmayı daha iyi destekleyen tek bir somut, sinematik, İngilizce görsel sahne tarifi yaz. "
                "Yazı, logo, işaret, gerçek kişi ismi kullanma."
            )}]
            if style_hint:  # yönetmen akışında: sanat yönetmeninin kuralları ve görsel dil
                msgs.insert(0, {"role": "system", "content": style_hint})
            better = chat_json(msgs, BetterPrompt)
            br.prompt = better.visual
            br.asset = None
            fixed.append(br.id)
        fill_brolls(project, tl, progress, ids=fixed, only_missing=True)
    if progress:
        progress("review", 1.0, f"{len(results)} kontrol, {len(fixed)} düzeltme")
    return {"scores": results, "fixed": fixed}

