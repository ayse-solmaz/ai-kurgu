"""Uygulamaya ait Ollama sunucusu.

Ollama, Intel Arc gibi entegre GPU'ları varsayılan olarak devre dışı bırakır. Kullanıcının kendi
Ollama ayarlarını değiştirmemek için uygulama, ayrı bir portta iGPU'su açık bir Ollama
sunucusu başlatır (aynı model klasörünü paylaşır, modeller tekrar indirilmez).
Bu bilgisayarda ölçülen fark (qwen3:8b, 2.3k token prompt): iGPU 16 sn, CPU 67 sn.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import get_config, project_root


def _alive(host: str) -> bool:
    try:
        return httpx.get(f"{host}/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(cand) if cand.exists() else None


def ensure_ollama(timeout: float = 20.0) -> bool:
    """Yapılandırılan Ollama sunucusu çalışmıyorsa (ve yönetim açıksa) başlatır."""
    cfg = get_config().ollama
    if _alive(cfg.host):
        return True
    if not cfg.manage:
        return False
    exe = _ollama_exe()
    if not exe:
        return False
    u = urlparse(cfg.host)
    env = dict(os.environ)
    env["OLLAMA_HOST"] = f"{u.hostname}:{u.port or 11434}"
    if cfg.igpu:
        env["OLLAMA_IGPU_ENABLE"] = "1"
        env["OLLAMA_VULKAN"] = "1"
    log_dir = project_root() / ".cache"
    log_dir.mkdir(exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    with open(log_dir / "ollama.log", "ab") as log:
        subprocess.Popen([exe, "serve"], env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _alive(cfg.host):
            return True
        time.sleep(0.5)
    return False
