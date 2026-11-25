"""worker_cloud/worker_app.py'yi gömen Kaggle/Colab notebook'unu üretir.

Kullanım: uv run python scripts/build_worker_notebook.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
app_src = (ROOT / "worker_cloud" / "worker_app.py").read_text(encoding="utf-8")
reqs = (ROOT / "worker_cloud" / "requirements.txt").read_text(encoding="utf-8").split()


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.splitlines(keepends=True)}


cells = [
    md(
        "# AI Kurgu — Bulut GPU Worker (kişisel test)\n\n"
        "1. Kaggle: *Settings → Accelerator → GPU T4*, *Internet → On*. Colab: *Runtime → T4 GPU*.\n"
        "2. Kaggle'da *Add-ons → Secrets* altına `KURGU_WORKER_TOKEN` ekleyin (uzun, rastgele bir metin).\n"
        "3. Hücreleri sırayla çalıştırın. Son hücre `https://....trycloudflare.com` adresini yazar.\n"
        "4. Bu adresi bilgisayarınızdaki `config.yaml` → `assets.cloud_worker_url` alanına, anahtarı `.env` → "
        "`KURGU_WORKER_TOKEN=` satırına yazın.\n\n"
        "**Uyarı:** Ücretsiz Kaggle/Colab yalnızca kişisel deneme içindir; bir ürünün arka ucu olarak "
        "kullanılamaz. Ürün için `worker_cloud/Dockerfile` imajını ücretli bir GPU sağlayıcısında çalıştırın."
    ),
    code(f"!pip -q install {' '.join(r for r in reqs if not r.startswith('torch'))}"),
    code("%%writefile worker_app.py\n" + app_src),
    code(
        "import os\n"
        "try:\n"
        "    from kaggle_secrets import UserSecretsClient\n"
        "    os.environ['KURGU_WORKER_TOKEN'] = UserSecretsClient().get_secret('KURGU_WORKER_TOKEN')\n"
        "except Exception:\n"
        "    try:\n"
        "        from google.colab import userdata\n"
        "        os.environ['KURGU_WORKER_TOKEN'] = userdata.get('KURGU_WORKER_TOKEN')\n"
        "    except Exception:\n"
        "        print('Secret bulunamadı; worker kendi anahtarını üretip loga yazacak.')\n"
        "# MiniMax-H3-Image'ı açmak için (lisansı okuyup kabul ettiyseniz ve yeterli GPU varsa):\n"
        "# os.environ['ENABLE_MINIMAX'] = '1'\n"
    ),
    code(
        "import subprocess, time\n"
        "server = subprocess.Popen(['uvicorn', 'worker_app:app', '--host', '0.0.0.0', '--port', '8000'],\n"
        "                          stdout=open('worker.log', 'w'), stderr=subprocess.STDOUT)\n"
        "time.sleep(8)\n"
        "print(open('worker.log').read()[-2000:])"
    ),
    code(
        "!wget -q -nc https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 "
        "-O cloudflared && chmod +x cloudflared\n"
        "import re, subprocess, time\n"
        "tunnel = subprocess.Popen(['./cloudflared', 'tunnel', '--url', 'http://localhost:8000'],\n"
        "                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)\n"
        "for line in tunnel.stdout:\n"
        "    m = re.search(r'https://[a-z0-9-]+\\.trycloudflare\\.com', line)\n"
        "    if m:\n"
        "        print('WORKER ADRESİ:', m.group(0))\n"
        "        break"
    ),
    code("# Worker loglarını izlemek için\nprint(open('worker.log').read()[-3000:])"),
]

nb = {
    "cells": cells,
    "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                 "accelerator": "GPU"},
    "nbformat": 4,
    "nbformat_minor": 5,
}
out = ROOT / "worker_cloud" / "kaggle_worker.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"Yazıldı: {out}")
