import wave
from types import SimpleNamespace

from kurgu.agent.screenwriter import Beat, ScriptBrief, balance
from kurgu.generate.music import generate_procedural, spec_for
from kurgu.render.render import glass_backdrop


def _brief(**kw):
    base = dict(logline="x", structure="a -> b", title="t", hook="h", description="d", hashtags=["#a"], tone="t",
                pacing="orta", broll_density="orta", text_density="orta", zoom_intensity="hafif", caption_theme="apple",
                visual_style="clean", avoid="text", music_mood="inspiring", notes="")
    base.update(kw)
    return ScriptBrief(**base)


def _slots(n):
    return [[SimpleNamespace(id=i)] for i in range(n)]


def test_balance_fixes_bad_templates_and_runs():
    beats = [Beat(slot=i + 1, sentence_id=i, phrase="p", purpose="açıklama", visual="b-roll", idea="x") for i in range(6)]
    beats[1] = Beat(slot=2, sentence_id=1, phrase="p", purpose="vurgu", visual="animasyon", template="yok_boyle", idea="y")
    beats[3] = Beat(slot=4, sentence_id=99, phrase="p", purpose="vurgu", visual="animasyon", template="cta", idea="z")
    out = balance(beats, _slots(6), _brief(broll_density="çok"))
    assert all(b.visual != "animasyon" for b in out)  # bilinmeyen ve otomatik (cta) şablonlar düşer
    assert out[3].sentence_id == 3  # dilim dışı cümle düzeltilir
    for a, b, c in zip(out, out[1:], out[2:]):
        assert not (a.visual == b.visual == c.visual != "konuşmacı")  # üç kez art arda aynı tür yok


def test_balance_caps_broll_ratio_and_full_templates():
    beats = [Beat(slot=i + 1, sentence_id=i, phrase="p", purpose="açıklama",
                  visual="b-roll" if i % 2 else "animasyon", template="" if i % 2 else "timeline", idea="x")
             for i in range(10)]
    out = balance(beats, _slots(10), _brief(broll_density="az"))
    assert sum(b.visual == "b-roll" for b in out) <= 2  # az: %20
    assert sum(b.template == "timeline" for b in out) <= 2  # tam ekran sınırı


def test_music_spec_is_english_and_bounded():
    s = spec_for("upbeat", 30, {"genre": "lo-fi hip hop", "instruments": "soft piano, warm pads", "bpm": 300}, seed="p")
    assert "lo-fi hip hop" in s.caption and "instrumental" in s.caption and "no vocals" in s.caption
    assert s.bpm == 140
    assert spec_for("calm", 30, {}, seed="p").key() == spec_for("calm", 30, {}, seed="p").key()


def test_procedural_music_writes_stereo_wav(tmp_path):
    out = generate_procedural(spec_for("inspiring", 6, {"genre": "ambient electronic", "instruments": "pads"}), tmp_path / "m.wav")
    with wave.open(str(out)) as w:
        assert w.getnchannels() == 2 and abs(w.getnframes() / w.getframerate() - 6) < 0.05


def test_glass_backdrop_graph():
    g = glass_backdrop("v0", "mg3", 3, 2.0, 5.5, 1080)
    assert g[0] == "[mg3]split[mg3o][mg3m]"
    assert "alphaextract" in g[1] and "trim=start=2.000:end=5.500" in g[3]
    assert g[-1].endswith("[vg3]") and "between(t,2.000,5.500)" in g[-1]


def test_number_grounding_moves_rejects_and_converts():
    from types import SimpleNamespace as NS

    from kurgu.agent.animator import MotionPick, ground_pick
    from kurgu.generate.motion_gfx import list_templates

    tpls = list_templates()
    sents = [NS(id=1, text="Ateş beynimize ulaşarak iç ısımızı yükseltir."),
             NS(id=2, text="Her bir derecelik artış için %10 daha fazla enerji harcarız."),
             NS(id=3, text="Elbette 40 derecenin üzerindeki ateş tehlikelidir."),
             NS(id=4, text="Ateşi agresif düşürmek ölüm oranlarını yükseltir.")]
    # sayı başka cümlede söyleniyor -> oraya taşınır
    p, note = ground_pick(MotionPick(sentence_id=1, phrase="iç ısımızı", template="gauge",
                                     params={"value": 40, "unit": "°C", "label": "Tehlike"}), tpls["gauge"], sents, tpls)
    assert p.sentence_id == 3 and p.template == "gauge" and "taşındı" in note
    # sayı hiç söylenmiyor -> reddedilir
    p, note = ground_pick(MotionPick(sentence_id=4, phrase="ölüm", template="gauge",
                                     params={"value": 80, "label": "Ölüm riski"}), tpls["gauge"], sents, tpls)
    assert p is None and "geçmiyor" in note
    # termometre ama sıcaklık değil -> büyük sayı (yüzde korunur)
    p, note = ground_pick(MotionPick(sentence_id=2, phrase="%10", template="gauge",
                                     params={"value": 10, "label": "enerji"}), tpls["gauge"], sents, tpls)
    assert p.template == "gauge"  # 'derecelik' sıcaklık bağlamı -> termometre kalır
    p, _ = ground_pick(MotionPick(sentence_id=4, phrase="ölüm", template="big_number",
                                  params={"value": 10, "prefix": "%", "label": "x"}), tpls["big_number"], sents, tpls)
    assert p.sentence_id == 2
