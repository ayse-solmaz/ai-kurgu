"""Yüz takibi (9:16 reframe için): OpenCV YuNet yüz dedektörü (MIT, ~230 KB).

Yarım saniyede bir kare örneklenir, en büyük / bir önceki konuma en yakın yüz seçilir,
kamera hareketi yumuşatılır ve küçük oynamalarda kadraj sabit tutulur (ölü bölge).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ..core.config import get_config
from ..core.timeline import ReframeKey

YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)


def _model_path() -> Path:
    cfg = get_config()
    p = cfg.path(cfg.models_cache) / "faces" / "face_detection_yunet_2023mar.onnx"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", YUNET_URL, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            p.write_bytes(r.read())
    return p


def track_faces(video: Path, step: float = 0.5, smooth: float = 0.35, deadzone: float = 0.06) -> list[ReframeKey]:
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Video açılamadı: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scale = 640 / max(w, 1)
    sw, sh = int(w * scale), int(h * scale)
    det = cv2.FaceDetectorYN.create(str(_model_path()), "", (sw, sh), score_threshold=0.6)

    every = max(int(round(fps * step)), 1)
    raw: list[tuple[float, float | None]] = []
    prev_cx = 0.5
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % every == 0:
            ok, frame = cap.retrieve()
            if ok:
                small = cv2.resize(frame, (sw, sh))
                _, faces = det.detect(small)
                cx = None
                if faces is not None and len(faces):
                    # büyüklük ve bir önceki konuma yakınlık birlikte puanlanır
                    best = max(
                        faces,
                        key=lambda f: f[2] * f[3] - 4000 * abs((f[0] + f[2] / 2) / sw - prev_cx),
                    )
                    cx = float((best[0] + best[2] / 2) / sw)
                    prev_cx = cx
                raw.append((idx / fps, cx))
        idx += 1
    cap.release()

    keys: list[ReframeKey] = []
    cam = None
    for t, cx in raw:
        target = cx if cx is not None else (cam if cam is not None else 0.5)
        if cam is None:
            cam = target
        elif abs(target - cam) > deadzone:
            cam = cam + smooth * (target - cam)
        keys.append(ReframeKey(t=round(t, 2), cx=round(cam, 4)))
    return keys


def face_band(video: Path, samples: int = 16) -> dict:
    """Konuşmacının yüzünün dikeyde kapladığı bant (0-1, kare yüksekliğine oranla).

    Kartları yüzün üstüne koymamak için kullanılır. 16:9 -> 9:16 kırpma dikeyi değiştirmez.
    Dönen: {"top": .., "bottom": .., "found": n}; yüz yoksa found=0.
    """
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return {"found": 0}
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scale = 640 / max(w, h, 1)
    sw, sh = max(int(w * scale), 1), max(int(h * scale), 1)
    det = cv2.FaceDetectorYN.create(str(_model_path()), "", (sw, sh), score_threshold=0.6)
    tops, bots = [], []
    for k in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (k + 0.5) / samples))
        ok, frame = cap.read()
        if not ok:
            continue
        _, faces = det.detect(cv2.resize(frame, (sw, sh)))
        if faces is None or not len(faces):
            continue
        f = max(faces, key=lambda f: f[2] * f[3])
        # YuNet kutusu alından çeneye; saç ve boyun için biraz genişlet
        tops.append(max(0.0, (f[1] - 0.25 * f[3]) / sh))
        bots.append(min(1.0, (f[1] + 1.15 * f[3]) / sh))
    cap.release()
    if not tops:
        return {"found": 0}
    tops.sort(), bots.sort()
    q = lambda a, p: a[min(len(a) - 1, int(p * len(a)))]
    return {"top": round(q(tops, 0.15), 3), "bottom": round(q(bots, 0.85), 3), "found": len(tops)}
