"""Animasyonlu altyazı ve yazı katmanları (ASS / libass) + SRT dışa aktarım.

ASS efektleriyle ffmpeg içinde, ek render motoru olmadan şunlar yapılır:
- karaoke: konuşulan kelime renklenir ve "pop" yapar (Shorts tarzı)
- pop: kelime grubu büyüyerek belirir
- classic: alt yazı, yumuşak geçiş
- hook / title / lower_third / chapter / pop yazıları
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.config import CaptionCfg
from ..core.timeline import TimeMap, Transcript


@dataclass
class OutWord:
    text: str
    start: float  # çıktı zamanı
    end: float


@dataclass
class OutText:
    text: str
    start: float
    end: float
    style: str  # hook | title | lower_third | pop | chapter
    position: str = "top"


# --------------------------------------------------------------------------- yardımcılar


def tr_upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")


# --------------------------------------------------------------------------- kelimeleri eşleme


def map_words(tr: Transcript, tm: TimeMap) -> list[OutWord]:
    """Kesimlerden sonra görünür kalan kelimeleri çıktı zamanına çevirir."""
    out: list[OutWord] = []
    for w in tr.words:
        mid = (w.start + w.end) / 2
        if tm.to_out(mid, "strict") is None:
            continue
        r = tm.range_to_out(w.start, w.end)
        if r is None:
            continue
        out.append(OutWord(w.text, round(r[0], 3), round(r[1], 3)))
    # Zaman damgası düzeltme: çakışmaları gider
    for a, b in zip(out, out[1:]):
        if a.end > b.start:
            a.end = b.start
    return out


_SENT_END = (".", "?", "!", "…", ":")


def group_words(words: list[OutWord], max_words: int, max_chars: int, max_gap: float = 0.6) -> list[list[OutWord]]:
    groups: list[list[OutWord]] = []
    cur: list[OutWord] = []
    chars = 0
    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            too_long = len(cur) >= max_words or chars + 1 + len(w.text) > max_chars
            if too_long or gap > max_gap or cur[-1].text.endswith(_SENT_END):
                groups.append(cur)
                cur, chars = [], 0
        cur.append(w)
        chars += len(w.text) + (1 if chars else 0)
    if cur:
        groups.append(cur)
    return groups


# --------------------------------------------------------------------------- ASS üretimi


def _styles(cfg: CaptionCfg, w: int, h: int) -> list[str]:
    def style(name, font, size, primary, outline_c, back, bold, border_style, outline, shadow, align, ml, mr, mv):
        return (
            f"Style: {name},{font},{size},{primary},{primary},{outline_c},{back},"
            f"{bold if isinstance(bold, int) and not isinstance(bold, bool) else (-1 if bold else 0)},0,0,0,100,100,0,0,"
            f"{border_style},{outline},{shadow},{align},{ml},{mr},{mv},1"
        )

    if cfg.position == "center":
        align, mv = 2, int(h * 0.27)
    elif cfg.position == "top":
        align, mv = 8, max(cfg.margin_v, int(h * 0.08))
    else:
        align, mv = 2, cfg.margin_v
    base = min(w, h)
    side = int(w * 0.07)
    black = ass_color("#000000")
    white = ass_color("#FFFFFF")
    hl = ass_color(cfg.highlight)
    hx = cfg.highlight.lstrip("#")
    lum = (0.299 * int(hx[0:2], 16) + 0.587 * int(hx[2:4], 16) + 0.114 * int(hx[4:6], 16)) / 255 if len(hx) == 6 else 1
    on_hl = white if lum < 0.6 else black  # vurgu zemini üstündeki yazı (Apple mavisinde beyaz, sarıda siyah)
    return [
        style("Caption", cfg.font, cfg.size, ass_color(cfg.primary), ass_color(cfg.outline_color, cfg.outline_alpha),
              ass_color("#000000", 0x70), cfg.weight, 1, cfg.outline, cfg.shadow, align, side, side, mv),
        style("Hook", cfg.font, int(base * 0.075), on_hl, hl, hl, True, 3, int(base * 0.018), 0, 8,
              side, side, int(h * 0.12)),
        style("Title", cfg.font, int(base * 0.09), white, black, ass_color("#000000", 0x60), True, 1,
              int(base * 0.008), 2, 5, side, side, 0),
        style("Lower", "Arial", int(base * 0.045), white, ass_color("#111111", 0x30),
              ass_color("#111111", 0x30), True, 3, int(base * 0.014), 0, 1, int(w * 0.05), side, int(h * 0.12)),
        style("Chapter", "Arial", int(base * 0.035), on_hl, hl, hl, True, 3, int(base * 0.01), 0, 7,
              int(w * 0.04), side, int(h * 0.04)),
        style("Pop", cfg.font, int(base * 0.11), hl, black, black, True, 1, int(base * 0.01), 3, 5, side, side, 0),
    ]


def _reset(cfg: CaptionCfg) -> str:
    return rf"{{\r\blur{cfg.blur:g}}}" if cfg.blur else r"{\r}"


def _highlight(t: str, cfg: CaptionCfg, hl: str) -> str:
    """Konuşulan kelimenin vurgusu (temaya göre)."""
    end = _reset(cfg)
    pop = r"\fscx110\fscy110\t(0,100,\fscx100\fscy100)"
    if cfg.highlight_mode == "pill":
        # Apple tarzı: kelimenin arkasında renkli kapsül. Kalın kenarlık (\bord) + bulanıklık
        # köşeleri yumuşatır; kenarlık rengi vurgu rengi olur.
        pad = max(6, int(cfg.size * 0.22))
        gap = rf"{{\fscx45}}\h{end}"  # kapsül komşu kelimelere değmesin
        return gap + rf"{{\3c{hl}\3a&H00&\bord{pad}\blur2\shad0\1c&HFFFFFF&{pop}}}{t}{end}" + gap
    if cfg.highlight_mode == "glow":  # neon hale
        return rf"{{\1c&HFFFFFF&\3c{hl}\3a&H00&\bord{max(3, cfg.outline + 2)}\blur7{pop}}}{t}{end}"
    if cfg.highlight_mode == "none":
        return t
    return rf"{{\1c{hl}\fscx118\fscy118\t(0,90,\fscx100\fscy100)}}{t}{end}"


def _fit(line: str, texts: list[str], cfg: CaptionCfg, width: int) -> str:
    """Satır kareye sığmıyorsa (uzun kelime/büyük font) yatayda sıkıştırır; vurgu ölçekleri de orantılanır."""
    chars = sum(len(t) for t in texts) + max(len(texts) - 1, 0)
    k = 0.66 if cfg.uppercase else 0.56
    est = chars * cfg.size * k * (cfg.weight / 700) ** 0.3
    limit = width * 0.86
    if est <= limit:
        return line
    fit = max(55, int(100 * limit / est))
    line = re.sub(r"\\fscx(\d+)", lambda m: f"\\fscx{int(int(m.group(1)) * fit / 100)}", line)
    line = line.replace("\\r", f"\\r\\fscx{fit}")
    return f"{{\\fscx{fit}}}" + line


def _caption_events(words: list[OutWord], cfg: CaptionCfg, width: int = 1080) -> list[str]:
    events: list[str] = []
    groups = group_words(words, cfg.max_words, cfg.max_chars)
    hl = ass_color(cfg.highlight)

    def fmt(text: str) -> str:
        text = tr_upper(text) if cfg.uppercase else text
        return ass_escape(text)

    soft = rf"{{\blur{cfg.blur:g}}}" if cfg.blur else ""

    for gi, g in enumerate(groups):
        g_start = g[0].start
        g_end = g[-1].end
        if gi + 1 < len(groups):
            nxt = groups[gi + 1][0].start
            if nxt - g_end < 0.5:
                g_end = nxt  # titreşimi önle: bir sonraki gruba kadar ekranda kalsın
        else:
            g_end += 0.3
        texts = [fmt(w.text) for w in g]

        if cfg.style == "karaoke":
            for i, w in enumerate(g):
                s = g_start if i == 0 else w.start
                e = g[i + 1].start if i + 1 < len(g) else g_end
                if e - s < 0.02:
                    continue
                parts = []
                for j, t in enumerate(texts):
                    parts.append(_highlight(t, cfg, hl) if j == i else t)
                intro = "{\\fad(40,0)}" if i == 0 else ""
                body = _fit(f"{soft}{intro}{' '.join(parts)}", texts, cfg, width)
                events.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Caption,,0,0,0,,{body}")
        elif cfg.style == "pop":
            text = " ".join(texts)
            fx = "{\\fscx70\\fscy70\\t(0,110,\\fscx104\\fscy104)\\t(110,170,\\fscx100\\fscy100)}"
            events.append(f"Dialogue: 0,{ass_time(g_start)},{ass_time(g_end)},Caption,,0,0,0,,{soft}{fx}{text}")
        else:  # classic
            text = " ".join(texts)
            events.append(f"Dialogue: 0,{ass_time(g_start)},{ass_time(g_end)},Caption,,0,0,0,,{soft}{{\\fad(80,80)}}{text}")
    return events


def _text_events(texts: list[OutText], w: int, h: int, upper: bool) -> list[str]:
    events = []
    for t in texts:
        body = ass_escape(tr_upper(t.text) if upper and t.style in ("hook", "pop") else t.text)
        s, e = ass_time(t.start), ass_time(t.end)
        if t.style == "hook":
            fx = "{\\fad(100,200)\\fscx60\\fscy60\\t(0,160,\\fscx106\\fscy106)\\t(160,240,\\fscx100\\fscy100)}"
            if t.position == "center":  # yüz üstteyse hook ortaya iner (altyazı bölgesine değil)
                fx = f"{{\\an5\\pos({w // 2},{int(h * 0.46)})}}" + fx
            events.append(f"Dialogue: 2,{s},{e},Hook,,0,0,0,,{fx}{body}")
        elif t.style == "title":
            fx = "{\\fad(250,250)\\blur3\\t(0,250,\\blur0)}"
            events.append(f"Dialogue: 2,{s},{e},Title,,0,0,0,,{fx}{body}")
        elif t.style == "lower_third":
            y = int(h * 0.86)
            x_end = int(w * 0.05)
            fx = f"{{\\an1\\move({-int(w * 0.6)},{y},{x_end},{y},0,350)\\fad(0,250)}}"
            events.append(f"Dialogue: 2,{s},{e},Lower,,0,0,0,,{fx}{body}")
        elif t.style == "chapter":
            events.append(f"Dialogue: 2,{s},{e},Chapter,,0,0,0,,{{\\fad(200,300)}}{body}")
        else:  # pop
            pos = {"top": int(h * 0.22), "center": int(h * 0.45), "bottom": int(h * 0.72)}.get(t.position, int(h * 0.22))
            fx = (
                f"{{\\an5\\pos({w // 2},{pos})\\fscx0\\fscy0\\t(0,140,\\fscx115\\fscy115)"
                f"\\t(140,220,\\fscx100\\fscy100)\\fad(0,180)}}"
            )
            events.append(f"Dialogue: 2,{s},{e},Pop,,0,0,0,,{fx}{body}")
    return events


def build_ass(
    words: list[OutWord],
    cfg: CaptionCfg,
    width: int,
    height: int,
    texts: list[OutText] | None = None,
) -> str:
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *_styles(cfg, width, height),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = _caption_events(words, cfg, width) if cfg.enabled else []
    events += _text_events(texts or [], width, height, cfg.uppercase)
    return "\n".join(header + events) + "\n"


def build_srt(words: list[OutWord], max_words: int = 7, max_chars: int = 42) -> str:
    lines = []
    groups = group_words(words, max_words, max_chars)
    for i, g in enumerate(groups, 1):
        end = g[-1].end + 0.05
        if i < len(groups):
            end = min(end, groups[i][0].start)  # bir sonraki altyazıyla çakışmasın
        lines += [str(i), f"{srt_time(g[0].start)} --> {srt_time(end)}", " ".join(w.text for w in g), ""]
    return "\n".join(lines)
