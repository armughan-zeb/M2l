import copy
import time
import pytest

from services.editorial_director.pixel_revision import (
    _normalize_review_patch, _repair_component_options, _suppress_grounded_transition_false_positives,
    apply_visual_patch, review_and_revise,
)


def scene():
    return {'narration':'An octopus explores a reef.','start':0,'from':0,'duration':4,'duration_in_frames':120,
            'primary_visual':{'type':'SmartImage','variant':'full_bleed_cinematic','asset':{'src':'verified.jpg'}},
            'text':{'display_text':'explores a reef','source':'llm','source_span':'explores a reef','at':1,'hold':1.5}}


def test_visual_patch_preserves_copy_media_and_timing():
    original = scene()
    revised = apply_visual_patch(original,{'variant':'editorial_crop'})
    assert revised['primary_visual']['variant']=='editorial_crop'
    assert revised['text']==original['text']
    assert revised['primary_visual']['asset']==original['primary_visual']['asset']
    assert original['primary_visual']['variant']=='full_bleed_cinematic'


def test_smart_image_review_variant_catalog_matches_renderer_and_editor():
    from services.editorial_director.contract import SCENE_TYPE_VARIANTS
    supported = set(SCENE_TYPE_VARIANTS['SmartImage'])
    assert {'full_bleed_cinematic', 'editorial_crop', 'framed_print',
            'archival_polaroid', 'spotlight_reveal', 'parallax_float'} <= supported
    result = apply_visual_patch(scene(), {'variant': 'editorial_crop'})
    assert result['primary_visual']['variant'] == 'editorial_crop'
    assert result['primary_visual']['props']['variant'] == 'editorial_crop'


def test_review_alias_normalizes_emphasis_highlight_without_rejecting_safe_patch():
    patch = _normalize_review_patch(scene(), {
        'visual_role': 'emphasis_highlight',
        'variant': 'editorial_crop',
    })
    assert patch['visual_role'] == 'media_overlay'
    assert patch['variant'] == 'editorial_crop'


@pytest.mark.parametrize('issue', [
    'Visual disconnect: narration says 1953 but footage shows a modern interview.',
    'The image depicts the wrong person at a different coronation.',
    'Wrong era: this is present-day footage.',
])
def test_factual_pixel_conflicts_require_media_repair(issue):
    from services.editorial_director.pixel_revision import _requires_media_repair
    assert _requires_media_repair([issue])


def test_missing_remotion_graphic_does_not_launch_redundant_media_search():
    from services.editorial_director.pixel_revision import _requires_media_repair
    assert not _requires_media_repair([
        'Missing explanatory graphic: current output fails to replace generic footage with a grounded Remotion diagram.'
    ])
    assert _requires_media_repair([
        'Generic aerial footage is contradicting the narration about a bright eclipse.'
    ])


@pytest.mark.parametrize('patch',[{'narration':'invented'}, {'variant':'unknown'}, {'recipe_geometry':[{'layer_index':-1}]},
                                  {'text_treatment':{},'new_number':15}])
def test_visual_patch_rejects_unsupported_changes(patch):
    with pytest.raises(ValueError):
        apply_visual_patch(scene(),patch)


def test_cannot_invent_text():
    value=scene(); value['text']={}
    with pytest.raises(ValueError,match='unapproved'):
        apply_visual_patch(value,{'text_treatment':{'panel_style':'hero_type'}})


def test_parallel_review_preserves_success_and_actual_frame_context():
    first=scene(); second=scene(); second.update({'start':4,'from':120})
    original=copy.deepcopy(second)
    film={'fps':30,'scenes':[first,second]}
    seen=[]
    class Client:
        def review_editorial_frames(self,paths,context):
            seen.append((paths,context))
            if paths==['second.png']:
                return {'status':'needs_revision','confidence':95,'issues':['wrong subject'],'patch':None}
            return {'status':'needs_revision','confidence':90,'issues':['framing'], 'patch':{'variant':'editorial_crop'}}
    report=review_and_revise(film,[{'frame':25,'path':'first.png'},{'frame':145,'path':'second.png'}],lambda budget:Client())
    assert report['changed_scene_indices']==[0]
    assert report['unresolved_scene_indices']==[1]
    assert film['scenes'][1]==original
    assert all('story_arc' in context for _,context in seen)


def test_slow_review_obeys_deadline_and_does_not_mutate_after_return():
    film={'fps':30,'scenes':[scene()]}
    original=copy.deepcopy(film)
    class Slow:
        def review_editorial_frames(self,paths,context):
            time.sleep(.15)
            return {'status':'needs_revision','confidence':90,'patch':{'variant':'editorial_crop'}}
    started=time.monotonic()
    report=review_and_revise(film,[{'frame':25,'path':'first.png'}],lambda budget:Slow(),budget_seconds=.02)
    assert time.monotonic()-started<.12
    assert report['status']=='incomplete'
    time.sleep(.2)
    assert film==original


def test_missing_frames_cannot_certify_scene():
    report=review_and_revise({'scenes':[scene()]},[],lambda budget:None)
    assert report['status']=='incomplete'
    assert report['unavailable_scene_indices']==[0]


def test_counter_intermediate_is_not_mistaken_for_wrong_final_fact():
    value = {
        'narration':'The wind chill reached minus 48 degrees.',
        'primary_visual':{'type':'StatCard','props':{'value':48,'prefix':'−','suffix':'°'}},
    }
    issue = 'Numeric value in Sample 1 (-20 degrees) contradicts narration and final hold frame (Sample 2 shows -48).'
    assert _suppress_grounded_transition_false_positives(value, [issue]) == []
    unrelated = 'The label is too small to read.'
    assert _suppress_grounded_transition_false_positives(value, [unrelated]) == [unrelated]


def test_comparison_reviewer_uses_named_team_score_binding():
    value = {
        'narration':'Dallas led 17–14 when Green Bay took over.',
        'primary_visual':{'type':'Comparison','props':{
            'leftLabel':'GREEN BAY','leftValue':'14','rightLabel':'DALLAS','rightValue':'17',
        }},
    }
    issue = 'The graphic displays Green Bay 14 and Dallas 17, contradicting the narration that Dallas led 17-14.'
    assert _suppress_grounded_transition_false_positives(value, [issue]) == []


def test_grounded_stat_labels_do_not_trigger_empty_copy_false_positive():
    value = {
        'narration': 'The wind chill reached minus 48 degrees in Green Bay in 1967.',
        'primary_visual': {'type': 'StatCard', 'props': {
            'value': 48, 'label': 'wind chill', 'context': 'Green Bay • 1967',
        }},
    }
    issue = 'The wind chill value is unapproved text even though copy contract is empty.'
    assert _suppress_grounded_transition_false_positives(value, [issue]) == []


def test_invisible_stat_kicker_is_not_a_rendered_copy_finding():
    value = {'narration': 'The wind chill reached minus 48 degrees.',
             'primary_visual': {'type': 'StatCard', 'props': {'value': 48, 'kicker': 'NARRATED FIGURE'}}}
    issue = "The component includes a 'NARRATED FIGURE' kicker which is not in approved copy."
    assert _suppress_grounded_transition_false_positives(value, [issue]) == []


def test_requested_position_cannot_disprove_an_observed_placement_defect():
    value = {
        'text': {'position': 'top_left', 'display_text': 'more than atmosphere'},
        'editorial_contract': {'approved': True, 'display_text': 'more than atmosphere'},
        'primary_visual': {'type': 'SmartImage'},
    }
    issue = 'Approved copy is positioned bottom-left rather than top-left.'
    assert _suppress_grounded_transition_false_positives(value, [issue]) == [issue]


def test_counter_sample_is_not_mislabeled_as_wrong_final_value():
    value = {
        'narration': 'The Packers drove 68 yards toward the end zone.',
        'primary_visual': {'type': 'StatCard', 'variant': 'delta_growth_badge', 'props': {'value': 68, 'variant': 'delta_growth_badge'}},
    }
    issue = "Sample 1 displays '13 yards' which contradicts the approved copy '68 yards'."
    assert _suppress_grounded_transition_false_positives(value, [issue]) == []


def test_counter_adjudication_handles_inflected_contradiction_wording():
    value = {
        'narration': 'The Packers drove 68 yards toward the end zone.',
        'primary_visual': {'type': 'StatCard', 'variant': 'delta_growth_badge',
                           'props': {'value': 68, 'variant': 'delta_growth_badge'}},
    }
    issue = "Sample 1 shows '13 yards' which contradicts the approved copy '68 yards'."
    assert _suppress_grounded_transition_false_positives(value, [issue]) == []


def test_countup_sample_and_css_case_style_are_not_copy_or_fact_errors():
    stat = {'narration': 'The Packers drove 68 yards.', 'primary_visual': {
        'type': 'StatCard', 'variant': 'delta_growth_badge',
        'props': {'value': 68, 'variant': 'delta_growth_badge'},
    }}
    assert _suppress_grounded_transition_false_positives(stat, [
        "Sample 1 shows a partial count from the delta_growth_badge instead of the final value."
    ]) == []
    text = {'text': {'display_text': 'one-yard play'}, 'editorial_contract': {
        'approved': True, 'display_text': 'one-yard play'},
        'primary_visual': {'type': 'KineticText'}}
    assert _suppress_grounded_transition_false_positives(text, [
        "Approved text 'one-yard play' is rendered in all-caps."
    ]) == []


def test_review_alias_normalizes_overlay_highlight_role():
    from services.editorial_director.pixel_revision import _normalize_review_patch
    value = scene()
    patch = _normalize_review_patch(value, {
        'component_type': 'text_panel', 'visual_role': 'overlay_highlight',
    })
    assert 'component_type' not in patch
    assert patch['visual_role'] == 'media_overlay'


def test_unsupported_component_suggestion_falls_back_to_existing_scene_treatment():
    value = scene()
    value['media'] = {'src': 'verified.mp4', 'type': 'video'}
    patch = _normalize_review_patch(value, {
        'component_type': 'Diagram', 'visual_role': 'primary_explanation',
    })
    assert 'component_type' not in patch
    assert patch['visual_role'] == 'media_overlay'
    revised = apply_visual_patch(value, patch)
    assert revised['primary_visual']['type'] == 'SmartImage'
    assert revised['visual_role'] == 'media_overlay'


def test_low_confidence_verdict_cannot_pass():
    class Client:
        def review_editorial_frames(self,*args):
            return {'status':'pass','confidence':0,'issues':[]}
    report=review_and_revise({'scenes':[scene()]},[{'frame':25,'path':'first.png'}],lambda budget:Client())
    assert report['status']=='incomplete'


def test_unapproved_text_cannot_be_styled_into_a_heading():
    with pytest.raises(ValueError,match='unapproved'):
        apply_visual_patch(scene(),{'text_treatment':{'panel_style':'hero_type'}})


def test_specialist_variant_patch_updates_renderer_props():
    value=scene(); value['primary_visual']['props']={'variant':'full_bleed_cinematic'}
    result=apply_visual_patch(value,{'variant':'editorial_crop'})
    assert result['primary_visual']['props']['variant']=='editorial_crop'


def test_pixel_director_can_switch_media_to_grounded_diagram_component():
    value=scene()
    value['narration']='The Moon blocks sunlight, and its umbra sweeps across Earth.'
    recipe={'version':2,'id':'eclipse-mechanism','name':'Eclipse mechanism', 'layers':[
        {'kind':'diagram','x':0,'y':0,'width':100,'height':100,'text':'','color':'#F5F3EA',
         'data':[{'label':'Moon','x':23,'y':43,'label_y':10,'hide_marker':True},
                 {'label':'sunlight','x':49,'y':43,'label_y':10,'hide_marker':True},
                 {'label':'Earth','x':82,'y':43,'label_y':10,'hide_marker':True}],
         'links':[{'from':'Moon','to':'sunlight','evidence':'The Moon blocks sunlight'}]},
        {'kind':'sphere','semantic_actor':'Moon','x':16,'y':24,'width':32,'height':38,'text':'','color':'#F5F3EA','motion':'scale','start_frame':0,'duration_frames':18},
        {'kind':'cone','semantic_actor':'sunlight','x':26,'y':26,'width':38,'height':34,'text':'','color':'#F2C14E','motion':'wipe','start_frame':12,'duration_frames':20},
        {'kind':'sphere','semantic_actor':'Earth','x':69,'y':24,'width':28,'height':38,'text':'','color':'#4D9AA1','motion':'scale','start_frame':28,'duration_frames':18},
    ]}
    result=apply_visual_patch(value,{
        'component_type':'Diagram','visual_role':'primary_explanation',
        'variant':'cause_and_effect_flow','template_recipe':recipe,
    })
    assert result['primary_visual']['type']=='Diagram'
    assert result['visual_role']=='primary_explanation'
    contract=result['primary_visual']['props']['node_copy_contract']
    assert contract['approved'] is True
    assert {row['label'] for row in contract['nodes']}=={'Moon','sunlight','Earth'}
    assert result['primary_visual']['asset']==value['primary_visual']['asset']


def test_component_switch_cannot_create_ungrounded_map_or_empty_specialist():
    with pytest.raises(ValueError,match='grounded map payload'):
        apply_visual_patch(scene(),{'component_type':'AnimatedMap','visual_role':'map_canvas'})
    with pytest.raises(ValueError,match='validated scene recipe'):
        apply_visual_patch(scene(),{'component_type':'Diagram','visual_role':'primary_explanation'})


def test_director_visual_geometry_unlocks_diagram_without_authorizing_text():
    value={'narration':'One split second that changed the story.',
           'visual_intent':'A single white goal-line stripe cuts the frame as a visual seam.',
           'primary_visual':{'type':'SmartImage'},'media':{'src':'verified.jpg'}}
    assert 'Diagram' in _repair_component_options(value)
    findings=_suppress_grounded_transition_false_positives(value,[
        'No text treatment applied: Scene.text is empty (approved_copy_available=false), yet visual_intent explicitly calls for revealing words. This indicates a failure to render the intended cinematic payoff element.',
        'The requested goal-line stripe is missing from the composition.',
    ])
    assert findings==['The requested goal-line stripe is missing from the composition.']


def test_pixel_patch_canonicalizes_component_names_and_drops_invalid_optional_variant():
    value=scene()
    value['narration']='The Moon blocks sunlight, and its umbra sweeps across Earth.'
    recipe={'version':2,'id':'eclipse-mechanism','name':'Eclipse mechanism','layers':[
        {'kind':'diagram','x':0,'y':0,'width':100,'height':100,'text':'','color':'#F5F3EA',
         'data':[{'label':'Moon','x':23,'y':43,'label_y':10,'hide_marker':True},
                 {'label':'sunlight','x':49,'y':43,'label_y':10,'hide_marker':True},
                 {'label':'Earth','x':82,'y':43,'label_y':10,'hide_marker':True}],
         'links':[{'from':'Moon','to':'sunlight','evidence':'The Moon blocks sunlight'}]},
        {'kind':'sphere','semantic_actor':'Moon','x':16,'y':24,'width':32,'height':38,'text':'','color':'#F5F3EA','motion':'scale','start_frame':0,'duration_frames':18},
        {'kind':'cone','semantic_actor':'sunlight','x':26,'y':26,'width':38,'height':34,'text':'','color':'#F2C14E','motion':'wipe','start_frame':12,'duration_frames':20},
        {'kind':'sphere','semantic_actor':'Earth','x':69,'y':24,'width':28,'height':38,'text':'','color':'#4D9AA1','motion':'scale','start_frame':28,'duration_frames':18},
    ]}
    allowed=['Diagram']
    patch=_normalize_review_patch(value,{
        'component_type':'diagram','variant':'unregistered_line_overlay',
        'visual_role':'primary_explanation','template_recipe':recipe,
    },allowed)
    assert patch['component_type']=='Diagram'
    assert 'variant' not in patch
    revised=apply_visual_patch(value,patch,allowed_components=allowed)
    assert revised['primary_visual']['type']=='Diagram'
    assert revised['visual_role']=='primary_explanation'


def test_review_recipe_normalizes_safe_layers_and_retains_only_narration_grounded_copy():
    value = scene()
    value.update({
        'scene_index': 3,
        'narration': 'One yard. Twenty-six seconds decide the game.',
        'media': {'src': 'verified-play.mp4', 'type': 'video'},
        'primary_visual': {'type': 'SmartVideo', 'asset': {'src': 'verified-play.mp4'}},
    })
    patch = _normalize_review_patch(value, {
        'component_type': 'Diagram', 'visual_role': 'primary_explanation',
        'template_recipe': {'layers': [
            {'type': 'rectangle', 'props': {'x': '3%', 'y': '8%', 'width': '2%', 'height': '82%', 'fill': '#F2C14E'}},
            {'type': 'text', 'props': {'x': '8%', 'y': '8%', 'width': '42%', 'height': '12%', 'content': 'One yard.', 'color': '#FFFFFF'}},
            {'type': 'text', 'props': {'x': '8%', 'y': '25%', 'width': '42%', 'height': '12%', 'content': 'Instant replay', 'color': '#FFFFFF'}},
        ]},
    })
    assert 'component_type' not in patch  # the layers are an overlay, not a semantic diagram
    assert patch['visual_role'] == 'media_overlay'
    assert [layer['kind'] for layer in patch['template_recipe']['layers']] == ['rect', 'text']
    assert patch['template_recipe']['layers'][0]['x'] == 3
    assert patch['template_recipe']['layers'][1]['text'] == 'One yard.'
    assert patch['recipe_copy_contract']['spans'] == [{'display_text': 'One yard.', 'source_span': 'One yard.'}]
    revised = apply_visual_patch(value, patch)
    assert revised['template_id'] == 'generated-recipe'
    assert revised['recipe_copy_contract']['approved'] is True
    assert revised['visual_role'] == 'media_overlay'


def test_review_cache_key_changes_when_director_feedback_changes(tmp_path):
    image = tmp_path / 'frame.png'
    image.write_bytes(b'frame pixels for deterministic cache key')
    film = {'fps': 30, 'scenes': [scene()], '_creative_feedback': {'0': {'issue': 'initial composition'}}}
    cache = {}
    calls = []

    class Client:
        def review_editorial_frames(self, paths, context):
            calls.append(context)
            return {'status': 'pass', 'confidence': 90, 'issues': []}

    frame = [{'frame': 25, 'path': str(image)}]
    assert review_and_revise(film, frame, lambda budget: Client(), pass_cache=cache)['status'] == 'pass'
    film['_creative_feedback']['0'] = {'issue': 'new guidance: protect the subject'}
    result = review_and_revise(film, frame, lambda budget: Client(), pass_cache=cache)
    assert result['status'] == 'pass'
    assert len(calls) == 2


def test_recipe_copy_provenance_is_rechecked_at_production_compile():
    from services.editorial_director.production_contract import compile_production_timeline

    scene_with_copy = {
        'start': 0, 'duration': 3, 'narration': 'One yard changed everything.',
        'primary_visual': {'type': 'SmartVideo'}, 'media': {'src': 'verified.mp4', 'type': 'video'},
        'template_recipe': {'version': 2, 'id': 'review-copy', 'name': 'Reviewed copy', 'layers': [
            {'kind': 'text', 'x': 8, 'y': 8, 'width': 40, 'height': 12, 'text': 'One yard', 'color': '#FFFFFF'},
        ]},
        'recipe_copy_contract': {'version': 1, 'source': 'pixel_director_narration_span', 'approved': True,
                                 'spans': [{'display_text': 'One yard', 'source_span': 'One yard'}]},
    }
    assert compile_production_timeline({'fps': 30, 'scenes': [scene_with_copy]})['production_audit']['status'] == 'pass'
    scene_with_copy['recipe_copy_contract']['spans'][0]['source_span'] = 'invented phrase'
    audit = compile_production_timeline({'fps': 30, 'scenes': [scene_with_copy]})['production_audit']
    assert any(error['code'] == 'invalid_recipe_copy_provenance' for error in audit['errors'])


def test_pixel_director_can_move_only_approved_copy_to_protect_the_visual_subject():
    value=scene()
    value['media_plan']={'decision_source':'llm_director'}
    value['editorial_contract']={'version':4,'source':'llm','approved':True,
        'display_text':'explores a reef','source_span':'explores a reef'}
    result=apply_visual_patch(value,{'text_position':'top_right'})
    assert result['text']['position']=='top_right'
    assert result['design']['position']=='top_right'
    assert result['primary_visual']['props']['position']=='top_right'
    assert result['primary_visual']['props']['_position']=='top_right'
    assert result['editorial_contract']==value['editorial_contract']


def test_pixel_director_cannot_reposition_unapproved_copy_or_use_arbitrary_coordinates():
    with pytest.raises(ValueError,match='unapproved'):
        apply_visual_patch(scene(),{'text_position':'top_right'})
    value=scene(); value['media_plan']={'decision_source':'llm_director'}
    value['editorial_contract']={'version':4,'source':'llm','approved':True,
        'display_text':'explores a reef','source_span':'explores a reef'}
    with pytest.raises(ValueError,match='position'):
        apply_visual_patch(value,{'text_position':'left_of_the_face'})


def test_component_repair_options_are_scene_grounded_and_enforced():
    value=scene()
    assert _repair_component_options(value)==[]
    with pytest.raises(ValueError,match='not grounded or eligible'):
        apply_visual_patch(value,{'component_type':'AnimatedMap','visual_role':'map_canvas'},allowed_components=[])

    value['narration']='In 1912 the ship left port; in 1914 the route changed.'
    assert 'Timeline' in _repair_component_options(value)
    assert 'Chart' not in _repair_component_options(value)
    value['primary_visual']['props']={'map_shot':{'enabled':True,'geographic_scope':'North Atlantic'}}
    assert 'AnimatedMap' in _repair_component_options(value)


def test_pixel_reviewer_receives_only_scene_eligible_components_and_map_context():
    import json
    contexts=[]
    class Client:
        def review_editorial_frames(self,paths,context):
            contexts.append(json.loads(context))
            return {'status':'pass','confidence':90,'issues':[]}
    value=scene()
    review_and_revise({'fps':30,'scenes':[value]},[{'frame':25,'path':'actual.png'}],lambda budget:Client())
    protected=contexts[0]['scene']
    assert contexts[0]['repair_component_options']==[]
    assert contexts[0]['allowed_components']=={}
    assert 'map_shot' in protected and protected['map_shot'] is None


def test_pixel_reviewer_cannot_apply_an_ineligible_map_even_if_model_ignores_its_shortlist():
    class Client:
        def review_editorial_frames(self,paths,context):
            return {'status':'needs_revision','confidence':96,'repair_kind':'design',
                'issues':['A map might better explain the scene.'],
                'patch':{'component_type':'AnimatedMap','visual_role':'map_canvas'}}
    report=review_and_revise({'fps':30,'scenes':[scene()]},
        [{'frame':25,'path':'actual.png'}],lambda budget:Client())
    assert report['changed_scene_indices']==[]
    assert report['scenes'][0]['revision_error']=='Map canvas role requires a map component'
    assert report['scenes'][0]['rejected_patch']['component_type']=='AnimatedMap'


def test_null_optional_fields_do_not_discard_a_valid_revision():
    result=apply_visual_patch(scene(),{'variant':'editorial_crop','template_recipe':None,'text_treatment':None})
    assert result['primary_visual']['variant']=='editorial_crop'


def test_empty_geometry_does_not_validate_a_missing_recipe_or_discard_copy_style():
    value=scene()
    value['media_plan']={'decision_source':'llm_director'}
    value['editorial_contract']={'version':4,'source':'llm','approved':True,
        'display_text':'explores a reef','source_span':'explores a reef'}
    result=apply_visual_patch(value,{
        'recipe_geometry':[],
        'text_treatment':{'panel_style':'hero_type','panel_background':'#0C1718'},
    })
    assert result['text'].get('panel_style')=='hero_type'
    assert result['editorial_contract']==value['editorial_contract']


def test_media_failures_are_repaired_even_when_critic_also_suggests_design():
    class Client:
        def review_editorial_frames(self,*args):
            return {'status':'needs_revision','confidence':92,
                'repair_kind':'design',
                'issues':['Generic aerial footage is contradicting the narration about a bright eclipse.'],
                'patch':{'variant':'editorial_crop'}}
    report=review_and_revise({'fps':30,'scenes':[scene()]},
        [{'frame':25,'path':'actual.png'}],lambda budget:Client())
    assert report['changed_scene_indices']==[0]
    assert report['scenes'][0]['media_repair_required'] is True


def test_low_information_wrong_media_is_classified_for_retrieval_repair():
    class Client:
        def review_editorial_frames(self,*args):
            return {'status':'needs_revision','confidence':94,'repair_kind':'design',
                'issues':['Observed low information frames: a static small crescent on black fails to illustrate the planetary-scale narration.'],
                'patch':None}
    report=review_and_revise({'fps':30,'scenes':[scene()]},
        [{'frame':25,'path':'actual.png'}],lambda budget:Client())
    assert report['scenes'][0]['media_repair_required'] is True


def test_unaddressable_geometry_is_ignored_while_valid_text_style_is_applied():
    value=scene()
    value['media_plan']={'decision_source':'llm_director'}
    value['editorial_contract']={'version':4,'source':'llm','approved':True,
        'display_text':'explores a reef','source_span':'explores a reef'}
    result=apply_visual_patch(value,{
        'recipe_geometry':[{'layer_index':0,'x':40,'y':40,'width':20,'height':10}],
        'text_treatment':{'panel_style':'hero_type','panel_background':'#0C1718'},
    })
    assert result['text'].get('panel_style')=='hero_type'
    assert 'template_recipe' not in result


def test_map_critic_does_not_demand_an_unverified_geographic_route():
    value={'narration':'Watch the global process unfold across Earth.','start':0,'from':0,
        'duration':4,'duration_in_frames':120,
        'primary_visual':{'type':'AnimatedMap','variant':'country_spotlight','props':{
            'map_shot':{'render_style':'globe_3d','geographic_scope':'Earth',
                        'route_status':'not_established_by_script'}}}}
    class Client:
        def review_editorial_frames(self,*args):
            return {'status':'needs_revision','confidence':90,'repair_kind':'media',
                'issues':['The globe has no requested trace motion to reveal the path across Earth.'],
                'patch':None}
    report=review_and_revise({'fps':30,'scenes':[value]},
        [{'frame':25,'path':'map.png'}],lambda budget:Client())
    assert report['status']=='pass'
    assert report['unresolved_scene_indices']==[]
    assert len(report['scenes'][0]['suppressed_ungrounded_route_findings'])==1


def test_accepted_frame_review_is_reused_only_for_identical_pixels_and_context(tmp_path):
    path=tmp_path/'frame.png';path.write_bytes(b'original actual frame')
    film={'fps':30,'scenes':[scene()]};cache={};calls=[]
    class Client:
        def review_editorial_frames(self,*args):
            calls.append(args)
            return {'status':'pass','confidence':90,'issues':[]}
    frames=[{'frame':25,'path':str(path)}]
    first=review_and_revise(film,frames,lambda budget:Client(),pass_cache=cache)
    assert first['status']=='pass'
    second=review_and_revise(film,frames,lambda budget:Client(),pass_cache=cache)
    assert second['scenes'][0]['reused_unchanged_scene'] is True
    assert len(calls)==1
    path.write_bytes(b'changed pixels')
    review_and_revise(film,frames,lambda budget:Client(),pass_cache=cache)
    assert len(calls)==2
    film['scenes'][0]['text']['position']='top_left'
    review_and_revise(film,frames,lambda budget:Client(),pass_cache=cache)
    assert len(calls)==3


def test_verified_legacy_llm_handoff_keeps_copy_provenance():
    value=scene()
    value['media_plan']={'decision_source':'llm_director'}
    value['editorial_contract']={'version':4,'approved':True,'display_text':'explores a reef','source_span':'explores a reef'}
    result=apply_visual_patch(value,{'text_treatment':{'panel_style':'hero_type'}})
    assert result['editorial_contract']==value['editorial_contract']


def test_legacy_copy_without_director_provenance_stays_unapproved():
    value=scene()
    value['editorial_contract']={'version':4,'approved':True,'display_text':'explores a reef','source_span':'explores a reef'}
    with pytest.raises(ValueError,match='unapproved'):
        apply_visual_patch(value,{'text_treatment':{'panel_style':'hero_type'}})


def test_critic_uses_final_scene_palette_instead_of_film_default():
    film={'fps':30,'visual_dna':{'palette':{'accent':'#F2C14E'}},'scenes':[scene()]}
    film['scenes'][0]['visual_dna']={'palette':{'accent':'#4F8B78'}}
    contexts=[]
    class Client:
        def review_editorial_frames(self,paths,context):
            import json
            contexts.append(json.loads(context))
            return {'status':'pass','confidence':90,'issues':[]}
    report=review_and_revise(film,[{'frame':25,'path':'first.png'}],lambda budget:Client())
    assert report['status']=='pass'
    assert contexts[0]['visual_dna']['palette']['accent']=='#4F8B78'


def test_measured_blank_frame_cannot_be_cached_as_creatively_accepted():
    contexts=[];cache={}
    class Client:
        def review_editorial_frames(self,paths,context):
            import json
            contexts.append(json.loads(context))
            return {'status':'pass','confidence':90,'issues':[]}
    film={'fps':30,'scenes':[scene()]}
    report=review_and_revise(film,[{'frame':25,'path':'first.png'}],lambda budget:Client(),
                            pixel_audit={'findings':[{'code':'low_information_frames','severity':'error','frame_indices':[1]}]},
                            pass_cache=cache)
    assert report['status']=='needs_revision'
    assert contexts[0]['measured_pixel_defects']==[{'frame':25,'codes':['low_information_frames']}]
    assert not cache


def test_missing_unapproved_copy_is_removed_but_grounded_geometry_remains_actionable():
    value={'narration':'One yard decides the game.','text':{},
           'primary_visual':{'type':'SmartVideo'}}
    missing=['Missing approved visual intent: The scene directive requires the phrase '
             "'One yard.' to be displayed large and clean with a single white goal-line stripe anchoring the frame."]
    assert _suppress_grounded_transition_false_positives(value, missing)==[
        'Missing grounded visual geometry: single white goal-line stripe.'
    ]
    visible=['Unauthorized overlay text: The frame displays unapproved words over the player.']
    assert _suppress_grounded_transition_false_positives(value, visible)==visible


def test_identical_panel_and_palette_surface_color_is_not_a_palette_violation():
    value={'primary_visual':{'type':'SmartVideo'},'text':{}}
    issue=('The panel background color (#1A2420) does not match the visual_dna.palette '
           'surface (#1A2420) or primary (#F5F3EA), causing a slight disconnect.')
    assert _suppress_grounded_transition_false_positives(value, [issue])==[]


def test_pixel_redesign_records_the_actual_strong_director_model(monkeypatch):
    import services.editorial_director.pixel_revision as pixel_revision

    monkeypatch.setattr(pixel_revision, '_compact_redesign', lambda *_a, **_k: {
        'patch':{'variant':'step_by_step_process'},'model':'openai/gpt-6-sol',
    })
    film={'fps':30,'scenes':[{
        'narration':'The sun heats water, vapor rises, then rain returns.',
        'duration':4,'duration_in_frames':120,
        'primary_visual':{'type':'Diagram','variant':'cause_and_effect_flow','props':{}},
    }]}

    class Client:
        api_key='test-key'
        def review_editorial_frames(self,*_args):
            return {'status':'needs_revision','confidence':90,'repair_kind':'design',
                    'issues':['The explanatory geometry needs clearer sequence.'],'patch':None}

    report=review_and_revise(film,[{'frame':45,'path':'frame.png'}],lambda _budget:Client())
    assert report['changed_scene_indices']==[0]
    assert report['scenes'][0]['designer_model']=='openai/gpt-6-sol'
    assert film['scenes'][0]['primary_visual']['variant']=='step_by_step_process'


def test_pixel_reviewer_receives_caption_and_source_authorization():
    import json
    captured = {}
    film = {'fps':30, 'scenes':[{'narration':'The spacecraft approaches.', 'duration':4,
        'duration_in_frames':120, 'text':{'frame_mode':'media_only'},
        'captions_enabled':True, 'caption_words':[{'text':'spacecraft','start':1,'end':2}],
        'primary_visual':{'type':'SmartImage','props':{}},
        'media':{'type':'image','source_credit':{'approved':True,'label':'NASA archive'}}}]}
    class Client:
        def review_editorial_frames(self, frames, context):
            captured.update(json.loads(context))
            return {'status':'pass','confidence':90,'issues':[],'patch':None}
    report = review_and_revise(film, [{'frame':45,'path':'frame.png'}], lambda budget:Client())
    assert captured['scene']['captions_enabled'] is True
    assert captured['scene']['caption_words'][0]['text'] == 'spacecraft'
    assert captured['scene']['media']['source_credit']['approved'] is True
    assert 'not explicitly enabled narration captions' in captured['copy_review_policy']
    assert report['scenes'][0]['status'] == 'pass'
