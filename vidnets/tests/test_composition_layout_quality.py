"""Structural composition checks complement actual rendered-frame review."""
from copy import deepcopy

from server.services.editorial_director.director import _diagram_recipe_quality_error, validated_diagram_links


def recipe():
    return {'layers': [
        {'kind': 'diagram', 'x': 0, 'y': 0, 'width': 100, 'height': 100,
         'data': [{'label': 'Moon', 'x': 26, 'label_y': 14},
                  {'label': 'sunlight', 'x': 69, 'label_y': 14}]},
        {'kind': 'sphere', 'x': 8, 'y': 20, 'width': 36, 'height': 48, 'start_frame': 0},
        {'kind': 'cone', 'x': 48, 'y': 22, 'width': 38, 'height': 42, 'start_frame': 30},
    ]}


def test_meaningful_labeled_mechanism_preserved():
    assert not _diagram_recipe_quality_error(recipe(), 4, 30)


def test_distinct_actor_geometry_cannot_obscure_each_other():
    row=recipe()
    row['layers'][1].update(semantic_actor='Moon')
    row['layers'][2].update(semantic_actor='sunlight',x=20)
    assert _diagram_recipe_quality_error(row,4,30)=='composition_distinct_actors_overlap'


def test_intentional_actor_contact_requires_a_grounded_semantic_link():
    row=recipe()
    row['layers'][1].update(semantic_actor='Moon')
    row['layers'][2].update(semantic_actor='sunlight',x=20)
    row['layers'][0]['links']=[{'from':'Moon','to':'sunlight','evidence':'The Moon blocks sunlight'}]
    assert not _diagram_recipe_quality_error(row,4,30)


def test_director_keeps_only_exact_narrated_links_between_approved_actors():
    nodes=[{'label':'Moon'},{'label':'sunlight'},{'label':'Earth'}]
    links=validated_diagram_links([
        {'from':'Moon','to':'sunlight','evidence':'The Moon blocks sunlight'},
        {'from':'Moon','to':'Earth','evidence':'The Moon leads to an earthquake'},
        {'from':'unknown','to':'Earth','evidence':'unknown reaches Earth'},
    ],nodes,'The Moon blocks sunlight, and its umbra sweeps across Earth.')
    assert links==[{'from':'Moon','to':'sunlight','evidence':'The Moon blocks sunlight'}]


def test_background_and_stacked_duplicates_do_not_inflate_coverage():
    row = recipe()
    tiny = {'kind': 'rect', 'x': 40, 'y': 30, 'width': 15, 'height': 15}
    row['layers'] = [row['layers'][0], {'kind': 'rect', 'x': 0, 'y': 0, 'width': 100, 'height': 100}] + [deepcopy(tiny) for _ in range(8)]
    assert 'too sparse' in _diagram_recipe_quality_error(row, 4, 30)


def test_late_reveal_has_no_readable_hold():
    row = recipe()
    row['layers'][2]['start_frame'] = 115
    assert _diagram_recipe_quality_error(row, 4, 30) == 'composition_has_no_readable_hold'


def test_caption_area_is_protected():
    row = recipe()
    row['layers'][2]['y'] = 45
    assert _diagram_recipe_quality_error(row, 4, 30) == 'composition_intrudes_caption_safe_area'


def test_labels_do_not_overlap():
    row = recipe()
    row['layers'][0]['data'][1]['x'] = 27
    assert _diagram_recipe_quality_error(row, 4, 30) == 'composition_labels_collide'


def test_labels_cannot_be_clipped_off_canvas():
    row = recipe()
    row['layers'][0]['data'][1]['x'] = 99
    assert _diagram_recipe_quality_error(row, 4, 30) == 'composition_label_outside_safe_area'


def test_people_and_anatomy_are_not_anonymous_spheres():
    row = recipe()
    row['layers'][0]['data'][0].update({'label': 'goalkeeper', 'x': 26, 'y': 44})
    assert _diagram_recipe_quality_error(row, 4, 30) == 'composition_uses_generic_circle_for_subject'


def test_planet_can_use_physically_meaningful_sphere():
    row = recipe()
    row['layers'][0]['data'][0].update({'label': 'Earth', 'x': 26, 'y': 44})
    assert not _diagram_recipe_quality_error(row, 4, 30)


def test_parent_scale_is_included_in_visible_coverage():
    row = recipe()
    row['layers'].append({'kind': 'group', 'id': 'tiny', 'x': 0, 'y': 0, 'width': 10, 'height': 10})
    for layer in row['layers'][1:3]:
        layer['parent_id'] = 'tiny'
    assert 'too sparse' in _diagram_recipe_quality_error(row, 4, 30)


def test_subject_specific_contour_can_explain_mechanism():
    row = recipe()
    row['layers'][1:] = [{'kind': 'path', 'x': 10, 'y': 20, 'width': 75, 'height': 45,
        'points': [{'x': 0, 'y': 50}, {'x': 45, 'y': 0}, {'x': 100, 'y': 50}, {'x': 45, 'y': 100}, {'x': 0, 'y': 50}]}]
    assert not _diagram_recipe_quality_error(row, 4, 30)


def test_renderer_montage_uses_full_rows_and_handles_null_shots():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'src/remotion/components/DeclarativeRecipe.jsx').read_text(encoding='utf-8')
    assert "unique.length === 2 ? 'minmax(0, 1fr)'" in source
    assert "unique.length === 3 && index === 0" in source
    assert 'shot?.src' in source


def test_satellite_labels_adapt_to_edges_and_arrow_holds_are_continuous():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    satellite = (root / 'src/remotion/components/SatelliteMap.jsx').read_text(encoding='utf-8')
    assert 'fitsLeft' in satellite and 'fitsRight' in satellite
    assert 'width - margin - labelWidth' in satellite
    source = (root / 'src/remotion/components/DeclarativeRecipe.jsx').read_text(encoding='utf-8')
    arrow = source.split("else if (kind === 'arrow')", 1)[1].split("else if (kind === 'circle')", 1)[0]
    assert 'clipPath' in arrow
    assert 'strokeDasharray' not in arrow


def test_sparse_composition_gets_one_bounded_quality_correction():
    import json
    from server.services.editorial_director.director import EditingDirector

    class Client:
        repairs = 0

        def chat(self, **kwargs):
            stage = kwargs.get('json_schema_name')
            if stage == 'vidnets_art_direction_v1':
                return json.dumps({'genre': 'science-documentary', 'display_font': 'Archivo Black', 'body_font': 'DM Sans', 'accent_color': '#E8B75C'})
            if stage == 'vidnets_scene_direction_v1':
                tiny = {'version': 2, 'id': 'tiny-actors', 'name': 'Tiny actors', 'layers': [
                    {'kind': 'diagram', 'x': 0, 'y': 0, 'width': 100, 'height': 100, 'data': [{'label': 'Moon'}, {'label': 'sunlight'}]},
                    {'kind': 'circle', 'x': 25, 'y': 20, 'width': 5, 'height': 5},
                ]}
                return json.dumps({'scenes': [{'scene_index': 0, 'type': 'Diagram', 'variant': 'step_by_step_process',
                    'frame_mode': 'diagram', 'overlay_policy': 'none', 'display_text': '', 'template_id': 'generated-recipe',
                    'template_recipe': tiny, 'diagram_nodes': [{'label': 'Moon', 'source_span': 'The Moon blocks sunlight'},
                        {'label': 'sunlight', 'source_span': 'The Moon blocks sunlight'}]}]})
            if stage == 'vidnets_scene_composition_v1':
                # First pass is structurally valid but visually too small;
                # the director must ask for one targeted composition repair.
                objects = [{'kind': 'circle', 'x': x, 'y': 20, 'width': 5, 'height': 5,
                    'actor_label': actor, 'color_role': 'accent', 'motion': 'fade', 'start_seconds': .5}
                    for x, actor in [(12, 'Moon'), (32, 'sunlight'), (52, 'Moon')]]
                return json.dumps({'id': 'sparse-eclipse', 'name': 'Tiny eclipse', 'objects': objects})
            if stage == 'vidnets_scene_composition_repair_v1':
                self.repairs += 1
                assert 'too sparse' in kwargs['user_prompt']
                assert kwargs['total_timeout_seconds'] <= 40
                objects = [{'kind': kind, 'x': x, 'y': y, 'width': w, 'height': h,
                    'actor_label': actor, 'color_role': 'accent', 'motion': 'fade', 'start_seconds': .5}
                    for kind, x, y, w, h, actor in [('sphere', 12, 20, 36, 48, 'Moon'),
                        ('cone', 48, 26, 38, 28, 'sunlight'), ('arrow', 48, 48, 35, 6, 'Moon')]]
                return json.dumps({'id': 'meaningful-eclipse', 'name': 'Eclipse mechanism', 'objects': objects})
            return '{}'

    client = Client()
    plan = EditingDirector(llm_client=client).plan_documentary(
        script_text='The Moon blocks sunlight.', scene_texts=['The Moon blocks sunlight.'],
        scene_durations=[4], topic='Eclipse', category='science', fps=30)
    assert client.repairs == 1
    assert plan.director_metadata['composition_passes'][0]['status'] == 'accepted_after_correction'
    assert plan.scenes[0].template_recipe.get('id') == 'meaningful-eclipse', (plan.director_metadata.get('composition_passes'), plan.scenes[0].template_recipe, plan.scenes[0].template_id)
    assert plan.director_metadata['creative_coverage'] == 'complete'
    assert plan.director_metadata['composition_coverage'] == 'complete'
    assert plan.director_metadata['composition_passes'][0]['attempt_count'] == 2
    assert plan.director_metadata['composition_passes'][0]['detail'] == ''


def test_missing_approved_actors_cannot_start_an_uninitialized_correction():
    import json
    from server.services.editorial_director.director import EditingDirector
    class Client:
        repairs = 0
        def chat(self, **kwargs):
            stage = kwargs.get('json_schema_name')
            if stage == 'vidnets_art_direction_v1':
                return json.dumps({'genre':'technology-documentary','display_font':'Archivo Black',
                    'body_font':'DM Sans','accent_color':'#D64040'})
            if stage == 'vidnets_scene_direction_v1':
                return json.dumps({'scenes':[{'scene_index':0,'type':'Diagram',
                    'variant':'step_by_step_process','frame_mode':'diagram','overlay_policy':'none',
                    'display_text':'','template_id':'generated-recipe','template_recipe':None,
                    'diagram_nodes':[], 'diagram_links':[]}]})
            if stage == 'vidnets_scene_composition_repair_v1':
                self.repairs += 1
            return '{}'
    client=Client()
    plan=EditingDirector(llm_client=client).plan_documentary(
        script_text='Software changes what owners experience after purchase.',
        scene_texts=['Software changes what owners experience after purchase.'],
        scene_durations=[5],topic='Technology',fps=30)
    assert client.repairs == 0
    assert 'UnboundLocalError' not in json.dumps(plan.director_metadata)


def test_failed_geometry_keeps_valid_llm_selected_named_highlight():
    import json
    from server.services.editorial_director.director import EditingDirector
    passage = 'At home, Powerwall batteries store electricity.'
    class Client:
        def chat(self, **kwargs):
            stage = kwargs.get('json_schema_name')
            if stage == 'vidnets_art_direction_v1':
                return json.dumps({'genre': 'technology-documentary', 'display_font': 'Archivo Black',
                    'body_font': 'DM Sans', 'accent_color': '#D64040'})
            if stage == 'vidnets_scene_direction_v1':
                return json.dumps({'scenes': [{'scene_index': 0, 'type': 'Diagram',
                    'variant': 'step_by_step_process', 'frame_mode': 'diagram',
                    'overlay_policy': 'single_short_highlight', 'display_text': 'Powerwall',
                    'source_span': passage, 'emphasis_spans': [], 'placement': 'top_left',
                    'template_id': 'generated-recipe', 'template_recipe': None,
                    'diagram_nodes': [], 'diagram_links': []}]})
            return '{}'
    plan = EditingDirector(llm_client=Client()).plan_documentary(script_text=passage,
        scene_texts=[passage], scene_durations=[4], topic='Tesla', fps=30,
        media_items=[{'src': 'verified-powerwall.jpg', 'type': 'image'}])
    assert plan.scenes[0].display_text == 'Powerwall'
    assert plan.scenes[0].primary_visual.type == 'KineticText'
    assert plan.scenes[0].visual_role == 'media_overlay'
    assert plan.director_metadata['preserved_composition_copy_scene_indices'] == [0]
