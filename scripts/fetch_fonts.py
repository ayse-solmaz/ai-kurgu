"""Açık lisanslı (SIL OFL) tasarım fontlarını indirir.

- assets/fonts/*.ttf : altyazı motoru (libass) için; latin + latin-ext alt kümeleri tek dosyada
  birleştirilir (Türkçe ğ ş ı İ dahil).
- motion/fonts/*.woff2 : hareketli grafik şablonları (Chromium) için.

Kullanım: uv run python scripts/fetch_fonts.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CDN = "https://cdn.jsdelivr.net/fontsource/fonts/{fam}@latest/{sub}-{w}-normal.{ext}"

# (fontsource adı, ağırlıklar)
FAMILIES = {
    "inter": [500, 600, 700, 800],
    "inter-tight": [600, 700, 800],
    "montserrat": [800, 900],
}


def get(url: str) -> bytes:
    r = httpx.get(url, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return r.content


def main() -> None:
    from fontTools.merge import Merger
    from fontTools.ttLib import TTFont

    ttf_dir = ROOT / "assets" / "fonts"
    woff_dir = ROOT / "motion" / "fonts"
    ttf_dir.mkdir(parents=True, exist_ok=True)
    woff_dir.mkdir(parents=True, exist_ok=True)
    for fam, weights in FAMILIES.items():
        for w in weights:
            out = ttf_dir / f"{fam}-{w}.ttf"
            if not out.exists():
                parts = []
                for sub in ("latin", "latin-ext"):
                    p = ttf_dir / f"_{fam}-{sub}-{w}.ttf"
                    p.write_bytes(get(CDN.format(fam=fam, sub=sub, w=w, ext="ttf")))
                    parts.append(str(p))
                merged = Merger().merge(parts)
                merged.save(str(out))
                for p in parts:
                    Path(p).unlink()
                f = TTFont(str(out))
                fam_name = f["name"].getDebugName(1)
                print(f"ttf  {out.name}: {fam_name}, {len(f.getGlyphOrder())} glif")
            for sub in ("latin", "latin-ext"):
                wf = woff_dir / f"{fam}-{sub}-{w}.woff2"
                if not wf.exists():
                    wf.write_bytes(get(CDN.format(fam=fam, sub=sub, w=w, ext="woff2")))
    print("hazır")


if __name__ == "__main__":
    sys.exit(main())
