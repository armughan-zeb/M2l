from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.services.editing_profile import NICHE_VISUAL_GRAMMAR, classify_niche
from server.services.editorial_director.director import (
    _DIRECTOR_RESPONSE_SCHEMA,
    _director_runtime_capabilities,
    _recipe_is_grounded,
    _recipe_supports_visual_type,
)
from server.services.template_library import validate_template_recipe


def layer(kind: str, **values):
    return {
        "kind": kind,
        "x": 8,
        "y": 10,
        "width": 84,
        "height": 80,
        "text": "",
        "color": "#e7b84e",
        "asset_id": "",
        "motion": "rise",
        "start_frame": 0,
        "duration_frames": 72,
        "data": [],
        "shape": "rounded",
        "orientation": "horizontal",
        "focal_x": 50,
        "focal_y": 50,
        "links": [],
        **values,
    }


def recipe(*layers):
    return {"version": 2, "id": "niche-visual", "name": "Niche visual", "layers": list(layers)}


def test_filled_subject_contours_preserve_safe_paint_and_actor_binding():
    raw=recipe(layer('path',points=[{'x':0,'y':0},{'x':100,'y':0},{'x':50,'y':100}],
                     closed=True,fill_color='#4F8B78',semantic_actor='Compressor'))
    value=validate_template_recipe(raw)['layers'][0]
    assert value['closed'] is True
    assert value['fill_color']=='#4F8B78'
    assert value['semantic_actor']=='Compressor'
    raw['layers'][0]['fill_color']='url(https://untrusted.invalid/image.svg)'
    with pytest.raises(ValueError,match='hex color'):
        validate_template_recipe(raw)


def test_recipe_v2_composes_semantic_layers_and_keeps_legacy_recipe_compatibility():
    raw = recipe(
        layer("diagram", data=[{"label": "Ocean heat"}, {"label": "Currents"}], links=[{"from": "Ocean heat", "to": "Currents", "evidence": "Ocean heat shifts currents"}]),
        layer("timeline", data=[{"label": "Sailed", "value": 1912}, {"label": "Wreck was found", "value": 1985}], orientation="vertical"),
        layer("comparison", data=[{"label": "First trial", "value": 12}, {"label": "Second trial", "value": 19}], shape="arch"),
        layer("media_montage", motion="wipe", focal_x=61, focal_y=35),
        layer("image", shape="hexagon", focal_x=60, focal_y=24),
        layer("counter", text=" years", data=[{"label": "Century", "value": 100}]),
    )
    normalized = validate_template_recipe(raw)

    assert normalized["version"] == 2
    assert [item["kind"] for item in normalized["layers"]] == [
        "diagram", "timeline", "comparison", "media_montage", "image", "counter"
    ]
    assert normalized["layers"][0]["links"] == [{"from": "Ocean heat", "to": "Currents", "evidence": "Ocean heat shifts currents"}]
    assert normalized["layers"][4]["shape"] == "hexagon"
    old = validate_template_recipe({
        "id": "old-recipe", "name": "Old recipe", "version": 1,
        "layers": [{"kind": "text", "text": "Safe"}],
    })
    assert old["version"] == 1


def test_recipe_v2_rejects_broken_visual_contracts_and_untrusted_assets():
    with pytest.raises(ValueError, match="exactly two"):
        validate_template_recipe(recipe(layer("comparison", data=[{"label": "One", "value": 1}])))
    with pytest.raises(ValueError, match="numeric values"):
        validate_template_recipe(recipe(layer("chart", data=[{"label": "One"}, {"label": "Two"}])))
    with pytest.raises(ValueError, match="declared diagram nodes"):
        validate_template_recipe(recipe(layer("diagram", data=[{"label": "One"}, {"label": "Two"}], links=[{"from": "One", "to": "Invented", "evidence": "One creates an invented node"}])))
    with pytest.raises(ValueError, match="local catalog id"):
        validate_template_recipe(recipe(layer("image", asset_id="https://example.invalid/image.png")))
    with pytest.raises(ValueError, match="focal_x"):
        validate_template_recipe(recipe(layer("image", focal_x=101)))


def test_director_recipe_schema_exposes_the_composable_remotion_runtime():
    caps = _director_runtime_capabilities()["generated_recipe"]
    assert caps["version"] == 2
    assert set(caps["layer_kinds"]) >= {
        "diagram", "timeline", "comparison", "counter", "media_montage", "map"
    }
    layer_schema = _DIRECTOR_RESPONSE_SCHEMA["properties"]["scenes"]["items"]["properties"]["template_recipe"]["properties"]["layers"]["items"]
    assert set(layer_schema["properties"]["kind"]["enum"]) == set(caps["layer_kinds"])
    assert layer_schema["properties"]["shape"]["enum"] == ["rect", "rounded", "arch", "circle", "hexagon"]
    assert layer_schema["properties"]["links"]["maxItems"] == 8


@pytest.mark.parametrize('kind', ['sphere', 'cone', 'arrow'])
def test_illustrated_mechanism_geometry_is_validated_and_grounded(kind):
    result = validate_template_recipe(recipe(layer(kind)))
    assert result['layers'][0]['kind'] == kind
    assert _recipe_is_grounded(result, 'The Moon blocks sunlight.')


def test_narrated_physical_relationship_survives_recipe_grounding():
    data = recipe(layer('diagram', data=[{'label': 'Moon'}, {'label': 'sunlight'}], links=[{
        'from': 'Moon', 'to': 'sunlight', 'evidence': 'The Moon blocks sunlight',
    }]))
    assert _recipe_is_grounded(validate_template_recipe(data), 'The Moon blocks sunlight.')


def test_narrated_identity_relationship_survives_recipe_grounding():
    data = recipe(layer('diagram', data=[{'label': 'Butler'}, {'label': 'undrafted rookie'}], links=[{
        'from': 'Butler', 'to': 'undrafted rookie', 'evidence': 'Butler was an undrafted rookie',
    }]))
    assert _recipe_is_grounded(validate_template_recipe(data), 'Butler was an undrafted rookie.')


def test_generated_recipe_facts_and_diagram_links_must_come_from_scene_narration():
    text = "Ocean heat shifts currents, and currents carry warmth toward the coast."
    grounded = recipe(layer(
        "diagram",
        data=[{"label": "Ocean heat"}, {"label": "Currents"}, {"label": "Warmth toward the coast"}],
        links=[
            {"from": "Ocean heat", "to": "Currents", "evidence": "Ocean heat shifts currents"},
            {"from": "Currents", "to": "Warmth toward the coast", "evidence": "currents carry warmth toward the coast"},
        ],
    ))
    assert _recipe_is_grounded(grounded, text)
    fabricated_edge = recipe(layer(
        "diagram", data=[{"label": "Ocean heat"}, {"label": "Currents"}],
        links=[{"from": "Ocean heat", "to": "Currents", "evidence": "Ocean heat exists and currents exist"}],
    ))
    assert not _recipe_is_grounded(fabricated_edge, "Ocean heat exists. Currents exist.")
    fabricated_number = recipe(layer("counter", text=" billion", data=[{"label": "Revenue", "value": 90}]))
    assert not _recipe_is_grounded(fabricated_number, "Revenue rose to 9 billion.")


@pytest.mark.parametrize("niche", sorted(NICHE_VISUAL_GRAMMAR))
def test_all_advertised_niches_have_editorial_guidance_and_classify(niche):
    assert NICHE_VISUAL_GRAMMAR[niche]["editorial_logic"]
    if niche != "general":
        assert classify_niche(category=niche) == niche


def test_generated_recipes_own_the_matching_specialist_graphic():
    examples = {
        "Chart": [layer("chart", data=[{"label": "A", "value": 1}, {"label": "B", "value": 2}])],
        "Timeline": [layer("timeline", data=[{"label": "A", "value": 1912}, {"label": "B", "value": 1985}])],
        "Diagram": [layer("diagram", data=[{"label": "A"}, {"label": "B"}])],
        "Comparison": [layer("comparison", data=[{"label": "A", "value": 1}, {"label": "B", "value": 2}])],
        "Collage": [layer("media_montage")],
    }
    for visual_type, layers in examples.items():
        assert _recipe_supports_visual_type(recipe(*layers), visual_type)


def test_fraction_canvas_is_normalized_once_without_changing_thin_percent_accents():
    raw = recipe(layer("chart", x=.12, y=.2, width=.7, height=.58,
                       data=[{"label":"first","value":12},{"label":"second","value":19}]),
                 layer("line", x=.12, y=.8, width=.7, height=.01))
    normalized = validate_template_recipe(raw)
    assert normalized["layers"][0]["width"] == 70
    assert normalized["layers"][0]["height"] == 58
    assert normalized["layers"][1]["height"] == 1
    assert validate_template_recipe(normalized) == normalized
    percent = validate_template_recipe(recipe(layer("line", x=12,y=80,width=70,height=.4)))
    assert percent["layers"][0]["height"] == .4


def test_recipe_rejects_invisible_semantic_layers_and_off_canvas_geometry():
    with pytest.raises(ValueError, match="too small"):
        validate_template_recipe(recipe(layer("chart", width=2,height=2,
            data=[{"label":"first","value":12},{"label":"second","value":19}])))
    with pytest.raises(ValueError, match="outside"):
        validate_template_recipe(recipe(layer("text",x=90,width=30,text="A spoken phrase")))
