from kurgu.core.config import CaptionCfg
from kurgu.render.captions_ass import (
    OutText,
    OutWord,
    ass_color,
    ass_time,
    build_ass,
    build_srt,
    group_words,
    tr_upper,
)
from kurgu.render.reframe import fit_filter, piecewise_expr


def words(n=7):
    return [OutWord(f"k{i}", i * 0.5, i * 0.5 + 0.4) for i in range(n)]


def test_helpers():
    assert ass_color("#FFD60A") == "&H000AD6FF"
    assert ass_time(3723.456) == "1:02:03.46"
    assert tr_upper("istanbul ılık") == "İSTANBUL ILIK"


def test_grouping_respects_limits_and_punctuation():
    ws = [OutWord("Merhaba.", 0, 0.5), OutWord("Bugün", 0.6, 1.0), OutWord("çok", 1.1, 1.3),
          OutWord("güzel", 1.4, 1.8), OutWord("bir", 1.9, 2.0), OutWord("gün", 2.1, 2.4)]
    groups = group_words(ws, max_words=3, max_chars=40)
    assert [len(g) for g in groups] == [1, 3, 2]


def test_karaoke_has_one_event_per_word():
    cfg = CaptionCfg(style="karaoke", max_words=3, uppercase=True)
    ass = build_ass(words(6), cfg, 1080, 1920)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(events) == 6
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "\\fscx118" in events[0] and "K0" in events[0]  # büyük harf + vurgu


def test_texts_and_srt():
    cfg = CaptionCfg(style="classic")
    ass = build_ass(words(3), cfg, 1920, 1080, [OutText("Dikkat!", 0, 2, "hook"), OutText("Ayşe", 1, 3, "lower_third")])
    assert ",Hook," in ass and ",Lower," in ass and "\\move(" in ass
    srt = build_srt(words(3))
    assert srt.startswith("1\n00:00:00,000 --> ")


def test_piecewise_and_fit():
    e = piecewise_expr([(0, 100), (2, 300)])
    assert e.startswith("if(lt(t,0.000),100.0,") and "300.0" in e
    lines = fit_filter(1920, 1080, 1080, 1920, "crop", None, "a", "b")
    assert lines[0].startswith("[a]crop=608:1080") and lines[0].endswith("[b]")
    blur = fit_filter(1920, 1080, 1080, 1920, "blur", None, "a", "b")
    assert len(blur) == 4 and "gblur" in blur[1]
    same = fit_filter(1920, 1080, 1280, 720, "crop", None, "a", "b")
    assert len(same) == 1 and "crop" not in same[0]
