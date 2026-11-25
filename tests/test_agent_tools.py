import json

import pytest

from kurgu.agent.planner import KeptSentence, locate_phrase
from kurgu.agent.tools import ToolContext, dispatch
from kurgu.core.config import load_preset
from kurgu.core.timeline import SourceInfo, Sentence, Timeline, Transcript, Word


class FakeProject:
    pass


def make():
    words = [Word(id=i, text=t, start=i * 1.0, end=i * 1.0 + 0.8) for i, t in enumerate(
        "Merhaba bugün uzay ve galaksiler hakkında konuşacağız sonra kara delikler".split())]
    tr = Transcript(language="tr", words=words, sentences=[
        Sentence(id=0, word_ids=list(range(0, 7)), text="", start=0, end=6.8),
        Sentence(id=1, word_ids=list(range(7, 10)), text="", start=7, end=9.8),
    ])
    src = SourceInfo(path="x.mp4", duration=10.0, width=1920, height=1080, fps=30)
    tl = Timeline.from_preset(load_preset("youtube"), src)
    return ToolContext(FakeProject(), tl, tr)


def test_cut_range_in_output_time_spanning_cuts():
    ctx = make()
    ctx.tl.add_cut(2.0, 4.0)  # çıktı: [0-2] + [4-10] -> 8 sn
    assert ctx.tl.output_duration() == pytest.approx(8)
    out = dispatch(ctx, "cut_range", {"start": 1.0, "end": 3.0})  # çıktıda 1-3 = kaynak 1-2 ve 4-5
    assert "Kesildi" in out
    assert ctx.tl.keep_ranges() == [(0, 1.0), (5.0, 10.0)]
    assert ctx.changed


def test_add_broll_anchors_to_words_and_survives_new_cuts():
    ctx = make()
    dispatch(ctx, "add_broll", {"at": 3.0, "duration": 2.0, "visual": "spiral galaxy"})
    br = ctx.tl.brolls[0]
    assert br.anchor.word_start == 3 and br.id == "b1"
    before = ctx.out_span(br.anchor)
    ctx.tl.add_cut(0.0, 1.0)  # öne kesim eklenince b-roll 1 sn erken gelmeli
    after = ctx.out_span(br.anchor)
    assert after[0] == pytest.approx(before[0] - 1.0)


def test_overview_find_and_remove():
    ctx = make()
    dispatch(ctx, "add_text", {"at": 1, "text": "Uzay!"})
    ov = json.loads(dispatch(ctx, "get_overview", {}))
    assert ov["texts"][0]["text"] == "Uzay!"
    found = json.loads(dispatch(ctx, "find_text", {"query": "kara delik"}))
    assert found and found[0]["sentence_id"] == 1
    assert "silindi" in dispatch(ctx, "remove_text", {"id": "t1"})
    assert dispatch(ctx, "unknown_tool", {}).startswith("Hata")
    assert dispatch(ctx, "add_zoom", {"at": "abc"}).startswith("Hata")


def test_locate_phrase_fuzzy():
    ctx = make()
    s = KeptSentence(0, list(range(0, 7)), "", 0, 7)
    assert locate_phrase(ctx.tr, s, "uzay ve galaksiler") == (2, 4)
    assert locate_phrase(ctx.tr, s, "galaksilerin hakkında") == (4, 5)


def test_hook_trimming():
    from kurgu.agent.director import _is_dangling, _trim_dangling

    assert _is_dangling("Ateş, sadece bir semptom değil — bir")
    assert _trim_dangling("Ateş, sadece bir semptom değil — bir") == "Ateş, sadece bir semptom değil"
    assert not _is_dangling("Ateş sadece bir semptom değil")
    assert not _is_dangling("Ateş, vücudun savunması!")
