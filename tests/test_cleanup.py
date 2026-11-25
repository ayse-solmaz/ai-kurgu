from kurgu.analyze.cutter import build_cleanup_cuts
from kurgu.analyze.fillers import analyze_disfluencies, is_filler_token, normalize
from kurgu.core.config import CleanupCfg
from kurgu.core.timeline import CutReason, Sentence, TimeMap, Transcript, Word, complement


def make_transcript(spec):
    """spec: [(text, start, end), ...] — cümleler None ile ayrılır."""
    words, sentences, cur = [], [], []
    for item in spec + [None]:
        if item is None:
            if cur:
                sentences.append(Sentence(id=len(sentences), word_ids=cur, text=" ".join(words[i].text for i in cur),
                                          start=words[cur[0]].start, end=words[cur[-1]].end))
                cur = []
            continue
        t, s, e = item
        words.append(Word(id=len(words), text=t, start=s, end=e))
        cur.append(len(words) - 1)
    return Transcript(language="tr", words=words, sentences=sentences)


def test_turkish_normalize_and_fillers():
    assert normalize("IIı,") == "ııı"
    assert normalize("İstanbul'da") == "istanbulda"
    assert is_filler_token("ııı", None)
    assert is_filler_token("eee", None)
    assert is_filler_token("hmm", None)
    assert is_filler_token("şey", "yani")
    assert not is_filler_token("şey", "bir")  # "bir şey" anlamlı
    assert not is_filler_token("o", None)
    assert not is_filler_token("yani", None)  # söylem belirteci: varsayılan korunur


def test_stutter_and_retake_detection():
    tr = make_transcript([
        ("Bugün", 0.0, 0.4), ("size", 0.5, 0.8), ("şunu", 0.9, 1.2), None,          # yarım kalan deneme
        ("Bugün", 2.0, 2.4), ("size", 2.5, 2.8), ("şunu", 2.9, 3.2), ("anlatacağım", 3.3, 4.0), None,
        ("ben", 5.0, 5.2), ("ben", 5.3, 5.5), ("ııı", 5.7, 6.2), ("geldim", 6.4, 7.0), None,
    ])
    stats = analyze_disfluencies(tr)
    assert stats == {"fillers": 1, "stutters": 1, "retakes": 1}
    assert all(tr.words[i].is_retake for i in (0, 1, 2))
    assert not tr.words[3].is_retake
    assert tr.words[7].is_stutter and not tr.words[8].is_stutter  # "ben ben" -> ilki
    assert tr.words[9].is_filler  # "ııı"


def test_cleanup_cuts_remove_silence_and_fillers():
    tr = make_transcript([
        ("Merhaba", 1.0, 1.5), ("arkadaşlar", 1.6, 2.2), ("eee", 3.0, 3.6), ("bugün", 3.8, 4.2),
        ("başlıyoruz", 4.3, 5.0), None,
        ("Hazır", 8.0, 8.4), ("mısınız", 8.5, 9.0), None,
    ])
    analyze_disfluencies(tr)
    cfg = CleanupCfg(silence_min=0.5, padding=0.1, remove_fillers=True)
    cuts = build_cleanup_cuts(tr, duration=10.0, cfg=cfg)
    cut_ranges = [(c.start, c.end) for c in cuts]
    keep = complement(cut_ranges, 0, 10)
    tm = TimeMap(keep)

    # tüm anlamlı kelimeler duruyor
    for w in tr.words:
        mid = (w.start + w.end) / 2
        if w.text == "eee":
            assert tm.to_out(mid, "strict") is None
        else:
            assert tm.to_out(mid, "strict") is not None, w.text

    reasons = {c.reason for c in cuts}
    assert CutReason.filler in reasons
    assert CutReason.trim in reasons  # baştaki/sondaki boşluk
    # 5.0 -> 8.0 arasındaki 3 sn'lik sessizlik kesilmiş olmalı
    assert any(c.start <= 5.2 and c.end >= 7.8 for c in cuts)
    assert tm.duration < 6.0


def test_cleanup_respects_vad_trimming():
    """Whisper kelimeyi sessizliğe uzatmışsa VAD ile kırpılmalı."""
    tr = make_transcript([("Merhaba", 1.0, 4.0), None])  # gerçekte konuşma 1.0-1.6
    analyze_disfluencies(tr)
    cfg = CleanupCfg(silence_min=0.3, padding=0.05)
    cuts = build_cleanup_cuts(tr, 6.0, cfg, vad=[(1.0, 1.6)])
    keep = complement([(c.start, c.end) for c in cuts], 0, 6.0)
    total = sum(b - a for a, b in keep)
    assert total < 1.2


def test_turkish_reduplication_is_not_a_stutter():
    tr = make_transcript([("kelime", 0.0, 0.4), ("kelime", 0.42, 0.8), ("ekrana", 0.85, 1.3), ("geliyor", 1.35, 1.8), None,
                          ("ben", 3.0, 3.2), ("ben", 3.25, 3.45), ("geldim", 3.5, 4.0), None])
    analyze_disfluencies(tr)
    assert not tr.words[0].is_stutter  # ikileme korunur
    assert tr.words[4].is_stutter  # işlev sözcüğü tekrarı kesilir


def test_restart_inside_one_sentence():
    words = "bu uygulama videonuzu izliyor ve, bu uygulama videonuzu izliyor ve gereksiz kısımları kesiyor.".split()
    tr = make_transcript([(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate(words)] + [None])
    stats = analyze_disfluencies(tr)
    assert stats["retakes"] == 1
    assert [w.is_retake for w in tr.words[:5]] == [True] * 5
    assert not any(w.is_retake for w in tr.words[5:])


def test_build_sentences_splits_on_punctuation_and_pause():
    from kurgu.analyze.asr import build_sentences

    ws = [Word(id=0, text="Merhaba.", start=0, end=0.5), Word(id=1, text="Bugün", start=0.6, end=1.0),
          Word(id=2, text="başlıyoruz", start=1.1, end=1.6), Word(id=3, text="Hazır", start=4.0, end=4.4),
          Word(id=4, text="mısınız?", start=4.5, end=5.0)]
    assert [s.word_ids for s in build_sentences(ws)] == [[0], [1, 2], [3, 4]]


def _cands(text: str, pause_after: int | None = None):
    from kurgu.analyze.fillers import restart_candidates

    spec, t = [], 0.0
    for k, w in enumerate(text.split()):
        spec.append((w, t, t + 0.3))
        t += 0.35 + (0.6 if k == pause_after else 0.0)
    tr = make_transcript(spec + [None])
    cands = restart_candidates(tr, [w.id for w in tr.words])
    return [" ".join(tr.words[i].text for i in c["cut"]) for c in cands]


def test_restart_candidate_cuts_only_the_first_try():
    cuts = _cands("bu süreci dışarıdan ısı kaynağı kullanmadan, kendi enerjisini yakarak, enerjisini içeriden yakarak başlatır.")
    assert cuts == ["enerjisini yakarak,"]


def test_restart_candidate_ignores_meaningful_content():
    assert _cands("Telaşla özel proteinler üretirler ancak bu üretim bağışıklık sisteminin dikkatini çeker", pause_after=5) == []
    assert _cands("kişiler için de ateş risklidir. Ancak sağlıklı bir birey için ateş bir müttefik.") == []


def _tr_from(sentences: list[str], gap: float = 0.6):
    from kurgu.core.timeline import Sentence, Transcript, Word

    words, sents, t = [], [], 0.0
    for sid, text in enumerate(sentences):
        ids = []
        for tok in text.split():
            words.append(Word(id=len(words), text=tok, start=t, end=t + 0.3))
            ids.append(len(words) - 1)
            t += 0.35
        sents.append(Sentence(id=sid, word_ids=ids, text=text, start=words[ids[0]].start, end=words[ids[-1]].end))
        t += gap
    return Transcript(words=words, sentences=sents, duration=t)


def test_retakes_with_spoken_cues_keep_only_last_attempt():
    from kurgu.analyze.fillers import mark_retakes

    tr = _tr_from([
        "Gelecekte aşıları yapay zeka saniyeler içinde...",  # 0: yarım deneme
        "Bir daha.",  # 1: işaret
        "Gelecekte aşılar saniyeler içinde tasarlanabilecek mi?",  # 2: tutulur
        "Sorun şuydu, bir daha.",  # 3: deneme + işaret
        "Sorun şuydu.",  # 4: tutulur
        "Bu alanda geliştirilen yapay zeka modelleri süreci kısaltıyor.",  # 5: tutulur (tekrar değil)
        "Araştırmacılar potansiyel bileşen kombinasyonlarını modele girdiğinde yapay zeka hanginin...",  # 6
        "Bir daha.",  # 7
        "Araştırmacılar potansiyel bileşen kombinasyonlarını bu modele yapay zeka modeline girdiklerinde tahmin ediyor.",  # 8
        "Bir daha.",  # 9
        "İşte",  # 10: tek başına dolgu
        "araştırmacılar potansiyel birleşen kombinasyonlarını bu modele girdiğinde yapay zeka tahmin edebiliyor.",  # 11
        "Böylece sadece en umut verici formülasyonlar test ediliyor.",  # 12
    ])
    mark_retakes(tr)
    cut = {s.id for s in tr.sentences if all(tr.words[i].is_retake for i in s.word_ids)}
    assert cut == {0, 1, 3, 6, 7, 8, 9, 10}
