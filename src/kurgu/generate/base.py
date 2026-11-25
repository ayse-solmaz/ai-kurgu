"""B-roll varlık sağlayıcılarının ortak arayüzü."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


class ProviderUnavailable(RuntimeError):
    """Sağlayıcı bu ortamda kullanılamıyor (model yok, anahtar yok, worker kapalı...)."""


@dataclass
class AssetRequest:
    prompt: str
    keywords: list[str] = field(default_factory=list)
    kind: Literal["image", "video", "any"] = "any"
    duration: float = 3.0
    width: int = 1920
    height: int = 1080
    motion: Literal["kenburns", "parallax", "none"] = "kenburns"
    seed: int | None = None
    style: str = "cinematic, photorealistic, natural light, high detail"

    @property
    def orientation(self) -> str:
        return "portrait" if self.height > self.width else "landscape" if self.width > self.height else "square"

    def cache_key(self, provider: str) -> str:
        raw = f"{provider}|{self.prompt}|{','.join(self.keywords)}|{self.kind}|{self.orientation}|{self.seed}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def full_prompt(self) -> str:
        return f"{self.prompt}, {self.style}, no text, no watermark"


@dataclass
class AssetResult:
    path: Path  # video klibi (görseller hareketlendirilip klibe çevrilir)
    provider: str
    kind: Literal["image", "video"]
    attribution: str | None = None
    source_image: Path | None = None


class AssetProvider(Protocol):
    name: str

    def check(self) -> None:
        """Kullanılamıyorsa ProviderUnavailable fırlatır."""

    def generate(self, req: AssetRequest, out_dir: Path) -> AssetResult: ...
