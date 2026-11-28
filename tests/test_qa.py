import numpy as np

from kurgu.qa import _speech_in


def test_speech_energy_detects_clipped_onset():
    sr = 16000
    audio = np.zeros(sr * 2, np.float32)
    t = np.arange(int(0.5 * sr)) / sr
    audio[sr:sr + len(t)] = 0.3 * np.sin(2 * np.pi * 220 * t)  # 1.0-1.5 sn arası konuşma
    assert _speech_in(audio, sr, 0.2, 0.9) < 0.1  # sessizlik
    assert _speech_in(audio, sr, 1.05, 1.3) > 0.9  # sesli bölüm: buradan kesmek kelimeyi kırpar
    assert _speech_in(None, sr, 0, 1) == 0.0


def test_repeated_phrase_anchors_nearest_occurrence():
    from kurgu.agent.animator import content_anchor
    from tests.test_story_timing import _tr

    tr, sents = _tr(["Yapay zeka geliyor.", "Arada başka konu.", "Başka bir cümle.", "Yapay zeka her şeyi değiştirdi."])
    first = content_anchor(tr, sents, ["Yapay", "Zeka"], near=0.0)
    last = content_anchor(tr, sents, ["Yapay", "Zeka"], near=sents[3].out_start)
    assert first[0].id == 0 and last[0].id == 3
