from server.services.visual_requirement import (
    _is_organization_entity,
    _repair_institutional_requirement,
    build_visual_requirement,
)
from server.services.media_asset_policy import grounded_graphics_allowed
from server.config import normalize_generation_config


def test_nfl_and_leagues_are_organization_not_person():
    assert _is_organization_entity("The NFL")
    assert _is_organization_entity("NFL")
    assert _is_organization_entity("NBA")
    assert _is_organization_entity("FIFA")
    assert _is_organization_entity("Dallas Cowboys")

    repaired = _repair_institutional_requirement({
        "canonical_entity": "The NFL",
        "subject_type": "person",
    })
    assert repaired["subject_type"] == "organization"
    assert repaired["domain"] == "sports"


def test_grounded_graphics_allowed_default_and_flags():
    cfg = normalize_generation_config({
        "global_media_guarantee": True,
        "media_fallback_policy": "grounded_graphic",
        "allow_grounded_graphic_fallback": True,
        "allow_emergency_media": False,
    })
    assert grounded_graphics_allowed(cfg) is True

    # Even if strict flag was set or default
    cfg2 = normalize_generation_config({
        "global_media_guarantee": True,
        "media_fallback_policy": "grounded_graphic",
        "allow_grounded_graphic_fallback_strict": True,
        "allow_emergency_media": False,
    })
    assert grounded_graphics_allowed(cfg2) is True
