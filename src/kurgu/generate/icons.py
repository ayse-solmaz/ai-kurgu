"""İkon/emoji çözücü: Iconify API (anahtar gerektirmez) + yerel önbellek.

Ticari kullanım için yalnızca izin verici lisanslı setler aranır. Renkli emoji setleri öncelikli
(hareketli grafiklerde daha canlı durur), bulunamazsa çizgi ikon setlerine düşülür.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from ..core.config import project_root

# set -> lisans (SPDX). Hepsi ticari kullanıma izin verir.
ICON_SETS: dict[str, str] = {
    "fluent-emoji-flat": "MIT",
    "noto": "Apache-2.0",
    "fluent-emoji": "MIT",
    "tabler": "MIT",
    "lucide": "ISC",
    "ph": "MIT",
    "mdi": "Apache-2.0",
}
MONO_SETS = ["ph", "lucide", "tabler", "mdi"]  # sade (apple) tema: tek renk çizgi ikonlar
MONO_SYNONYMS: dict[str, list[str]] = {
    "microbe": ["bacteria", "virus"], "germ": ["bacteria"], "pathogen": ["virus"], "bacteria": ["virus"],
    "lizard": ["turtle", "paw"], "reptile": ["turtle"], "dna": ["dna", "atom"], "rna": ["dna"], "mrna": ["dna", "syringe"], "vaccine": ["syringe"], "molecule": ["atom"],
    "thinking face": ["lightbulb", "brain"], "ai": ["robot", "cpu", "brain"], "artificial intelligence": ["robot", "cpu"], "high voltage": ["lightning"], "fire": ["flame"],
    "syringe": ["syringe", "vaccine"], "pill": ["pill", "medicine"], "factory": ["factory", "building"],
    "test tube": ["flask", "test-tube"], "microscope": ["microscope", "flask"], "chart": ["chart-bar"],
}
API = "https://api.iconify.design"
# marka logoları ticari markadır (OpenAI, Google, Apple...): animasyonlarda kullanılmaz
_BRAND = re.compile(r"logo|brand|openai|google|apple|microsoft|meta|facebook|instagram|youtube|tiktok|twitter|github|android|windows|amazon|chatgpt")


def _is_brand(icon_id: str) -> bool:
    return bool(_BRAND.search(icon_id.split(":", 1)[-1]))
FALLBACK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="26" '
    'fill="#ffb703"/><circle cx="32" cy="32" r="12" fill="#fff" opacity=".85"/></svg>'
)
MONO_FALLBACK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="9" fill="#ffffff"/></svg>'


def _cache_dir() -> Path:
    d = project_root() / "assets" / "icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fetch_svg(icon_id: str) -> str | None:
    prefix, _, name = icon_id.partition(":")
    if prefix not in ICON_SETS or not name:
        return None
    f = _cache_dir() / f"{prefix}__{name}.svg"
    if f.exists():
        return f.read_text(encoding="utf-8")
    try:
        r = httpx.get(f"{API}/{prefix}/{name}.svg", params={"height": "auto"}, timeout=15)
        if r.status_code != 200 or "<svg" not in r.text:
            return None
    except httpx.HTTPError:
        return None
    svg = r.text
    if prefix in ("tabler", "lucide", "ph", "mdi"):  # tek renkli setler: koyu zeminde beyaz çizgi
        svg = svg.replace("currentColor", "#ffffff")
    f.write_text(svg, encoding="utf-8")
    return svg


def search(query: str, limit: int = 8, mono: bool = False) -> list[str]:
    """Anahtar kelimeye uyan ikon kimlikleri (set:ad), izinli setlerde, öncelik sırasıyla."""
    q = re.sub(r"[^a-z0-9 \-]", " ", query.lower()).strip()
    if not q:
        return []
    idx = _cache_dir() / "_search.txt"
    sets = MONO_SETS if mono else list(ICON_SETS)
    key = f"{'mono:' if mono else ''}{q}\t"
    if idx.exists():
        for line in idx.read_text(encoding="utf-8").splitlines():
            if line.startswith(key):
                return [x for x in line.split("\t")[1].split(",") if x and not _is_brand(x)]
    try:
        r = httpx.get(f"{API}/search", params={"query": q, "limit": 64, "prefixes": ",".join(sets)}, timeout=15)
        icons = r.json().get("icons", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        icons = []
    order = sets
    icons.sort(key=lambda i: (order.index(i.split(":")[0]) if i.split(":")[0] in order else 99))
    icons = [i for i in icons if not _is_brand(i)][:limit]
    with open(idx, "a", encoding="utf-8") as fh:
        fh.write(f"{key}{','.join(icons)}\n")
    return icons


def resolve(keyword_or_id: str | None, mono: bool = False) -> tuple[str, str | None]:
    """Anahtar kelime veya 'set:ad' -> (svg, ikon kimliği). Bulunamazsa nötr bir daire döner."""
    fallback = MONO_FALLBACK_SVG if mono else FALLBACK_SVG
    if not keyword_or_id:
        return fallback, None
    k = keyword_or_id.strip()
    if ":" in k and not _is_brand(k) and (not mono or k.split(":")[0] in MONO_SETS):
        svg = _fetch_svg(k)
        if svg:
            return svg, k
    if ":" in k:
        k = k.split(":", 1)[1].replace("-", " ")
    candidates = []
    # kısa/çok anlamlı sözcükler ("ai" -> first-aid, moai) ve çizgi setlerde olmayan emoji adları için
    # önce yakın anlamlılar denenir
    for alt in MONO_SYNONYMS.get(k.lower(), []) if (mono or len(k) <= 3) else []:
        candidates = search(alt, mono=mono)
        if candidates:
            break
    if not candidates:
        candidates = search(k, mono=mono)
    if not candidates and " " in k:  # çok kelimeliyse tek tek dene
        for part in sorted(k.split(), key=len, reverse=True):
            candidates = search(part, mono=mono)
            if candidates:
                break
    for c in candidates:
        svg = _fetch_svg(c)
        if svg:
            return svg, c
    return fallback, None


def license_of(icon_id: str | None) -> str | None:
    if not icon_id:
        return None
    return ICON_SETS.get(icon_id.split(":")[0])
