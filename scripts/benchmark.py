"""Bu bilgisayarda hangi ayarların daha hızlı olduğunu ölçer.

Kullanım:  uv run python scripts/benchmark.py [--image]

Ölçülenler: Whisper (CPU int8) gerçek zaman oranı, LLM token/sn, VLM yanıt süresi,
encoder hızı (QSV vs libx264), isteğe bağlı görsel üretim süresi (GPU vs CPU).
Sonuçlar ekrana yazılır; config.yaml'ı buna göre ayarlayabilirsiniz.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kurgu.core import ffmpeg as ff  # noqa: E402
from kurgu.core.config import get_config  # noqa: E402


def bench_encoders(tmp: Path):
    print("\n[encoder] 10 sn 1080x1920 test videosu kodlanıyor")
    for enc in ("h264_qsv", "libx264"):
        if not ff.encoder_works(enc):
            print(f"  {enc}: yok")
            continue
        out = tmp / f"enc_{enc}.mp4"
        t0 = time.time()
        ff.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=10", "-vf",
                       "format=nv12" if enc.endswith("qsv") else "format=yuv420p",
                       *ff.video_encode_args(enc, 21), str(out)])
        dt = time.time() - t0
        print(f"  {enc}: {dt:.1f} sn  ({10 / dt:.1f}x gerçek zaman)")


def bench_whisper(tmp: Path):
    sample = ROOT / "samples" / "ornek_ses.wav"
    if not sample.exists():
        print("\n[whisper] samples/ornek_ses.wav yok; önce scripts/make_sample.ps1 çalıştırın")
        return
    from kurgu.analyze.asr import transcribe

    info = ff.probe(sample)
    dur = float(info["format"]["duration"])
    t0 = time.time()
    tr = transcribe(sample)
    dt = time.time() - t0
    print(f"\n[whisper] {get_config().models.asr}: {dur:.0f} sn ses {dt:.1f} sn'de ({dur / dt:.1f}x), {len(tr.words)} kelime")


def bench_llm():
    import httpx

    cfg = get_config()
    for model in (cfg.models.llm,):
        t0 = time.time()
        try:
            r = httpx.post(f"{cfg.ollama.host}/api/generate", json={
                "model": model, "prompt": "Video kurgusu hakkında 80 kelimelik bir paragraf yaz.",
                "stream": False, "think": False, "options": {"num_predict": 160}}, timeout=600).json()
        except Exception as e:
            print(f"\n[llm] {model}: hata {e}")
            continue
        n = r.get("eval_count", 0)
        sec = r.get("eval_duration", 1) / 1e9
        print(f"\n[llm] {model}: {n / sec:.1f} token/sn (ilk yükleme dahil toplam {time.time() - t0:.1f} sn)")
    try:
        ps = httpx.get(f"{cfg.ollama.host}/api/ps", timeout=5).json()
        for m in ps.get("models", []):
            vram = m.get("size_vram", 0) / 1e9
            print(f"  {m['name']}: GPU belleği {vram:.1f} GB / toplam {m.get('size', 0) / 1e9:.1f} GB"
                  f" -> {'iGPU kullanılıyor' if vram > 0.5 else 'CPU'}")
    except Exception:
        pass


def bench_image(tmp: Path):
    from kurgu.generate.base import AssetRequest
    from kurgu.generate.image import generate_image, is_downloaded

    cfg = get_config()
    if not is_downloaded(cfg.models.image):
        print(f"\n[image] {cfg.models.image} indirilmemiş (kurgu models pull image)")
        return
    req = AssetRequest(prompt="aerial view of a futuristic city at sunset", width=1080, height=1920, seed=1)
    for i in range(2):  # ilki derleme/yükleme içerir
        t0 = time.time()
        generate_image(req, tmp / f"img{i}.jpg")
        print(f"\n[image] {cfg.models.image} ({cfg.devices.openvino}) deneme {i + 1}: {time.time() - t0:.1f} sn")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", action="store_true", help="Görsel üretimini de ölç")
    args = ap.parse_args()
    import openvino as ov

    print("OpenVINO cihazları:", ov.Core().available_devices)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        bench_encoders(tmp)
        bench_whisper(tmp)
        bench_llm()
        if args.image:
            bench_image(tmp)
