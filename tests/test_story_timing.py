from kurgu.agent import planner
from kurgu.agent.animator import content_anchor
from kurgu.agent.planner import KeptSentence, StoryPick, _story_highlight
from kurgu.core.timeline import Sentence, Transcript, Word


def _tr(sentences: list[str], gap: float = 0.4):
    words, sents, t = [], [], 0.0
    for si, text in enumerate(sentences):
        ids = []
        for tok in text.split():
            words.append(Word(id=len(words), text=tok, start=t, end=t + 0.4))
            ids.append(len(words) - 1)
            t += 0.45
        sents.append(Sentence(id=si, word_ids=ids, text=text, start=words[ids[0]].start, end=words[ids[-1]].end))
        t += gap
    tr = Transcript(words=words, sentences=sents, duration=t)
    kept = [KeptSentence(s.id, s.word_ids, s.text, s.start, s.end) for s in sents]
    return tr, kept


def test_card_anchors_where_its_words_are_spoken():
    tr, sents = _tr(["Bugün aşılardan konuşacağız.", "Asıl sorun dev bir darboğaz vardı.",
                     "Bu da deneme yanılma süreci demekti.", "Sonunda bir model geliştirildi."])
    s, wid = content_anchor(tr, sents, ["deneme", "yanılma"])
    assert s.id == 2 and tr.words[wid].text == "deneme"
    s, wid = content_anchor(tr, sents, ["Dev", "darboğaz"])
    assert s.id == 1 and tr.words[wid].text == "dev"
    assert content_anchor(tr, sents, ["kuantum", "bilgisayar"]) is None  # konuşmada yoksa bağlanmaz


def test_story_highlight_keeps_hook_and_ending_and_respects_limit(monkeypatch):
    texts = [f"cümle {i} " + "uzun " * (3 + i % 4) for i in range(20)]
    tr, sents = _tr(texts)
    picked = [0, 3, 4, 9, 12, 15, 19]
    monkeypatch.setattr(planner, "chat_json", lambda *a, **k: StoryPick(sentence_ids=picked, reason="x"))
    chosen = _story_highlight(None, tr, sents, max_s=12.0)
    assert chosen[0].id == 0 and chosen[-1].id == 19  # açılış ve kapanış korunur
    total = sum(x.out_end - x.out_start for x in chosen) + 0.3 * (len(chosen) - 1)
    assert total <= 12.0
    assert [x.id for x in chosen] == sorted(x.id for x in chosen)


def test_dependent_sentence_brings_its_antecedent_or_is_dropped():
    from kurgu.agent.planner import _fix_dependencies

    tr, sents = _tr(["Aşılar hızlanabilir mi?", "RNA molekülleri kırılgandır.", "Onları dayanıklı yapmak zordu.",
                     "Araştırmacılar bir model geliştirdi.", "Ayrıca maliyet düştü.", "Sonuç yüzde yetmiş beş iyileşme."])
    dur = lambda cs: sum(x.out_end - x.out_start for x in cs)
    by = {x.id: x for x in sents}
    got = _fix_dependencies([by[0], by[2], by[3], by[5]], sents, dur, max_s=100)
    assert [x.id for x in got] == [0, 1, 2, 3, 5]  # "Onları" öncülünü getirdi
    picked = [by[0], by[2], by[3], by[5]]
    got = _fix_dependencies(picked, sents, dur, max_s=dur(picked))
    assert [x.id for x in got] == [0, 3, 5]  # öncül sığmıyor: "Onları" cümlesi çıkarıldı


def test_story_keep_does_not_leak_previous_word():
    from kurgu.agent.planner import _story_keep

    tr, sents = _tr(["Bu bir bilim kurgu değil.", "Araştırmacılar bir model geliştirdi."], gap=0.0)
    (a, b), = _story_keep(tr, [sents[1]])
    prev_end = tr.words[sents[0].word_ids[-1]].end
    assert a > prev_end  # önceki cümlenin son kelimesi ("değil.") sızmaz


def test_unfinished_sentence_gets_its_continuation_or_is_dropped():
    from kurgu.agent.planner import _fix_dependencies

    tr, sents = _tr(["Aşılar hızlanabilir mi?", "Bilim insanları binlerce farklı", "formülü tek tek test ediyordu.",
                     "Sonuç büyük bir iyileşme."])
    dur = lambda cs: sum(x.out_end - x.out_start for x in cs)
    by = {x.id: x for x in sents}
    assert [x.id for x in _fix_dependencies([by[0], by[1], by[3]], sents, dur, 100)] == [0, 1, 2, 3]
    tight = [by[0], by[1], by[3]]
    assert [x.id for x in _fix_dependencies(tight, sents, dur, dur(tight))] == [0, 3]
