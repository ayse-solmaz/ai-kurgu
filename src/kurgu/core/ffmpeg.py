"""ffmpeg / ffprobe bulma ve çalıştırma yardımcıları."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Callable

ProgressCb = Callable[[float, str], None]


class FFmpegError(RuntimeError):
    pass


def _candidates(name: str) -> list[str]:
    exe = f"{name}.exe" if os.name == "nt" else name
    found = shutil.which(name)
    out = [found] if found else []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        out += [
            os.path.join(local, "Microsoft", "WinGet", "Links", exe),
            *glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", exe)),
            os.path.join("C:\\", "ffmpeg", "bin", exe),
        ]
    return out


@lru_cache
def find_tool(name: str) -> str:
    env = os.environ.get(f"KURGU_{name.upper()}")
    if env and Path(env).exists():
        return env
    for c in _candidates(name):
        if c and Path(c).exists():
            return c
    raise FFmpegError(
        f"{name} bulunamadı. Windows'ta kurmak için: winget install Gyan.FFmpeg  "
        f"(veya KURGU_{name.upper()} ortam değişkeniyle yolu verin)"
    )


def ffmpeg() -> str:
    return find_tool("ffmpeg")


def ffprobe() -> str:
    return find_tool("ffprobe")


@lru_cache
def tool_env() -> dict:
    """ffmpeg için ortam: libass/drawtext yazı tiplerini bulabilsin diye fontconfig ayarı.

    Windows ffmpeg derlemelerinde varsayılan fontconfig dosyası olmadığından
    "Cannot load default config file" hatası çıkar ve yazı tipleri bulunamaz.
    """
    env = dict(os.environ)
    if env.get("FONTCONFIG_FILE"):
        return env
    from .config import get_config, project_root

    cache = project_root() / ".cache"
    cache.mkdir(exist_ok=True)
    conf = cache / "fonts.conf"
    dirs = [get_config().path(get_config().render.fonts_dir)]
    if os.name == "nt":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        dirs += [Path(windir) / "Fonts", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path.home() / ".fonts"]
    body = "\n".join(f"  <dir>{d.as_posix()}</dir>" for d in dirs)
    conf.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
        f"{body}\n  <cachedir>{(cache / 'fontconfig').as_posix()}</cachedir>\n</fontconfig>\n",
        encoding="utf-8",
    )
    env["FONTCONFIG_FILE"] = str(conf)
    return env


def run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=tool_env()
    )
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"Komut başarısız ({proc.returncode}): {' '.join(args[:6])} ...\n{tail}")
    return proc


def run_ffmpeg(
    args: list[str],
    duration: float | None = None,
    progress: ProgressCb | None = None,
    cwd: Path | None = None,
) -> str:
    """ffmpeg'i çalıştırır, ilerlemeyi bildirir, stderr'i döndürür."""
    cmd = [ffmpeg(), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *args]
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=tool_env(),
    )
    stderr_lines: list[str] = []

    import threading

    def _drain():
        assert proc.stderr
        for line in proc.stderr:
            stderr_lines.append(line)
            if len(stderr_lines) > 4000:
                del stderr_lines[:2000]

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    assert proc.stdout
    for line in proc.stdout:
        if progress and duration and line.startswith("out_time_us="):
            try:
                us = int(line.split("=", 1)[1])
                progress(min(us / 1e6 / duration, 1.0), "")
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=5)
    err = "".join(stderr_lines)
    if proc.returncode != 0:
        tail = "\n".join(err.strip().splitlines()[-30:])
        raise FFmpegError(f"ffmpeg başarısız ({proc.returncode}):\n{tail}")
    return err


def probe(path: Path) -> dict:
    out = run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(out.stdout)


@lru_cache
def version() -> str:
    out = run([ffmpeg(), "-version"]).stdout.splitlines()[0]
    m = re.search(r"version\s+(\S+)", out)
    return m.group(1) if m else out


@lru_cache
def encoder_works(name: str) -> bool:
    """Encoder'ı gerçekten çalıştırarak dener (QSV/NVENC sürücü gerektirir)."""
    try:
        proc = run(
            [
                ffmpeg(), "-hide_banner", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.2",
                "-vf", "format=nv12", "-c:v", name, "-f", "null", "-",
            ],
            check=False,
        )
        return proc.returncode == 0
    except FFmpegError:
        return False


@lru_cache
def has_filter(name: str) -> bool:
    out = run([ffmpeg(), "-hide_banner", "-filters"], check=False).stdout
    return re.search(rf"^\s*\S+\s+{re.escape(name)}\s", out, re.M) is not None


def pick_encoder(pref: str = "auto") -> str:
    if pref != "auto":
        return pref
    for enc in ("h264_qsv", "h264_nvenc", "h264_amf"):
        if encoder_works(enc):
            return enc
    return "libx264"


def video_encode_args(encoder: str, quality: int, preview: bool = False) -> list[str]:
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast" if preview else "medium",
                "-global_quality", str(quality + (6 if preview else 0)), "-look_ahead", "0"]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p2" if preview else "p5", "-cq", str(quality + (6 if preview else 0))]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "speed" if preview else "balanced",
                "-rc", "cqp", "-qp_i", str(quality), "-qp_p", str(quality)]
    return ["-c:v", "libx264", "-preset", "ultrafast" if preview else "medium",
            "-crf", str(quality + (6 if preview else 0))]


def filter_script_args(script: Path) -> list[str]:
    """Uzun filtre grafikleri komut satırı sınırına takılmasın diye dosyadan okunur."""
    major = int(re.match(r"n?(\d+)", version()).group(1)) if re.match(r"n?(\d+)", version()) else 7
    if major >= 7:
        return ["-/filter_complex", str(script)]
    return ["-filter_complex_script", str(script)]


def escape_filter_path(path: str) -> str:
    """Filtre argümanlarında (subtitles=...) kullanılacak yolu kaçışlar."""
    p = path.replace("\\", "/")
    return p.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")
