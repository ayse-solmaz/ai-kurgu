"""AI Kurgu bulut GPU worker'ı (CUDA).

Çalıştırma:
    pip install -r requirements.txt
    KURGU_WORKER_TOKEN=gizli-bir-anahtar uvicorn worker_app:app --host 0.0.0.0 --port 8000
    cloudflared tunnel --url http://localhost:8000      # herkese açık https adresi verir

Modeller ilk istekte indirilir ve bellekte tek model tutulur. Desteklenenler:
  image: flux-schnell (Apache-2.0), z-image-turbo (Apache-2.0),
         minimax-h3-image (yalnızca ENABLE_MINIMAX=1 ile; özel lisans, bkz. models_registry.yaml)
  video: wan2.1-t2v-1.3b (Apache-2.0)

Not: Kaggle/Colab ücretsiz katmanı kişisel deneme içindir; ürün arka ucu olarak
kullanmak bu hizmetlerin koşullarına aykırıdır. Ürün için aynı imajı ücretli GPU'da çalıştırın.
"""

from __future__ import annotations

import gc
import os
import queue
import secrets
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

TOKEN = os.environ.get("KURGU_WORKER_TOKEN") or secrets.token_urlsafe(24)
if not os.environ.get("KURGU_WORKER_TOKEN"):
    print(f"[worker] KURGU_WORKER_TOKEN tanımlı değil, bu oturum için üretildi: {TOKEN}")

OUT = Path(tempfile.gettempdir()) / "kurgu_worker"
OUT.mkdir(exist_ok=True)

IMAGE_MODELS = {
    "flux-schnell": ("black-forest-labs/FLUX.1-schnell", 4),
    "z-image-turbo": ("Tongyi-MAI/Z-Image-Turbo", 8),
}
if os.environ.get("ENABLE_MINIMAX") == "1":
    IMAGE_MODELS["minimax-h3-image"] = ("MiniMaxAI/MiniMax-H3-Image", 6)
VIDEO_MODELS = {"wan2.1-t2v-1.3b": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"}


class JobIn(BaseModel):
    kind: Literal["image", "video"]
    model: str
    prompt: str
    width: int = 1024
    height: int = 576
    duration: float = 3.0
    seed: int | None = None


class Job(BaseModel):
    id: str
    req: JobIn
    status: str = "queued"
    error: str | None = None
    path: str | None = None
    created: float = 0.0
    seconds: float | None = None


jobs: dict[str, Job] = {}
q: "queue.Queue[str]" = queue.Queue()
_loaded: dict[str, object] = {}


def auth(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, f"Bearer {TOKEN}"):
        raise HTTPException(401, "Geçersiz anahtar")


# --------------------------------------------------------------------------- modeller


def _vram_gb() -> float:
    import torch

    if not torch.cuda.is_available():
        return 0.0
    return torch.cuda.get_device_properties(0).total_memory / 1e9


def _dtype():
    import torch

    return torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16


def _get(name: str):
    if name in _loaded:
        return _loaded[name]
    for k in list(_loaded):  # tek model tut
        del _loaded[k]
    gc.collect()
    import torch

    torch.cuda.empty_cache()

    if name == "flux-schnell":
        from diffusers import FluxPipeline

        kwargs = {"torch_dtype": _dtype()}
        if _vram_gb() < 30:  # T4/16GB: dönüştürücüyü 4-bit'e indir
            from diffusers import BitsAndBytesConfig, FluxTransformer2DModel

            tr = FluxTransformer2DModel.from_pretrained(
                IMAGE_MODELS[name][0], subfolder="transformer",
                quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                       bnb_4bit_compute_dtype=_dtype()),
                torch_dtype=_dtype(),
            )
            kwargs["transformer"] = tr
        pipe = FluxPipeline.from_pretrained(IMAGE_MODELS[name][0], **kwargs)
        pipe.enable_model_cpu_offload()
    elif name in IMAGE_MODELS:
        from diffusers import DiffusionPipeline

        pipe = DiffusionPipeline.from_pretrained(IMAGE_MODELS[name][0], torch_dtype=_dtype())
        pipe.enable_model_cpu_offload()
    elif name in VIDEO_MODELS:
        from diffusers import AutoencoderKLWan, WanPipeline

        repo = VIDEO_MODELS[name]
        vae = AutoencoderKLWan.from_pretrained(repo, subfolder="vae", torch_dtype=torch.float32)
        pipe = WanPipeline.from_pretrained(repo, vae=vae, torch_dtype=_dtype())
        pipe.enable_model_cpu_offload()
    else:
        raise ValueError(f"Desteklenmeyen model: {name}")
    _loaded[name] = pipe
    return pipe


def _size(w: int, h: int, long_side: int, mult: int = 16) -> tuple[int, int]:
    ar = w / h
    if ar >= 1:
        ow, oh = long_side, long_side / ar
    else:
        ow, oh = long_side * ar, long_side
    return int(ow // mult * mult), int(oh // mult * mult)


def run(job: Job):
    import torch

    r = job.req
    gen = torch.Generator("cpu").manual_seed(r.seed or 0)
    if r.kind == "image":
        repo, steps = IMAGE_MODELS[r.model]
        pipe = _get(r.model)
        long_side = 2048 if r.model == "minimax-h3-image" else 1024
        w, h = _size(r.width, r.height, long_side)
        kwargs = dict(prompt=r.prompt, width=w, height=h, num_inference_steps=steps, generator=gen)
        if r.model in ("flux-schnell", "z-image-turbo", "minimax-h3-image"):
            kwargs["guidance_scale"] = 0.0 if r.model != "minimax-h3-image" else 1.0
        img = pipe(**kwargs).images[0]
        out = OUT / f"{job.id}.png"
        img.save(out)
    else:
        from diffusers.utils import export_to_video

        pipe = _get(r.model)
        w, h = (832, 480) if r.width >= r.height else (480, 832)
        fps = 16
        frames = int(min(r.duration, 5.0) * fps) // 4 * 4 + 1
        video = pipe(prompt=r.prompt, negative_prompt="text, watermark, blurry, low quality, distorted",
                     width=w, height=h, num_frames=frames, guidance_scale=5.0, generator=gen).frames[0]
        out = OUT / f"{job.id}.mp4"
        export_to_video(video, str(out), fps=fps)
    job.path = str(out)


def worker_loop():
    while True:
        jid = q.get()
        job = jobs[jid]
        job.status = "running"
        t0 = time.time()
        try:
            run(job)
            job.status = "done"
        except Exception as e:
            job.status = "failed"
            job.error = f"{e}\n{traceback.format_exc()[-1500:]}"
        job.seconds = round(time.time() - t0, 1)


threading.Thread(target=worker_loop, daemon=True).start()

# --------------------------------------------------------------------------- API

app = FastAPI(title="AI Kurgu GPU Worker")


@app.get("/health", dependencies=[Depends(auth)])
def health():
    return {
        "models": {"image": list(IMAGE_MODELS), "video": list(VIDEO_MODELS), "music": []},
        "vram_gb": round(_vram_gb(), 1),
        "queue": q.qsize(),
    }


@app.post("/jobs", dependencies=[Depends(auth)])
def create_job(req: JobIn):
    allowed = IMAGE_MODELS if req.kind == "image" else VIDEO_MODELS
    if req.model not in allowed:
        raise HTTPException(400, f"Model desteklenmiyor: {req.model}. Mevcut: {list(allowed)}")
    job = Job(id=uuid.uuid4().hex[:12], req=req, created=time.time())
    jobs[job.id] = job
    q.put(job.id)
    return {"id": job.id}


@app.get("/jobs/{jid}", dependencies=[Depends(auth)])
def job_status(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404)
    return {"status": job.status, "error": job.error, "seconds": job.seconds}


@app.get("/jobs/{jid}/result", dependencies=[Depends(auth)])
def job_result(jid: str):
    job = jobs.get(jid)
    if not job or job.status != "done" or not job.path:
        raise HTTPException(404, "Sonuç hazır değil")
    return FileResponse(job.path)
