"""Proje klasörü: kaynak video, analiz sonuçları, timeline sürümleri, varlıklar, renderlar.

projects/<id>/
  project.json            # meta + geçerli timeline sürümü
  source.<ext>
  audio.wav               # 16 kHz mono, analiz için
  proxy.mp4               # düşük çözünürlük, önizleme ve VLM için
  analysis/*.json
  timeline/v0001.json ...
  assets/
  renders/
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .config import get_config
from .timeline import Timeline, Transcript


class ProjectInfo(BaseModel):
    id: str
    name: str
    created: str
    source_file: str
    preset: str
    current_version: int = 0
    status: str = "created"


def _slug(text: str) -> str:
    text = text.lower()
    for a, b in zip("çğıöşü", "cgiosu"):
        text = text.replace(a, b)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:40] or "video"


def projects_root() -> Path:
    root = get_config().path(get_config().projects_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


class Project:
    def __init__(self, path: Path):
        self.dir = Path(path)
        self.info = ProjectInfo.model_validate_json((self.dir / "project.json").read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ oluşturma / açma

    @classmethod
    def create(cls, source: Path, preset: str, name: str | None = None, link: bool = True) -> "Project":
        source = Path(source).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        pid = f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(name or source.stem)}-{uuid.uuid4().hex[:4]}"
        pdir = projects_root() / pid
        for sub in ("analysis", "timeline", "assets", "renders"):
            (pdir / sub).mkdir(parents=True, exist_ok=True)
        dst = pdir / f"source{source.suffix.lower()}"
        copied = False
        # Dropbox/OneDrive gibi senkronize klasörlerde sabit bağlantı (hard link) senkronizasyonu
        # bozabilir; bu durumda her zaman kopyala.
        synced = any(s in str(source).lower() for s in ("dropbox", "onedrive", "google drive", "icloud"))
        if link and not synced:
            try:
                os.link(source, dst)  # aynı diskteyse kopyalamadan bağla
                copied = True
            except OSError:
                pass
        if not copied:
            shutil.copy2(source, dst)
        info = ProjectInfo(
            id=pid,
            name=name or source.stem,
            created=datetime.now().isoformat(timespec="seconds"),
            source_file=dst.name,
            preset=preset,
        )
        (pdir / "project.json").write_text(info.model_dump_json(indent=2), encoding="utf-8")
        return cls(pdir)

    @classmethod
    def open(cls, ref: str | Path) -> "Project":
        p = Path(ref)
        if (p / "project.json").exists():
            return cls(p)
        cand = projects_root() / str(ref)
        if (cand / "project.json").exists():
            return cls(cand)
        matches = sorted(projects_root().glob(f"*{ref}*"))
        if len(matches) == 1:
            return cls(matches[0])
        raise FileNotFoundError(f"Proje bulunamadı: {ref}")

    @staticmethod
    def list_all() -> list[ProjectInfo]:
        out = []
        for d in sorted(projects_root().iterdir(), reverse=True):
            f = d / "project.json"
            if f.exists():
                out.append(ProjectInfo.model_validate_json(f.read_text(encoding="utf-8")))
        return out

    # ------------------------------------------------------------------ yollar

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def source(self) -> Path:
        return self.dir / self.info.source_file

    @property
    def audio(self) -> Path:
        return self.dir / "audio.wav"

    @property
    def proxy(self) -> Path:
        return self.dir / "proxy.mp4"

    @property
    def assets_dir(self) -> Path:
        return self.dir / "assets"

    @property
    def renders_dir(self) -> Path:
        return self.dir / "renders"

    def save_info(self):
        (self.dir / "project.json").write_text(self.info.model_dump_json(indent=2), encoding="utf-8")

    def set_status(self, status: str):
        self.info.status = status
        self.save_info()

    # ------------------------------------------------------------------ analiz

    def analysis_path(self, name: str) -> Path:
        return self.dir / "analysis" / f"{name}.json"

    def has_analysis(self, name: str) -> bool:
        return self.analysis_path(name).exists()

    def save_analysis(self, name: str, data: Any):
        if isinstance(data, BaseModel):
            text = data.model_dump_json(indent=1)
        else:
            text = json.dumps(data, ensure_ascii=False, indent=1)
        self.analysis_path(name).write_text(text, encoding="utf-8")

    def load_analysis(self, name: str, default: Any = None) -> Any:
        p = self.analysis_path(name)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def transcript(self) -> Transcript:
        p = self.analysis_path("transcript")
        if not p.exists():
            raise FileNotFoundError("Transkript yok; önce analiz çalıştırın.")
        return Transcript.model_validate_json(p.read_text(encoding="utf-8"))

    def save_transcript(self, tr: Transcript):
        self.save_analysis("transcript", tr)

    # ------------------------------------------------------------------ timeline sürümleri

    def _tl_path(self, v: int) -> Path:
        return self.dir / "timeline" / f"v{v:04d}.json"

    def versions(self) -> list[int]:
        return sorted(int(p.stem[1:]) for p in (self.dir / "timeline").glob("v*.json"))

    # Aynı projeye birden fazla süreç (masaüstü uygulaması, CLI, web) yazabilir. Bu yüzden sürüm
    # bilgisi her işlemden önce diskten okunur ve yazmalar bir kilit dosyasıyla sıraya alınır.

    def _reload(self) -> None:
        self.info = ProjectInfo.model_validate_json((self.dir / "project.json").read_text(encoding="utf-8"))

    @contextmanager
    def _lock(self, timeout: float = 30.0):
        lock = self.dir / ".lock"
        t0 = time.time()
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 60:  # çökmüş bir süreçten kalan kilit
                        lock.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() - t0 > timeout:
                    raise TimeoutError("Proje başka bir işlem tarafından kilitli")
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(fd)
            lock.unlink(missing_ok=True)

    def timeline(self, version: int | None = None) -> Timeline:
        if version is None:
            self._reload()
        v = version or self.info.current_version
        if not v:
            raise FileNotFoundError("Henüz timeline yok; önce plan oluşturun.")
        return Timeline.model_validate_json(self._tl_path(v).read_text(encoding="utf-8"))

    def has_timeline(self) -> bool:
        self._reload()
        return self.info.current_version > 0

    def save_timeline(self, tl: Timeline, note: str | None = None) -> int:
        """Yeni sürüm olarak kaydeder. Geri alınmış (redo) sürümler silinir."""
        with self._lock():
            self._reload()
            cur = self.info.current_version
            for v in self.versions():
                if v > cur:
                    self._tl_path(v).unlink()
            v = cur + 1
            tl = tl.model_copy(update={"version": v, "note": note or tl.note})
            self._tl_path(v).write_text(tl.model_dump_json(indent=1), encoding="utf-8")
            self.info.current_version = v
            self.save_info()
            return v

    def undo(self) -> int:
        with self._lock():
            self._reload()
            if self.info.current_version > 1:
                self.info.current_version -= 1
                self.save_info()
            return self.info.current_version

    def redo(self) -> int:
        with self._lock():
            self._reload()
            if self.info.current_version + 1 in self.versions():
                self.info.current_version += 1
                self.save_info()
            return self.info.current_version

    def history(self) -> list[dict]:
        out = []
        for v in self.versions():
            data = json.loads(self._tl_path(v).read_text(encoding="utf-8"))
            out.append({"version": v, "note": data.get("note"), "current": v == self.info.current_version})
        return out

    def rel(self, path: Path) -> str:
        """Proje klasörüne göre göreli yol (timeline'da saklamak için)."""
        return Path(path).resolve().relative_to(self.dir.resolve()).as_posix()

    def abs(self, rel: str) -> Path:
        return (self.dir / rel).resolve()

    def new_render_path(self, kind: str = "final") -> Path:
        return self.renders_dir / f"{kind}-v{self.info.current_version:04d}-{int(time.time())}.mp4"
