"""Altyazı temaları.

Tema, altyazının tüm görünüşünü (font, kalınlık, renkler, vurgu biçimi, boyut) tek adla belirler.
Fontlar `scripts/fetch_fonts.py` ile indirilen SIL OFL lisanslı fontlardır (assets/fonts).
"apple" teması Apple'ın tasarım dilinden esinlenir (SF Pro yerine açık lisanslı Inter,
Apple sistem renkleri, kapsül vurgu, yumuşak gölge); Apple'a ait font veya varlık kullanmaz.
"""

from __future__ import annotations

from ..core.config import CaptionCfg

# boyut: dikey videoda genişliğe, yatay videoda yüksekliğe oranı
CAPTION_THEMES: dict[str, dict] = {
    "apple": dict(
        label="Apple", font="Inter", weight=700, uppercase=False, primary="#FFFFFF", highlight="#0A84FF",
        highlight_mode="pill", outline=2, outline_color="#000000", outline_alpha=0x90, blur=3.0, shadow=0,
        size_v=0.074, size_h=0.05, words_v=3, words_h=6, chars_v=20, chars_h=40, style="karaoke",
    ),
    "bold": dict(
        label="Kalın", font="Montserrat", weight=900, uppercase=True, primary="#FFFFFF", highlight="#FFD60A",
        highlight_mode="color", outline=7, outline_color="#000000", outline_alpha=0, blur=0.0, shadow=2,
        size_v=0.085, size_h=0.052, words_v=3, words_h=6, chars_v=18, chars_h=36, style="karaoke",
    ),
    "neon": dict(
        label="Neon", font="Inter Tight", weight=800, uppercase=True, primary="#FFFFFF", highlight="#64D2FF",
        highlight_mode="glow", outline=3, outline_color="#0A84FF", outline_alpha=0x50, blur=4.0, shadow=0,
        size_v=0.08, size_h=0.05, words_v=2, words_h=5, chars_v=16, chars_h=34, style="karaoke",
    ),
    "minimal": dict(
        label="Sade", font="Inter", weight=600, uppercase=False, primary="#FFFFFF", highlight="#FFFFFF",
        highlight_mode="none", outline=2, outline_color="#000000", outline_alpha=0xA0, blur=4.0, shadow=0,
        size_v=0.056, size_h=0.042, words_v=5, words_h=8, chars_v=28, chars_h=48, style="classic",
        position="bottom",
    ),
}

_CFG_KEYS = {"font", "weight", "uppercase", "primary", "highlight", "highlight_mode", "outline", "outline_color",
             "outline_alpha", "blur", "shadow", "style", "position"}


def apply_caption_theme(cfg: CaptionCfg, name: str, width: int, height: int) -> CaptionCfg:
    """Temayı uygular; açık/kapalı durumu ve (tema belirtmiyorsa) konum korunur."""
    t = CAPTION_THEMES.get(name)
    if t is None:
        return cfg
    vertical = height > width
    upd = {k: v for k, v in t.items() if k in _CFG_KEYS}
    upd["theme"] = name
    upd["size"] = int(round((width if vertical else height) * (t["size_v"] if vertical else t["size_h"])))
    upd["max_words"] = t["words_v"] if vertical else t["words_h"]
    upd["max_chars"] = t["chars_v"] if vertical else t["chars_h"]
    if "position" in upd and upd["position"] == "bottom":
        upd["margin_v"] = int(height * (0.12 if vertical else 0.07))
    return cfg.model_copy(update=upd)


def theme_list() -> list[dict]:
    return [{"id": k, "label": v["label"], "font": v["font"], "highlight": v["highlight"],
             "highlight_mode": v["highlight_mode"]} for k, v in CAPTION_THEMES.items()]


def set_theme(tl, name: str):
    """Projenin görsel temasını değiştirir: altyazı görünümü + hareketli grafik teması.
    Animasyonlar bir sonraki render'da (fill_motions) yeni temayla yeniden üretilir."""
    if name not in CAPTION_THEMES:
        raise ValueError(f"Bilinmeyen tema: {name}. Mevcut: {', '.join(CAPTION_THEMES)}")
    tl.captions = apply_caption_theme(tl.captions, name, tl.output.width, tl.output.height)
    tl.theme = name
    return tl
