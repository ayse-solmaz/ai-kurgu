# Ücretli GPU sağlayıcıları (RunPod, Vast.ai, Modal vb.) için worker imajı.
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker_app.py .

ENV HF_HOME=/models
EXPOSE 8000
# KURGU_WORKER_TOKEN ortam değişkenini sağlayıcının panelinden verin.
CMD ["uvicorn", "worker_app:app", "--host", "0.0.0.0", "--port", "8000"]
