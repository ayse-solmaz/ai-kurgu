# AI Kurgu kurulum betiği (Windows)
# Kullanım:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-WithTTS] [-WithImageModel]
param(
    [switch]$WithTTS,         # Chatterbox Multilingual dış ses (torch, ~2 GB)
    [switch]$WithImageModel   # FLUX.1-schnell OpenVINO INT4 (~9.3 GB)
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Step "uv"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
}

Step "ffmpeg (libass + Intel QSV içeren tam derleme)"
$ff = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $ff -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
}

Step "Python 3.12 ortamı ve bağımlılıklar"
uv python install 3.12
if ($WithTTS) { uv sync --extra tts } else { uv sync }

Step "Ollama ve modeller"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
}
try { Invoke-RestMethod http://127.0.0.1:11434/api/version | Out-Null }
catch { Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"; Start-Sleep 5 }
ollama pull qwen3:30b-a3b   # varsayılan senarist/planlayıcı (~18.6 GB, işlemcide çalışır)
ollama pull qwen3:8b        # yedek: büyük model yoksa veya bellek yetmezse
ollama pull qwen3-vl:4b

Step "Yazı tipleri (SIL OFL)"
uv run python scripts/fetch_fonts.py

if ($WithImageModel) {
    Step "Yerel görsel modeli (FLUX.1-schnell, OpenVINO INT4)"
    uv run kurgu models pull image
}

Step "Web arayüzü"
Push-Location web
npm install --no-audit --no-fund
npm run build
Pop-Location

Step "Kontrol"
uv run kurgu doctor
Write-Host "`nHazır. Başlatmak için:  uv run kurgu serve   ->  http://127.0.0.1:8765" -ForegroundColor Green
