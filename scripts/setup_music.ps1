# Müzik üretimi: ACE-Step 1.5 (MIT) kurulumu.
# tools/ACE-Step-1.5 altına depoyu klonlar, kendi Python 3.11 ortamını kurar ve turbo modelini indirir (~6 GB).
# Kullanım:  powershell -ExecutionPolicy Bypass -File scripts\setup_music.ps1 [-Device xpu|cuda|cpu]
param([ValidateSet("xpu", "cuda", "cpu")] [string]$Device = "xpu")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $root "tools"
$ace = Join-Path $tools "ACE-Step-1.5"
New-Item -ItemType Directory -Force $tools | Out-Null

if (-not (Test-Path $ace)) {
    git clone --depth 1 https://github.com/ACE-Step/ACE-Step-1.5.git $ace
}
Set-Location $ace

# Arayüz ve eğitim paketleri gerekmez (gradio, peft, lightning)
$req = @{ xpu = "requirements-xpu.txt"; cuda = "requirements.txt"; cpu = "requirements.txt" }[$Device]
Get-Content $req | Where-Object { $_ -notmatch "^(gradio|peft|lightning)" } | Set-Content -Encoding utf8 requirements-kurgu.txt

$venv = "venv_$Device"
if (-not (Test-Path $venv)) { uv venv --python 3.11 $venv }
$py = Join-Path $ace "$venv\Scripts\python.exe"
uv pip install --python $py -r requirements-kurgu.txt
uv pip install --python $py huggingface_hub

# Model: turbo DiT + VAE + metin kodlayıcı (Qwen3-Embedding-0.6B). Dil modeli (5Hz LM) gerekmez.
& $py -c "from huggingface_hub import snapshot_download; snapshot_download('ACE-Step/Ace-Step1.5', local_dir='checkpoints', allow_patterns=['acestep-v15-turbo/*','vae/*','Qwen3-Embedding-0.6B/*','config.json'])"
Write-Host "ACE-Step hazır: $ace ($Device)" -ForegroundColor Green
