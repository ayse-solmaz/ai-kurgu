"""AssetRouter: b-roll'ları config'deki sırayla sağlayıcılara dener, sonuçları önbellekler."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
import hashlib
from typing import Callable

from ..core.config import LicenseError, get_config
from ..core.project_store import Project
from ..core.timeline import BRoll, Timeline
from .base import AssetProvider, AssetRequest, ProviderUnavailable

Progress = Callable[[str, float, str], None]


def make_provider(name: str) -> AssetProvider:
    if name == "local_image_motion":
        from .image import LocalImageMotion

        return LocalImageMotion()
    if name == "cloud_worker":
        from .cloud_worker import CloudWorkerProvider

        return CloudWorkerProvider()
    if name == "stock":
        from .stock import StockProvider

        return StockProvider()
    if name == "local_video":
        from .video_local import LocalVideoProvider

        return LocalVideoProvider()
    raise ValueError(f"Bilinmeyen sağlayıcı: {name}")


def provider_status(names: list[str] | None = None) -> dict[str, str]:
    """Her sağlayıcının kullanılabilirlik durumu: 'hazır' veya neden kullanılamadığı."""
    out = {}
    for n in names or get_config().assets.providers:
        try:
            make_provider(n).check()
            out[n] = "hazır"
        except (ProviderUnavailable, LicenseError, ValueError) as e:
            out[n] = str(e)
    return out


@dataclass
class FillReport:
    filled: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    providers: dict[str, str] = field(default_factory=dict)


class AssetRouter:
    def __init__(self, project: Project, provider_names: list[str] | None = None):
        self.project = project
        self.names = provider_names or get_config().assets.providers
        self.available: list[AssetProvider] = []
        self.status: dict[str, str] = {}
        for n in self.names:
            try:
                p = make_provider(n)
                p.check()
                self.available.append(p)
                self.status[n] = "hazır"
            except (ProviderUnavailable, LicenseError, ValueError) as e:
                self.status[n] = str(e)
        self.index_path = project.assets_dir / "index.json"
        self.index: dict[str, dict] = (
            json.loads(self.index_path.read_text(encoding="utf-8")) if self.index_path.exists() else {}
        )

    def _save_index(self):
        self.index_path.write_text(json.dumps(self.index, ensure_ascii=False, indent=1), encoding="utf-8")

    def request_for(self, tl: Timeline, br: BRoll, duration: float) -> AssetRequest:
        return AssetRequest(
            prompt=br.prompt, keywords=br.keywords, kind=br.kind, duration=round(duration + 0.8, 1),
            width=tl.output.width, height=tl.output.height, motion=br.motion,
            seed=int(hashlib.md5(br.prompt.encode("utf-8")).hexdigest(), 16) % 100000,  # süreçler arası sabit
        )

    def fill(self, tl: Timeline, progress: Progress | None = None, only_missing: bool = True,
             ids: list[str] | None = None) -> FillReport:
        rep = FillReport(providers=dict(self.status))
        if not self.available:
            for br in tl.brolls:
                rep.failed[br.id] = "hiçbir sağlayıcı kullanılamıyor"
            return rep
        try:
            tr = self.project.transcript()
            words = tr.word_map()
        except FileNotFoundError:
            words = {}
        tm = tl.time_map()
        todo = [b for b in tl.brolls if (ids is None or b.id in ids)]
        for i, br in enumerate(todo):
            if only_missing and br.asset and self.project.abs(br.asset).exists():
                continue
            try:
                a, b = br.anchor.resolve(words)
            except ValueError as e:
                rep.failed[br.id] = str(e)
                continue
            span = tm.range_to_out(a, b)
            if not span:
                rep.failed[br.id] = "b-roll kesilmiş bir bölgede"
                continue
            req = self.request_for(tl, br, span[1] - span[0])
            if progress:
                progress("assets", i / max(len(todo), 1), f"{br.id}: {br.prompt[:45]}")
            errors = []
            for p in self.available:
                key = f"{req.cache_key(p.name)}_{req.duration:.1f}"  # klip süresi de anahtarın parçası
                cached = self.index.get(key)
                if cached and self.project.abs(cached["path"]).exists():
                    br.asset, br.provider, br.attribution = cached["path"], cached["provider"], cached.get("attribution")
                    break
                try:
                    res = p.generate(req, self.project.assets_dir)
                except Exception as e:  # bir sağlayıcı hata verirse sıradakine geç
                    errors.append(f"{p.name}: {e}"[:200])
                    continue
                br.asset = self.project.rel(res.path)
                br.provider = res.provider
                br.attribution = res.attribution
                self.index[key] = {"path": br.asset, "provider": res.provider, "attribution": res.attribution,
                                   "prompt": req.prompt}
                self._save_index()
                break
            if br.asset:
                rep.filled.append(br.id)
            else:
                rep.failed[br.id] = " | ".join(errors) or "bilinmeyen hata"
        if progress:
            progress("assets", 1.0, f"{len(rep.filled)} hazır, {len(rep.failed)} başarısız")
        return rep


def fill_brolls(project: Project, tl: Timeline, progress: Progress | None = None, **kw) -> FillReport:
    return AssetRouter(project).fill(tl, progress, **kw)


def attributions(tl: Timeline) -> list[str]:
    return sorted({b.attribution for b in tl.brolls if b.attribution})

