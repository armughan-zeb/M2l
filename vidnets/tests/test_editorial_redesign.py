import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from server.services.semantic_edit_engine import (
    _editorial_text_contract,
    build_semantic_edit_manifest,
)
from server.services.editing_profile import deterministic_profile
from server.services.text_position_contract import ALLOWED_TEXT_POSITIONS, guide_anchor
from server.services.native_engine.libraries.professional_templates import (
    PROFESSIONAL_TEMPLATES,
    SANS,
    _contract_metrics,
    _contract_layout,
    _size,
)
from server.services.native_engine.libraries.vox_templates import (
    _fit_fontsize,
    _font_metrics,
    bold_name_banner as LEGACY_BOLD_NAME_BANNER,
)
from server.services.semantic_ffmpeg_renderer import (
    _PALETTE,
    _overlay_editorial_contract,
)
from services.web_image.query import (
    _derive_effective_web_title,
    _generate_queries_mechanical,
    _repair_query_pack,
)
from services.web_image.flickr_api_source import _flickr_metadata_relevant, _tags_bad
from services.web_image.safety import candidate_is_safe


def test_history_query_anchor_stays_on_named_vessel():
    scenes = [
        "On April 10, 1912, the RMS Titanic departed Southampton.",
        "The ship struck an iceberg in the North Atlantic.",
        "Carpathia rescued survivors from the lifeboats.",
    ]
    title, _ = _derive_effective_web_title("The Sinking of RMS Titanic", " ".join(scenes))
    assert title == "RMS Titanic"

    rows = _generate_queries_mechanical(" ".join(scenes), "The Sinking of RMS Titanic", scenes)
    assert all("rms titanic" in " ".join(row["query_variants"]).lower() for row in rows)
    joined = " ".join(" ".join(row["query_variants"]) for row in rows).lower()
    assert not re.search(r"\b(fielding|speaking|hailed|portrait)\b", joined)
    assert "iceberg collision" in joined
    assert "lifeboat rescue" in joined


def test_query_repair_rejects_llm_prose_and_metadata():
    repaired = _repair_query_pack(
        [
            "The scene depicts RMS Titanic during the important historical moment",
            "RMS Titanic iceberg collision",
            "analysis of the subject and recommended search query",
        ],
        "RMS Titanic",
        "The ship struck an iceberg.",
        limit=5,
    )
    assert repaired
    assert all("depict" not in query.lower() for query in repaired)
    assert all("analysis" not in query.lower() for query in repaired)
    assert "RMS Titanic iceberg collision" in repaired


def test_editorial_contract_limits_layout_and_removes_date_kicker():
    profile = deterministic_profile("RMS Titanic history", variation_seed=17)
    contract = _editorial_text_contract(
        "date_numeral",
        {"date": "April 10, 1912", "label": ""},
        {"treatment": "date", "role": "evidence", "importance": 0.8},
        "On April 10, 1912, the ship departed.",
        "RMS Titanic history",
        0,
        17,
        profile,
    )
    assert contract["_position"] in set(ALLOWED_TEXT_POSITIONS)
    assert contract["_card_mode"] in {"none", "surface", "accent", "danger"}
    assert contract["_micro_label"] == ""
    assert contract["_show_rule"] is True


def test_contract_template_has_one_heading_and_short_rule():
    props = {
        "_editorial_contract": True,
        "_contract_text": "APRIL 10",
        "_contract_kind": "date",
        "_contract_size": 82,
        "_position": "top_left",
        "_card_mode": "surface",
        "_micro_label": "",
        "_show_rule": True,
    }
    chains = PROFESSIONAL_TEMPLATES["date_numeral"](
        {"primary": "#ffffff", "accent": "#e2aa4d", "danger": "#d94a43", "surface": "#11151d"},
        props,
        scene_duration=4.0,
    )["chains"]
    text_layers = [chain for chain in chains if chain.startswith("drawtext=")]
    assert len(text_layers) == 1
    assert all("A TURNING POINT" not in chain for chain in chains)
    rule_layers = [chain for chain in chains if chain.startswith("drawbox=") and ":h=3:" in chain]
    assert len(rule_layers) == 1
    measured_w, _ = _contract_metrics("APRIL 10", SANS, _size("APRIL 10", 82))
    assert f"({measured_w + 18})" in rule_layers[0]


def test_contract_card_width_uses_rendered_font_metrics():
    text = "X"
    size = _size(text, 68)
    measured_w, measured_h = _contract_metrics(text, SANS, size)
    horizontal_pad = max(24, int(size * 0.36))
    vertical_pad = max(12, int(size * 0.16))
    chains = PROFESSIONAL_TEMPLATES["bold_red_text"](
        {"primary": "#ffffff", "accent": "#e2aa4d", "danger": "#d94a43", "surface": "#11151d"},
        {
            "_editorial_contract": True,
            "_contract_text": text,
            "_contract_size": 68,
            "_position": "center",
            "_card_mode": "surface",
            "_show_rule": True,
        },
        scene_duration=4.0,
    )["chains"]
    card = next(chain for chain in chains if chain.startswith("drawbox=") and ":h=" in chain and ":h=3:" not in chain)
    assert f":w={measured_w + horizontal_pad * 2}:" in card
    assert f":h={min(210, max(56, measured_h + vertical_pad * 2))}:" in card
    assert ":w=210:" not in card
    # Card geometry is a drawbox expression, so drawtext-only dimensions must
    # be substituted before FFmpeg evaluates the filter. Leaving ``text_w`` or
    # ``text_h`` here makes both QSV and software encoders fail at runtime.
    assert "text_w" not in card
    assert "text_h" not in card


def test_legacy_name_banner_width_tracks_name_metrics():
    name = "X"
    font = "PlayfairDisplay-Bold.ttf"
    size = int(_fit_fontsize(name, "iw*0.5", 64, font))
    measured_w, measured_h = _font_metrics(name, font, size)
    horizontal_pad = max(24, int(size * 0.36))
    vertical_pad = max(12, int(size * 0.18))
    chains = LEGACY_BOLD_NAME_BANNER(
        {"primary": "#ffffff", "accent": "#e2aa4d", "danger": "#d94a43"},
        {"name": name, "_font": font},
    )["chains"]
    banner = next(chain for chain in chains if chain.startswith("drawbox=") and ":w=" in chain and ":w=iw:" not in chain)
    assert f":w={min(1020, measured_w + horizontal_pad * 2)}:" in banner
    assert f":h={min(190, measured_h + vertical_pad * 2)}:" in banner
    assert "iw*0.55" not in banner


def test_pillow_contract_card_uses_symmetric_glyph_padding():
    text = "X"
    size = 72
    image = _overlay_editorial_contract({
        "_contract_text": text,
        "_contract_size": size,
        "_position": "top_left",
        "_card_mode": "surface",
    })
    assert image.mode == "RGBA"
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    assert bbox is not None
    top_left = guide_anchor("top_left")
    assert abs(bbox[0] - int(1920 * top_left["x"])) <= 2
    assert bbox[1] < int(1080 * top_left["y"])


def test_remotion_contract_uses_intrinsic_text_wrapper():
    source = (Path(__file__).resolve().parents[1] / "src" / "remotion" / "templates.jsx").read_text()
    assert "display: 'inline-block'" in source
    assert "width: 'max-content'" in source
    assert "value.length * 42" not in source
    assert "Math.max(210" not in source


def test_manifest_contract_uses_only_the_five_green_guide_slots():
    manifest = build_semantic_edit_manifest(
        [
            "On April 10, 1912, the RMS Titanic departed.",
            "The iceberg collision changed everything.",
            "The wreck was discovered in 1985.",
        ],
        [3, 3, 3],
        topic="The Sinking of RMS Titanic",
        variation_seed=44,
    )
    contracts = [
        scene["graphic"]["props"]
        for scene in manifest["scenes"]
        if scene["graphic"].get("vox_template")
    ]
    assert contracts
    assert all(props.get("_position") in set(ALLOWED_TEXT_POSITIONS) for props in contracts)
    assert all(props.get("_show_rule") is True for props in contracts)


def test_guide_coordinates_are_copied_without_forbidden_side_slots():
    assert guide_anchor("top_left")["rect"] == {"left": 10, "top": 10, "right": 120, "bottom": 40}
    assert guide_anchor("top_right")["rect"] == {"left": 347, "top": 10, "right": 457, "bottom": 40}
    assert guide_anchor("center")["rect"] == {"left": 178, "top": 103, "right": 300, "bottom": 145}
    assert guide_anchor("bottom_left")["rect"] == {"left": 10, "top": 225, "right": 120, "bottom": 255}
    assert guide_anchor("bottom_right")["rect"] == {"left": 347, "top": 230, "right": 457, "bottom": 260}
    assert guide_anchor("mid_left")["position"] == "center"


def test_native_contract_emits_all_five_guide_anchors():
    for position in ALLOWED_TEXT_POSITIONS:
        anchor = guide_anchor(position)
        layout = _contract_layout(position)
        assert layout["x"] == f"iw*{anchor['x']:.6f}"
        assert layout["y"] == f"ih*{anchor['y']:.6f}"
        assert layout["horizontal"] == anchor["horizontal"]


def test_flickr_metadata_gate_blocks_adult_and_irrelevant_results():
    assert _tags_bad("portrait, NSFW, nude") is True
    assert _flickr_metadata_relevant(
        "RMS Titanic iceberg collision",
        "random portrait",
        "studio model",
    ) is False
    safe, reason = candidate_is_safe({
        "title": "private erotic set",
        "domain": "flickr.com",
        "description": "adult explicit content",
    })
    assert safe is False
    assert reason == "adult_metadata"
