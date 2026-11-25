"""ACE-Step 1.5 ile enstrümantal müzik üretir. ACE-Step'in kendi sanal ortamında çalışır
(tools/ACE-Step-1.5/venv_xpu), AI Kurgu'yu içe aktarmaz; kurgu.generate.music bu betiği alt süreç olarak çağırır.

Kullanım: <ace venv python> scripts/ace_music.py job.json
job.json: {"ace_dir", "out_dir", "caption", "bpm", "duration", "seed", "steps", "device"}
Çıktı (stdout'un son satırı): {"ok": true, "path": "...wav", "seconds": 12.3} veya {"ok": false, "error": "..."}
"""

from __future__ import annotations

import json
import os
import sys
import time


def pick_device(want: str) -> str:
    """auto: CUDA (doğruluk testini geçerse) > CPU. Intel XPU yalnızca açıkça istenirse kullanılır: bazı torch-XPU
    sürümleri bazı Intel sürücüleriyle arange/cos gibi temel işlemleri YANLIŞ hesaplıyor (hata vermeden sıfır
    döndürüyor) ve paylaşılan iGPU belleği görsel üretimle çakışıyor."""
    import torch

    if want not in ("auto", ""):
        return want

    def sane(dev: str) -> bool:
        try:
            x = torch.arange(6, device=dev, dtype=torch.float32)
            ok = torch.allclose(x.cpu(), torch.arange(6, dtype=torch.float32))
            return ok and torch.allclose(torch.cos(x).cpu(), torch.cos(torch.arange(6, dtype=torch.float32)), atol=1e-3)
        except Exception:
            return False

    if torch.cuda.is_available() and sane("cuda"):
        return "cuda"
    # Intel iGPU (XPU) otomatik seçilmez: bellek görsel üretim/LLM ile paylaşılıyor ve bazı torch-XPU + sürücü
    # birleşimlerinde sonuçlar aralıklı olarak yanlış. İstenirse KURGU_MUSIC_DEVICE=xpu ile açıkça seçilir.
    return "cpu"


def main() -> int:
    job = json.loads(open(sys.argv[1], encoding="utf-8").read())
    ace = job["ace_dir"]
    sys.path.insert(0, ace)
    os.chdir(ace)
    t0 = time.time()
    try:
        from acestep.handler import AceStepHandler
        from acestep.inference import GenerationConfig, GenerationParams, generate_music

        device = pick_device(job.get("device", "auto"))
        print(f"[kurgu] cihaz: {device}", file=sys.stderr, flush=True)
        h = AceStepHandler()
        status, ok = h.initialize_service(project_root=ace, config_path=job.get("model", "acestep-v15-turbo"),
                                          device=device, offload_to_cpu=False)
        if not ok:
            print(json.dumps({"ok": False, "error": f"model yüklenemedi: {status}"}))
            return 1
        seed = int(job.get("seed", -1))
        params = GenerationParams(
            caption=job["caption"][:500], lyrics="[Instrumental]", instrumental=True,
            bpm=int(job["bpm"]) if job.get("bpm") else None, duration=float(job["duration"]),
            inference_steps=int(job.get("steps", 8)), seed=seed, thinking=False,
            use_cot_metas=False, use_cot_caption=False, use_cot_language=False,
            fade_in_duration=1.0, fade_out_duration=2.5,
        )
        cfg = GenerationConfig(batch_size=1, use_random_seed=seed < 0, seeds=[seed] if seed >= 0 else None,
                               audio_format="wav")
        res = generate_music(h, None, params, cfg, save_dir=job["out_dir"])
        if not res.success or not res.audios:
            print(json.dumps({"ok": False, "error": res.error or res.status_message}))
            return 1
        print(json.dumps({"ok": True, "path": res.audios[0].get("path"), "seconds": round(time.time() - t0, 1),
                          "device": device}))
        return 0
    except Exception as e:  # hata metni üst sürece JSON olarak döner
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"[:800]}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
