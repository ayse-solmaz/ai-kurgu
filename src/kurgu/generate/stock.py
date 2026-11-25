"""Ücretsiz stok video yedeği: Pexels ve Pixabay API'leri.

İçerikler ticari kullanılabilir; API koşulları gereği kaynak/atıf bilgisi saklanır ve
arayüzde gösterilmelidir. Anahtarlar ortam değişkenlerinden (.env) okunur.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from ..core.config import get_config
from .base import AssetRequest, AssetResult, ProviderUnavailable
from .motion import fit_clip


def _load_dotenv():
    from ..core.config import project_root

    env = project_root() / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def _download(url: str, dst: Path) -> Path:
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
    return dst


def _query(req: AssetRequest) -> str:
    return " ".join(req.keywords[:3]) if req.keywords else " ".join(req.prompt.split()[:5])


class PexelsProvider:
    name = "pexels"

    def _key(self) -> str:
        _load_dotenv()
        key = os.environ.get(get_config().assets.pexels_api_key_env)
        if not key:
            raise ProviderUnavailable("PEXELS_API_KEY tanımlı değil (.env)")
        return key

    def check(self):
        self._key()

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        r = httpx.get(
            "https://api.pexels.com/videos/search",
            params={"query": _query(req), "orientation": req.orientation, "per_page": 8, "size": "medium"},
            headers={"Authorization": self._key()},
            timeout=30,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if not videos:
            raise ProviderUnavailable(f"Pexels'te sonuç yok: {_query(req)}")
        # süresi yeterli, çözünürlüğü hedefe en yakın dosya
        best = None
        for v in videos:
            for f in v.get("video_files", []):
                if f.get("file_type") != "video/mp4" or not f.get("width"):
                    continue
                score = abs(f["height"] - req.height) + (0 if v.get("duration", 0) >= req.duration else 2000)
                if best is None or score < best[0]:
                    best = (score, v, f)
        if not best:
            raise ProviderUnavailable("Uygun mp4 bulunamadı")
        _, v, f = best
        raw = out_dir / f"pexels_{v['id']}.mp4"
        if not raw.exists():
            _download(f["link"], raw)
        clip = out_dir / f"pexels_{v['id']}_{req.width}x{req.height}_{req.duration:.1f}.mp4"
        fit_clip(raw, clip, req.duration, req.width, req.height)
        user = v.get("user", {}).get("name", "Pexels")
        return AssetResult(clip, self.name, "video", f"Video: {user} / Pexels ({v.get('url')})")


class PixabayProvider:
    name = "pixabay"

    def _key(self) -> str:
        _load_dotenv()
        key = os.environ.get(get_config().assets.pixabay_api_key_env)
        if not key:
            raise ProviderUnavailable("PIXABAY_API_KEY tanımlı değil (.env)")
        return key

    def check(self):
        self._key()

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        r = httpx.get(
            "https://pixabay.com/api/videos/",
            params={"key": self._key(), "q": _query(req)[:100], "per_page": 10, "safesearch": "true"},
            timeout=30,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            raise ProviderUnavailable(f"Pixabay'de sonuç yok: {_query(req)}")
        hit = max(hits, key=lambda h: (h.get("duration", 0) >= req.duration, h.get("likes", 0)))
        vids = hit.get("videos", {})
        f = vids.get("large") if vids.get("large", {}).get("url") else vids.get("medium")
        raw = out_dir / f"pixabay_{hit['id']}.mp4"
        if not raw.exists():
            _download(f["url"], raw)
        clip = out_dir / f"pixabay_{hit['id']}_{req.width}x{req.height}_{req.duration:.1f}.mp4"
        fit_clip(raw, clip, req.duration, req.width, req.height)
        return AssetResult(clip, self.name, "video", f"Video: {hit.get('user', 'Pixabay')} / Pixabay ({hit.get('pageURL')})")


class StockProvider:
    """Pexels, olmazsa Pixabay."""

    name = "stock"

    def __init__(self):
        self.providers = [PexelsProvider(), PixabayProvider()]

    def check(self):
        errors = []
        for p in self.providers:
            try:
                p.check()
                return
            except ProviderUnavailable as e:
                errors.append(str(e))
        raise ProviderUnavailable("; ".join(errors))

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult:
        last: Exception | None = None
        for p in self.providers:
            try:
                p.check()
                return p.generate(req, out_dir)
            except (ProviderUnavailable, httpx.HTTPError) as e:
                last = e
        raise ProviderUnavailable(str(last))
