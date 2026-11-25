from types import SimpleNamespace as NS

from kurgu.agent.director import motion_target
from kurgu.generate.icons import _is_brand
from kurgu.generate.motion_gfx import best_card_position, best_text_position


def test_cards_avoid_face_band():
    allowed = ["top", "center", "bottom"]
    assert best_card_position({"found": 9, "top": 0.35, "bottom": 0.64}, allowed) == "top"
    assert best_card_position({"found": 9, "top": 0.08, "bottom": 0.40}, allowed) == "bottom"
    assert best_card_position({"found": 0}, allowed) is None  # yüz yoksa dokunma


def test_text_never_goes_to_caption_area():
    assert best_text_position({"found": 5, "top": 0.35, "bottom": 0.64}) == "top"
    assert best_text_position({"found": 5, "top": 0.12, "bottom": 0.40}) == "center"
    assert best_text_position({"found": 5, "top": 0.05, "bottom": 0.95}) in ("top", "center")


def test_motion_target_follows_content():
    plain = [NS(text="Bugün hava güzel.") for _ in range(12)]
    rich = [NS(text=f"Önce {i} adım, sonra yüzde {i * 10} fark.") for i in range(12)]
    assert motion_target(plain, 60, True) == 4  # yalnızca süre tabanı
    assert motion_target(rich, 60, True) == 6  # görselleştirilebilir an çok
    assert motion_target(rich, 20, True) <= 2  # kısa videoda üst sınır


def test_brand_icons_are_filtered():
    assert _is_brand("ph:open-ai-logo") and _is_brand("mdi:google") and _is_brand("simple:youtube")
    assert not _is_brand("ph:robot") and not _is_brand("mdi:bacteria")


def test_rejected_pick_is_salvaged_as_icon_card():
    from kurgu.agent.animator import MotionPick, salvage_pick
    from kurgu.generate.motion_gfx import list_templates

    tpls = list_templates()
    alt = salvage_pick(MotionPick(sentence_id=3, phrase="molekül yapısı", template="gauge",
                                  params={"value": 100, "label": "Kırılganlık", "icon": "dna"}), tpls)
    assert alt.template == "icon_pop" and alt.params == {"icon": "dna", "label": "Kırılganlık"}
    # ikon anahtarı yoksa ya da etiket yarım kalıyorsa kurtarılmaz
    assert salvage_pick(MotionPick(sentence_id=3, phrase="bir", template="process_steps", params={}), tpls) is None
    assert salvage_pick(MotionPick(sentence_id=3, phrase="vücutta bir", template="gauge",
                                   params={"icon": "dna", "label": "hem dayanıklı hem de"}), tpls) is None
