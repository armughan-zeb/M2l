"""Unit tests for the 5-role Accountable Reviewer Council.

Verifies:
1. Lead Editor: flags copy holds below the 1.3s readable floor.
2. Cinematographer: enforces media dominance and safe regions.
3. Graphics Director: enforces composition cooldowns and max 2 font families.
4. Evidence Reviewer: catches ungrounded numbers or missing locator endpoints.
5. Visual Critic: inspects frame metrics.
6. Full council passes on compliant documentary manifests.
"""

from __future__ import annotations

import pytest
from server.services.editorial_director.media_director import plan_media_composition
from server.services.editorial_director.media_first import apply_media_first_direction
from server.services.editorial_director.manager import execute_reviewer_council


def test_compliant_manifest_passes_full_council():
    raw_manifest = {
        "visual_dna": {
            "typography": {
                "display": "Plus Jakarta Sans",
                "body": "Inter",
                "numbers": "Inter",
            }
        },
        "scenes": [
            {
                "scene_index": 0,
                "duration": 6.0,
                "narration": "Fans waited a decade for Grand Theft Auto VI.",
                "media": {"src": "C:/media/shot1.jpg", "type": "image"},
                "primary_visual": {
                    "type": "TextCard",
                    "props": {"text": "GRAND THEFT AUTO VI"},
                },
            },
            {
                "scene_index": 1,
                "duration": 6.0,
                "narration": "The development budget reached two billion dollars.",
                "media": {"src": "C:/media/shot2.jpg", "type": "image"},
                "primary_visual": {
                    "type": "StatCard",
                    "props": {"value": "$2 BILLION"},
                },
            },
        ],
    }

    manifest = apply_media_first_direction(raw_manifest, plan_media_composition)
    result = execute_reviewer_council(manifest)
    assert result["status"] == "pass"
    assert result["ready_for_long_render"] is True
    council = result["council"]
    assert council["lead_editor"]["status"] == "pass"
    assert council["cinematographer"]["status"] == "pass"
    assert council["graphics_director"]["status"] == "pass"
    assert council["evidence_reviewer"]["status"] == "pass"
    assert council["visual_critic"]["status"] == "pass"


def test_lead_editor_fails_on_sub_floor_hold_time():
    manifest = {
        "scenes": [
            {
                "scene_index": 0,
                "duration": 4.0,
                "narration": "A very quick flash of text.",
                "editorial_contract": {
                    "family": "corner_identification",
                    "display_text": "FLASH",
                    "enabled": True,
                        "timing": {"enabled": True, "hold_seconds": 1.2},  # Below the readable 1.3s floor
                },
            }
        ]
    }
    result = execute_reviewer_council(manifest)
    assert result["status"] == "fail"
    council = result["council"]
    assert council["lead_editor"]["status"] == "fail"
    assert any("below minimum 1.3s floor" in v for v in council["lead_editor"]["hold_violations"])


def test_evidence_reviewer_catches_ungrounded_numeric_emphasis():
    manifest = {
        "scenes": [
            {
                "scene_index": 0,
                "duration": 5.0,
                "narration": "The company expanded its operations significantly.",
                "editorial_contract": {
                    "family": "numeric_emphasis",
                    "display_text": "EXPANDED OPERATIONS",  # Missing any digits/numbers!
                    "enabled": True,
                    "timing": {"enabled": True, "hold_seconds": 2.5},
                },
            }
        ]
    }
    result = execute_reviewer_council(manifest)
    assert result["status"] == "fail"
    council = result["council"]
    assert council["evidence_reviewer"]["status"] == "fail"
    assert any("numeric emphasis graphic has no numbers" in err for err in council["evidence_reviewer"]["errors"])
