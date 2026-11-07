from server.services.motion_templates import get_template_for_effect


def test_zoom_combo_alternates_zoom_in_then_zoom_out():
    sequence = [get_template_for_effect("Zoom Combo", index) for index in range(4)]

    assert sequence == ["zoom_in", "zoom_out", "zoom_in", "zoom_out"]


def test_legacy_combo_alias_keeps_public_effect_contract():
    assert get_template_for_effect("Ken Burns", 0) == "zoom_combo"
    assert get_template_for_effect("Alternate", 0) == "zoom_in"
