from kurgu.core.config import CaptionCfg, load_preset
from kurgu.core.timeline import Cut, SourceInfo, Timeline, Transcript, Word
from kurgu.generate.greenscreen import _phrase_hits, auto_place
from kurgu.render.caption_themes import CAPTION_THEMES, apply_caption_theme, set_theme
from kurgu.render.captions_ass import OutWord, build_ass


def _tl(dur=40.0):
    return Timeline.from_preset(load_preset("shorts"), SourceInfo(path="x.mp4", duration=dur, width=1920, height=1080,
                                                                  fps=30, has_audio=True))


def _tr(text: str, step=0.4):
    words = [Word(id=i, text=t, start=i * step, end=i * step + step * 0.9) for i, t in enumerate(text.split())]
    return Transcript(words=words, duration=words[-1].end)


def test_theme_applies_fonts_and_sizes():
    c = apply_caption_theme(CaptionCfg(), "apple", 1080, 1920)
    assert c.font == "Inter" and c.highlight_mode == "pill" and c.theme == "apple"
    assert c.size == round(1080 * CAPTION_THEMES["apple"]["size_v"])
    h = apply_caption_theme(CaptionCfg(), "apple", 1920, 1080)
    assert h.max_words > c.max_words  # yatay videoda satır başına daha çok kelime


def test_pill_highlight_resets_with_blur():
    c = apply_caption_theme(CaptionCfg(style="karaoke"), "apple", 1080, 1920)
    ass = build_ass([OutWord("Merhaba", 0, 0.5), OutWord("dünya", 0.5, 1.0)], c, 1080, 1920)
    line = [l for l in ass.splitlines() if l.startswith("Dialogue")][0]
    assert "\\bord" in line and "\\3a&H00&" in line  # opak kapsül
    assert "{\\r\\blur3}" in line  # vurgudan sonra yumuşak gölgeye dönüş
    assert "Style: Caption,Inter,80," in ass and ",700,0,0,0," in ass  # sayısal kalınlık


def test_preset_default_theme_and_set_theme():
    tl = _tl()
    assert tl.theme == "apple" and tl.captions.font == "Inter"
    set_theme(tl, "bold")
    assert tl.theme == "bold" and tl.captions.font == "Montserrat" and tl.captions.uppercase


def test_phrase_hits_turkish_suffixes():
    words = _tr("Kanala ABONE olmayı ve videoyu beğenmeyi unutmayın").words
    assert _phrase_hits(words, "abone ol") == [1]
    assert _phrase_hits(words, "beğen") == [5]
    assert _phrase_hits(words, "takip et") == []


def test_auto_place_cta_skips_cut_words_and_duplicates():
    text = ("bugün ateşi konuşacağız " * 10 + "beğenmeyi unutmayın " + "devam ediyoruz " * 30
            + "beğenmeyi unutmayın " + "ara " * 45 + "abone olun " + "son " * 20)
    tr = _tr(text)
    tl = _tl(tr.duration + 1)
    first_like = next(w for w in tr.words if w.text == "beğenmeyi")
    tl.cuts = [Cut(start=first_like.start - 0.01, end=first_like.end + 0.01, reason="manual")]  # ilk çağrı kesildi
    placed = auto_place(tl, tr)
    variants = [m.params.get("variant") for m in placed]
    assert variants.count("like") == 1 and "subscribe" in variants
    like = next(m for m in placed if m.params["variant"] == "like")
    assert like.anchor.word_start != first_like.id
    assert all(m.template == "cta" for m in placed)
    assert len(placed) == 2  # birbirine yakın veya çakışan çağrılar tek animasyon
