import pytest

from kurgu.core.config import load_preset
from kurgu.core.timeline import (
    Anchor,
    CutReason,
    SourceInfo,
    TimeMap,
    Timeline,
    Word,
    complement,
    intersect,
    merge_ranges,
)


def src(duration=20.0):
    return SourceInfo(path="x.mp4", duration=duration, width=1920, height=1080, fps=30)


def test_merge_ranges_with_gap():
    assert merge_ranges([(0, 1), (1.2, 2), (5, 6)], gap=0.3) == [(0, 2), (5, 6)]
    assert merge_ranges([(3, 4), (0, 1), (0.5, 2)]) == [(0, 2), (3, 4)]


def test_complement_and_intersect():
    assert complement([(1, 2), (4, 5)], 0, 6) == [(0, 1), (2, 4), (5, 6)]
    assert intersect([(0, 3), (5, 8)], [(2, 6)]) == [(2, 3), (5, 6)]


def test_time_map_roundtrip():
    tm = TimeMap([(0, 2), (5, 8), (10, 11)])
    assert tm.duration == pytest.approx(6)
    assert tm.to_out(1) == pytest.approx(1)
    assert tm.to_out(6) == pytest.approx(3)  # 2 + (6-5)
    assert tm.to_out(3) is None  # kesilmiş bölge
    assert tm.to_out(3, "next") == pytest.approx(2)
    assert tm.to_out(3, "prev") == pytest.approx(2)
    assert tm.to_out(9, "prev") == pytest.approx(5)
    assert tm.to_src(3) == pytest.approx(6)
    assert tm.range_to_out(1, 6) == pytest.approx((1, 3))
    assert tm.range_to_out(2.5, 4.5) is None  # tamamen kesik


def test_keep_ranges_and_restore():
    tl = Timeline.from_preset(load_preset("youtube"), src(20))
    tl.add_cut(0, 1, CutReason.trim)
    tl.add_cut(5, 7, CutReason.silence)
    tl.add_cut(6, 9, CutReason.filler)  # örtüşen kesim
    assert tl.keep_ranges() == [(1, 5), (9, 20)]
    assert tl.output_duration() == pytest.approx(15)
    tl.restore(7.5, 8.0)
    assert tl.keep_ranges() == [(1, 5), (7.5, 8.0), (9, 20)]


def test_anchor_follows_words_after_cuts():
    """Kelimeye bağlı bir öğe, önüne kesim eklenince çıktıda doğru yere kaymalı."""
    words = {0: Word(id=0, text="merhaba", start=4.0, end=4.5), 1: Word(id=1, text="dünya", start=4.6, end=5.0)}
    anchor = Anchor(word_start=0, word_end=1)
    tl = Timeline.from_preset(load_preset("shorts"), src(10))
    a, b = anchor.resolve(words)
    assert tl.time_map().range_to_out(a, b) == pytest.approx((4.0, 5.0))
    tl.add_cut(1.0, 3.0)
    assert tl.time_map().range_to_out(a, b) == pytest.approx((2.0, 3.0))


def test_anchor_falls_back_to_seconds():
    assert Anchor(start=1.0, end=2.0).resolve({}) == (1.0, 2.0)
    with pytest.raises(ValueError):
        Anchor(word_start=99).resolve({})


def test_concurrent_saves_do_not_overwrite_each_other(tmp_path, monkeypatch):
    """İki süreç (ör. masaüstü uygulaması ve CLI) aynı projeye sırayla kaydederse sürümler kaybolmamalı."""
    import kurgu.core.project_store as ps
    from kurgu.core.project_store import Project

    monkeypatch.setattr(ps, "projects_root", lambda: tmp_path)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    a = Project.create(video, "youtube", "test")
    tl = Timeline.from_preset(load_preset("youtube"), src(10))
    a.save_timeline(tl, note="ilk")
    b = Project.open(a.dir)          # ikinci süreç, bellekteki bilgi v1
    a.save_timeline(tl, note="A")    # v2
    v = b.save_timeline(tl, note="B")  # eskiden v2'nin üzerine yazıyordu
    assert v == 3
    assert [h["note"] for h in b.history()] == ["ilk", "A", "B"]


def test_anchor_with_only_start_word():
    words = {5: Word(id=5, text="ateş", start=2.0, end=2.4)}
    assert Anchor(word_start=5).resolve(words) == (2.0, 2.4)
