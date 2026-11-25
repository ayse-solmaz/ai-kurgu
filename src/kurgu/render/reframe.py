"""En-boy oranı dönüşümü: kırpma (yüz takipli) veya bulanık arka planla sığdırma."""

from __future__ import annotations


def _even(x: float) -> int:
    v = int(round(x))
    return v - (v % 2)


def piecewise_expr(points: list[tuple[float, float]], var: str = "t") -> str:
    """(zaman, değer) noktalarından doğrusal ara değerli ffmpeg ifadesi üretir."""
    if not points:
        return "0"
    if len(points) == 1:
        return f"{points[0][1]:.1f}"
    expr = f"{points[-1][1]:.1f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:]))):
        dt = max(t1 - t0, 1e-3)
        seg = f"({v0:.1f}+({v1 - v0:.1f})*({var}-{t0:.3f})/{dt:.3f})"
        expr = f"if(lt({var},{t1:.3f}),{seg},{expr})"
    return f"if(lt({var},{points[0][0]:.3f}),{points[0][1]:.1f},{expr})"


def downsample(points: list[tuple[float, float]], max_points: int = 90) -> list[tuple[float, float]]:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    out = [points[int(i * step)] for i in range(max_points)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def fit_filter(
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    mode: str = "crop",
    centers: list[tuple[float, float]] | None = None,
    label_in: str = "vin",
    label_out: str = "vfit",
) -> list[str]:
    """Kaynağı çıktı boyutuna getiren filtre satırları.

    centers: (çıktı zamanı, yatay merkez 0..1) noktaları; yüz takibinden gelir.
    """
    src_ar, out_ar = src_w / src_h, out_w / out_h
    scale = f"scale={out_w}:{out_h}:flags=lanczos,setsar=1"
    if abs(src_ar - out_ar) < 0.01:
        return [f"[{label_in}]{scale}[{label_out}]"]

    if mode == "crop":
        if src_ar > out_ar:  # kaynak daha geniş: yatay kırp
            cw = _even(src_h * out_ar)
            max_x = src_w - cw
            if centers:
                pts = [(t, min(max(c * src_w - cw / 2, 0), max_x)) for t, c in downsample(centers)]
                x = piecewise_expr(pts)
            else:
                x = f"{max_x / 2:.1f}"
            return [f"[{label_in}]crop={cw}:{src_h}:x='{x}':y=0,{scale}[{label_out}]"]
        ch = _even(src_w / out_ar)  # kaynak daha uzun: dikey kırp (üst-orta yüz için biraz yukarı)
        y = max(0, min(src_h - ch, int((src_h - ch) * 0.35)))
        return [f"[{label_in}]crop={src_w}:{ch}:0:{y},{scale}[{label_out}]"]

    # blur: arka planda bulanık kopya, önde tamamı
    bw, bh = _even(out_w / 4), _even(out_h / 4)
    return [
        f"[{label_in}]split=2[fitbg][fitfg]",
        f"[fitbg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
        f"gblur=sigma=6,eq=brightness=-0.06,scale={out_w}:{out_h},setsar=1[fitbgb]",
        f"[fitfg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1[fitfgs]",
        f"[fitbgb][fitfgs]overlay=(W-w)/2:(H-h)/2[{label_out}]",
    ]
