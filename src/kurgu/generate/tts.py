"""Dış ses (voiceover) üretimi.

Sağlayıcılar (sırayla denenir):
  chatterbox : Chatterbox Multilingual (MIT, Türkçe, ses klonlama). `uv sync --extra tts`
  piper      : Piper TTS (hafif, ONNX). Not: piper-tts 1.3+ GPL-3; dağıtılan üründe dikkat.
  windows    : Windows'un yerleşik Türkçe sesi (yalnızca geliştirme yedeği; açık kaynak değil)

Ses klonlama yalnızca sesin sahibinin onayıyla yapılmalıdır (`voice_consent: true`).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from ..core.config import get_config, require_model
from ..core.models import manager
from .base import ProviderUnavailable


def _key(text: str, provider: str, voice: str | None) -> str:
    return hashlib.sha1(f"{provider}|{voice}|{text}".encode("utf-8")).hexdigest()[:16]


class ChatterboxTTS:
    name = "chatterbox"

    def check(self):
        require_model("chatterbox-multilingual", "tts")
        try:
            import chatterbox  # noqa: F401
        except ImportError as e:
            raise ProviderUnavailable("chatterbox-tts kurulu değil (uv sync --extra tts)") from e

    def _load(self):
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        return ChatterboxMultilingualTTS.from_pretrained(device="cpu")

    def synth(self, text: str, out: Path, voice_ref: Path | None = None, language: str = "tr") -> Path:
        import torchaudio

        model = manager().get("tts:chatterbox", self._load, "chatterbox-multilingual")
        kwargs = {"language_id": language}
        if voice_ref:
            kwargs["audio_prompt_path"] = str(voice_ref)
        wav = model.generate(text, **kwargs)
        torchaudio.save(str(out), wav, model.sr)
        return out


class PiperTTS:
    name = "piper"

    def _voice(self) -> Path:
        cfg = get_config()
        d = cfg.path(cfg.models_cache) / "piper"
        voices = sorted(d.glob("tr_TR-*.onnx")) if d.exists() else []
        if not voices:
            raise ProviderUnavailable(f"Piper Türkçe sesi yok: {d}/tr_TR-*.onnx")
        return voices[0]

    def check(self):
        try:
            import piper  # noqa: F401
        except ImportError as e:
            raise ProviderUnavailable("piper-tts kurulu değil") from e
        self._voice()

    def synth(self, text: str, out: Path, voice_ref: Path | None = None, language: str = "tr") -> Path:
        import wave

        from piper import PiperVoice

        voice = manager().get("tts:piper", lambda: PiperVoice.load(str(self._voice())))
        with wave.open(str(out), "wb") as wf:
            voice.synthesize_wav(text, wf)
        return out


class WindowsTTS:
    """Geliştirme yedeği: Windows OneCore Türkçe sesi (Tolga). Ticari üründe kullanılmamalı."""

    name = "windows"

    SCRIPT = r"""
param([string]$TextFile, [string]$Out)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
function Await($op, [Type]$t) {
  $m = [System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select -First 1
  $task = $m.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait() | Out-Null; $task.Result }
$s = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$v = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | ? { $_.Language -eq 'tr-TR' } | Select -First 1
if (-not $v) { exit 3 }
$s.Voice = $v
$text = [IO.File]::ReadAllText($TextFile, [Text.Encoding]::UTF8)
$st = Await ($s.SynthesizeTextToStreamAsync($text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
$r = New-Object Windows.Storage.Streams.DataReader($st.GetInputStreamAt(0))
$null = Await ($r.LoadAsync([uint32]$st.Size)) ([uint32])
$b = New-Object byte[] $st.Size; $r.ReadBytes($b); [IO.File]::WriteAllBytes($Out, $b)
"""

    def check(self):
        if sys.platform != "win32":
            raise ProviderUnavailable("yalnızca Windows")

    def synth(self, text: str, out: Path, voice_ref: Path | None = None, language: str = "tr") -> Path:
        work = out.parent
        script = work / "_tts.ps1"
        script.write_text(self.SCRIPT, encoding="utf-8-sig")
        txt = out.with_suffix(".txt")
        txt.write_text(text, encoding="utf-8")
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(txt), str(out)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not out.exists():
            raise ProviderUnavailable(f"Windows TTS başarısız: {proc.stderr[-300:]}")
        return out


PROVIDERS = {"chatterbox": ChatterboxTTS, "piper": PiperTTS, "windows": WindowsTTS}


def synthesize(text: str, out_dir: Path, voice_ref: Path | None = None, order: list[str] | None = None) -> tuple[Path, str]:
    """Metni seslendirir; (wav yolu, sağlayıcı) döndürür. Önbellekli."""
    order = order or ["chatterbox", "piper", "windows"]
    if voice_ref and os.environ.get("KURGU_VOICE_CONSENT") != "1":
        raise PermissionError("Ses klonlama için sesin sahibinin onayı gerekir (KURGU_VOICE_CONSENT=1).")
    errors = []
    for name in order:
        p = PROVIDERS[name]()
        try:
            p.check()
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        out = out_dir / f"vo_{_key(text, name, str(voice_ref) if voice_ref else None)}.wav"
        if out.exists():
            return out, name
        try:
            return p.synth(text, out, voice_ref), name
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise ProviderUnavailable("Hiçbir TTS sağlayıcısı çalışmadı: " + " | ".join(errors))


def fill_voiceovers(project, tl) -> dict:
    """Timeline'daki seslendirilmemiş dış sesleri üretir."""
    done, failed = [], {}
    for vo in tl.voiceovers:
        if vo.asset and project.abs(vo.asset).exists():
            continue
        try:
            path, provider = synthesize(vo.text, project.assets_dir)
            vo.asset = project.rel(path)
            done.append(f"{vo.id}:{provider}")
        except Exception as e:
            failed[vo.id] = str(e)
    return {"filled": done, "failed": failed}
