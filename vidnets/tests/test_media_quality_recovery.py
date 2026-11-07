import copy
import time
import pytest
from PIL import Image, ImageDraw
from services.media_quality_recovery import recover_repeated_media


def _asset(path, tone):
    image=Image.new('RGB',(160,90),(18,42,68))
    draw=ImageDraw.Draw(image)
    draw.rectangle((10,10,70,80),fill=tone)
    image.save(path)
    return str(path)


def test_recovery_only_adds_pixel_verified_candidates_for_failed_scenes(tmp_path):
    items=[{'path':_asset(tmp_path/'first.png',(240,185,65))},
           {'path':_asset(tmp_path/'second.png',(80,175,140))}]
    calls=[]
    def retrieve(index,text,item,deadline):
        calls.append((index,text))
        return [{'path':_asset(tmp_path/'fresh.png',(230,90,40))},
                {'path':_asset(tmp_path/'rejected.png',(230,90,40))}]
    def verify(index,candidate):
        return {'accepted':candidate['path'].endswith('fresh.png'),'visual_checked':True,'verified_asset_sha256':'real-digest'}
    report=recover_repeated_media(items,['one','two'],{'unresolved_repeats':[{'scene_index':1}]},retrieve,verify)
    assert calls==[(1,'two')]
    assert items[0]['path'].endswith('first.png')
    assert items[1]['alternates'][0]['visual_decision']['verified_asset_sha256']=='real-digest'
    assert len(items[1]['alternates'])==1
    assert report['acceptance']=='requires_repeat_check_again'


def test_recovery_rejects_same_pixels_and_retries_for_a_distinct_candidate(tmp_path):
    repeated=_asset(tmp_path/'same-content-different-name.png',(200,40,20))
    unique=_asset(tmp_path/'genuinely-new.png',(20,190,150))
    items=[{'path':_asset(tmp_path/'previous.png',(200,40,20))},
           {'path':_asset(tmp_path/'current.png',(30,70,220))}]
    calls=[]
    def retrieve(index,text,item,deadline):
        calls.append(item['_repeat_recovery_attempt'])
        return [{'path':repeated if item['_repeat_recovery_attempt']==0 else unique,
                 'candidate_id':f"candidate-{item['_repeat_recovery_attempt']}"}]
    report=recover_repeated_media(items,['previous','target'],
        {'unresolved_repeats':[{'scene_index':1}]},retrieve,
        lambda *_:{'accepted':True,'visual_checked':True,'verified_asset_sha256':'verified'})
    assert calls==[0,1]
    assert report['rejected_duplicate_candidate_count']==1
    assert items[1]['alternates'][0]['path']==unique


def test_repeat_repair_uses_unused_verified_scene_alternate_before_network(tmp_path, monkeypatch):
    import hashlib
    import app
    import services.pipeline_v2_media_adapter as media_adapter
    from services.visual_requirement import build_visual_requirement

    selected=_asset(tmp_path/'selected.png',(210,35,25))
    unused=_asset(tmp_path/'verified-alternate.png',(25,150,210))
    title='A precise documentary'
    script='The owned action is clearly described.'
    scene_text='The owned action is clearly described.'
    requirement=build_visual_requirement(title=title,script=script,scene_text=scene_text)
    with open(unused,'rb') as source_file:
        digest=hashlib.sha256(source_file.read()).hexdigest()
    candidate={
        'path':unused,'source':'web','provider':'web','media_type':'image',
        'candidate_id':'already-verified-unused-alternate','verified':True,
        'status':'accepted','requirement_hash':requirement['requirement_hash'],
        'visual_decision':{
            'accepted':True,'visual_checked':True,'verified_asset_sha256':digest,
            'verified_requirement_hash':requirement['requirement_hash'],
        },
    }
    items=[
        {'path':selected,'source':'web','resolution':{'alternates':[candidate]}},
        {'path':selected,'source':'web','visual_requirement':requirement},
    ]
    verified=[]
    def fake_build(_config, _scenes):
        def verify(scene, row):
            verified.append((scene['scene_index'],row['candidate_id']))
            return {
                'accepted':True,'visual_checked':True,
                'verified_asset_sha256':digest,
                'verified_requirement_hash':scene['visual_requirement']['requirement_hash'],
                'verification_status':'verified',
            }
        return verify,None
    monkeypatch.setattr(media_adapter,'build_qwen_verifier',fake_build)
    monkeypatch.setattr(app,'_run_web_media_branch',lambda *args,**kwargs: pytest.fail('unexpected web retrieval'))
    report=app._recover_perceptual_media_candidates(
        items,[scene_text,scene_text],[2,2],
        {'perceptual_repetition_audit':{'unresolved_repeats':[{'scene_index':1}]}},
        {'openrouter_api_key':'test-key'},title,script,
    )
    assert report['new_verified_candidate_scene_indices']==[1]
    assert verified==[(1,'already-verified-unused-alternate')]
    assert items[1]['alternates'][0]['path']==unused


def test_repeat_repair_keeps_saved_scene_contract_when_title_rederivation_differs(tmp_path, monkeypatch):
    import hashlib
    import app
    import services.pipeline_v2_media_adapter as media_adapter
    from services.visual_requirement import build_visual_requirement

    saved_requirement = build_visual_requirement(
        title='Seattle Seahawks versus Patriots final play',
        script='Malcolm Butler intercepts Russell Wilson at Super Bowl XLIX.',
        scene_text='Malcolm Butler intercepts Russell Wilson at Super Bowl XLIX.',
    )
    selected = _asset(tmp_path/'selected.png', (210, 35, 25))
    unused = _asset(tmp_path/'verified-alternate.png', (25, 150, 210))
    digest = hashlib.sha256(open(unused, 'rb').read()).hexdigest()
    candidate = {
        'path': unused, 'source': 'web', 'provider': 'web', 'media_type': 'image',
        'candidate_id': 'saved-scene-contract-alternate',
        'visual_decision': {
            'accepted': True, 'visual_checked': True,
            'verified_asset_sha256': digest,
            'verified_requirement_hash': saved_requirement['requirement_hash'],
        },
    }
    items = [
        {'path': selected, 'resolution': {'alternates': [candidate]}},
        {'path': selected, 'visual_requirement': saved_requirement},
    ]
    seen = []

    def fake_build(_config, scenes):
        def verify(scene, row):
            seen.append((scene['visual_requirement']['requirement_hash'], row['candidate_id']))
            return {
                'accepted': True, 'visual_checked': True,
                'verified_asset_sha256': digest,
                'verified_requirement_hash': scene['visual_requirement']['requirement_hash'],
                'verification_status': 'verified',
            }
        return verify, None

    monkeypatch.setattr(media_adapter, 'build_qwen_verifier', fake_build)
    monkeypatch.setattr(app, '_run_web_media_branch', lambda *a, **k: pytest.fail('unexpected network retrieval'))
    report = app._recover_perceptual_media_candidates(
        items, ['One yard.'] * 2, [2, 2],
        {'perceptual_repetition_audit': {'unresolved_repeats': [{'scene_index': 1}]}},
        {'openrouter_api_key': 'test-key'}, 'Yard, One Read', 'One yard.',
    )

    assert report['new_verified_candidate_scene_indices'] == [1]
    assert seen == [(saved_requirement['requirement_hash'], 'saved-scene-contract-alternate')]
    assert items[1]['visual_requirement']['canonical_entity'] == saved_requirement['canonical_entity']


def test_transport_or_metadata_acceptance_cannot_bypass_pixels():
    items=[{}]
    report=recover_repeated_media(items,['one'],{'unresolved_repeats':[{'scene_index':0}]},
        lambda *args:[{'path':'unreviewed.jpg'}],lambda *args:{'accepted':True})
    assert report['new_verified_candidate_scene_indices']==[]
    assert items==[{}]


def test_slow_fresh_retrieval_does_not_mutate_after_deadline():
    items=[{}]; original=copy.deepcopy(items)
    def retrieve(*args):
        time.sleep(.12)
        return [{'path':'late.jpg'}]
    report=recover_repeated_media(items,['one'],{'unresolved_repeats':[{'scene_index':0}]},retrieve,
        lambda *args:{'accepted':True,'visual_checked':True,'verified_asset_sha256':'digest'},budget_seconds=.01)
    assert report['failures']
    time.sleep(.15)
    assert items==original


def test_pixel_repair_changes_only_failed_scene_and_preserves_other_reviewed_shots(tmp_path, monkeypatch):
    import hashlib
    import app
    import services.media_shot_plan as planner
    first = _asset(tmp_path/'first.png', (240, 185, 65))
    rejected = _asset(tmp_path/'rejected.png', (200, 30, 30))
    fresh = _asset(tmp_path/'fresh.png', (20, 190, 150))
    digest = hashlib.sha256(open(fresh, 'rb').read()).hexdigest()
    first_shot = {'path': first, 'candidate_id': 'reviewed-first', 'shot_start': 0,
                  'shot_end': 2, 'duration': 2,
                  'visual_decision': {'accepted': True, 'verified_asset_sha256': 'first-proof'}}
    rejected_shot = {'path': rejected, 'candidate_id': 'rejected-shot', 'shot_start': 0,
                     'shot_end': 2, 'duration': 2}
    manifest={'media_shot_plan': {'shot_count': 2}, 'scenes':[
        {'narration':'first','duration':2,'media':{'path':first,'shots':[first_shot]},'primary_visual':{}},
        {'narration':'second','duration':2,'media':{'path':rejected,'shots':[rejected_shot]},'primary_visual':{}},
    ]}
    def recovery(items,*args):
        items[1]['alternates']=[{
            'path':fresh,'type':'image','media_type':'image','candidate_id':'fresh-verified',
            'visual_decision': {'accepted':True,'visual_checked':True,
                'verified_asset_sha256':digest,'verified_requirement_hash':'target-contract'},
            'verified':True,
        }]
        return {'new_verified_candidate_scene_indices':[1]}
    def audit(plans, *_args, **_kwargs):
        return {'status':'pass','unresolved_repeats':[],'checked_shot_count':sum(map(len, plans))}
    monkeypatch.setattr(app,'_recover_perceptual_media_candidates',recovery)
    monkeypatch.setattr(planner,'build_media_shot_plan',lambda *a,**k: pytest.fail('must not replan unaffected scenes'))
    monkeypatch.setattr(planner,'_repair_perceptual_reuse',audit)
    config={'_visual_requirement_rows':[{}, {'schema_version':'visual_requirement.v2',
        'canonical_entity':'Super Bowl XLIX final play','subject_type':'named_entity',
        'requirement_hash':'target-contract'}]}
    assert app._repair_creative_media(manifest,[{'scene_index':1,'repair_kind':'media'}],30,config)
    first_scene_shots=manifest['scenes'][0]['media']['shots']
    assert first_scene_shots == [first_shot]
    assert manifest['scenes'][0]['media']['path'] == first
    assert manifest['scenes'][1]['media']['shots'][0]['path']==fresh
    assert manifest['scenes'][1]['media']['shots'][0]['visual_decision']['verified_asset_sha256']==digest
    assert manifest['media_shot_plan']['shot_count']==2
    assert manifest['media_shot_plan']['perceptual_repetition_audit']['unresolved_repeats']==[]
    assert manifest['media_shot_plan']['perceptual_repetition_audit']['status']=='pass'


def test_creative_recovery_queries_use_neighboring_beats_to_avoid_duplicate_views(monkeypatch):
    import json
    import app
    import services.openrouter_llm as openrouter
    import services.pipeline_v2_media_adapter as media_adapter

    captured={}

    class FakeClient:
        def __init__(self, **kwargs):
            pass
        def chat(self, **kwargs):
            captured.update(kwargs)
            return {'subject':'the exact action in this beat','queries':['a distinct close view']}

    monkeypatch.setattr(openrouter,'OpenRouterLLM',FakeClient)
    monkeypatch.setattr(media_adapter,'build_qwen_verifier',lambda *args,**kwargs:(lambda *a,**k:{},None))
    monkeypatch.setattr(app,'_run_web_media_branch',lambda *args,**kwargs:{'web_result':{'image_paths':[]}})
    report=app._recover_perceptual_media_candidates(
        [{},{},{}],['previous visual beat','owned scene needs a better view','next visual beat'],[2,2,2],
        {'perceptual_repetition_audit':{'unresolved_repeats':[{'scene_index':1}]}},
        {'openrouter_api_key':'test-key'},'topic','full script')

    prompt=json.loads(captured['user_prompt'])
    assert prompt['owned_scene']=='owned scene needs a better view'
    assert [row['narration'] for row in prompt['neighboring_beats']]==['previous visual beat','next visual beat']
    assert 'do not return another image of the same wide subject' in captured['system_prompt']
    assert report['requested_scene_indices']==[1]
    assert report['new_verified_candidate_scene_indices']==[]


def test_repeat_repair_uses_unused_verified_scene_alternate_before_network(tmp_path, monkeypatch):
    import hashlib
    import app
    import services.pipeline_v2_media_adapter as media_adapter
    from services.visual_requirement import build_visual_requirement

    selected=_asset(tmp_path/'selected.png',(210,35,25))
    unused=_asset(tmp_path/'verified-alternate.png',(25,150,210))
    title='A precise documentary'
    script='The owned action is clearly described.'
    scene_text='The owned action is clearly described.'
    requirement=build_visual_requirement(title=title,script=script,scene_text=scene_text)
    with open(unused,'rb') as source_file:
        digest=hashlib.sha256(source_file.read()).hexdigest()
    candidate={
        'path':unused,'source':'web','provider':'web','media_type':'image',
        'candidate_id':'already-verified-unused-alternate','verified':True,
        'status':'accepted','requirement_hash':requirement['requirement_hash'],
        'visual_decision':{
            'accepted':True,'visual_checked':True,'verified_asset_sha256':digest,
            'verified_requirement_hash':requirement['requirement_hash'],
        },
    }
    items=[
        {'path':selected,'source':'web','resolution':{'alternates':[candidate]}},
        {'path':selected,'source':'web','visual_requirement':requirement},
    ]
    verified=[]
    def fake_build(_config, _scenes):
        def verify(scene, row):
            verified.append((scene['scene_index'],row['candidate_id']))
            return {
                'accepted':True,'visual_checked':True,
                'verified_asset_sha256':digest,
                'verified_requirement_hash':scene['visual_requirement']['requirement_hash'],
                'verification_status':'verified',
            }
        return verify,None
    monkeypatch.setattr(media_adapter,'build_qwen_verifier',fake_build)
    monkeypatch.setattr(app,'_run_web_media_branch',lambda *args,**kwargs: pytest.fail('unexpected web retrieval'))
    report=app._recover_perceptual_media_candidates(
        items,[scene_text,scene_text],[2,2],
        {'perceptual_repetition_audit':{'unresolved_repeats':[{'scene_index':1}]}},
        {'openrouter_api_key':'test-key'},title,script,
    )
    assert report['new_verified_candidate_scene_indices']==[1]
    assert verified==[(1,'already-verified-unused-alternate')]
    assert items[1]['alternates'][0]['path']==unused


def test_production_repeat_audit_targets_only_the_repeated_scene_for_retry():
    from services.native_render_policy import (
        _grounded_graphic_fallback_feedback,
        _perceptual_repeat_repair_findings,
    )

    findings=_perceptual_repeat_repair_findings([
        {'code':'unresolved_perceptual_repetition','repeats':[
            {'scene_index':3,'matches_scene_index':1},
            {'scene_index':3,'matches_scene_index':2},
        ]},
        {'code':'unrelated_blocker'},
    ])
    assert findings==[{
        'scene_index':3,
        'repair_kind':'media',
        'fallback_reason':'repeated_pixels',
        'issues':['Fresh candidate repeats the actual imagery in another scene. Find a distinct, scene-specific view; do not return another file of the same subject/composition.'],
        'matching_scene_indices':[1,2],
    }]
    feedback=_grounded_graphic_fallback_feedback(findings)
    assert feedback['3']['repeated_visual_graphic_fallback'] is True
    assert feedback['3']['matching_scene_indices']==[1,2]
    assert 'script or research facts' in feedback['3']['issues'][0]


def test_repeated_visual_fallback_unlocks_grounded_diagram_for_scene_review():
    from services.editorial_director.pixel_revision import _repair_component_options

    scene={'narration':'The game is over.','primary_visual':{'type':'SmartVideo'}}
    assert _repair_component_options(scene)==[]
    feedback={'repeated_visual_graphic_fallback':True}
    assert _repair_component_options(scene,feedback)==['Diagram']


def test_only_explicit_minor_polish_can_be_deferred_after_local_audits_pass():
    from services.native_render_policy import _defer_minor_creative_findings

    creative={
        'status':'needs_revision','unresolved_scene_indices':[2],
        'scenes':[{'scene_index':2,'status':'needs_revision','repair_kind':'layout',
                   'issues':['A slight aesthetic transition mismatch could use a little polish.']}],
    }
    deferred=_defer_minor_creative_findings(
        creative, {'status':'pass'}, {'status':'pass'},
    )
    assert [row['scene_index'] for row in deferred]==[2]
    assert creative['status']=='pass_with_notes'
    assert creative['unresolved_scene_indices']==[]
    assert creative['scenes'][0]['status']=='deferred_minor'


@pytest.mark.parametrize('issue', [
    'Wrong subject: unrelated match footage does not show the narrated event.',
    'The overlay obscures the central player and the ball.',
    'This number is unsupported by the verified script.',
    'A slight contrast mismatch makes the chart label unreadable.',
])
def test_material_or_ambiguous_creative_findings_still_block(issue):
    from services.native_render_policy import _defer_minor_creative_findings

    creative={
        'status':'needs_revision','unresolved_scene_indices':[1],
        'scenes':[{'scene_index':1,'status':'needs_revision','repair_kind':'layout','issues':[issue]}],
    }
    assert _defer_minor_creative_findings(
        creative, {'status':'pass'}, {'status':'pass'},
    )==[]
    assert creative['unresolved_scene_indices']==[1]


def test_minor_creative_finding_never_defers_when_local_audit_fails():
    from services.native_render_policy import _defer_minor_creative_findings

    creative={
        'status':'needs_revision','unresolved_scene_indices':[1],
        'scenes':[{'scene_index':1,'status':'needs_revision','repair_kind':'layout',
                   'issues':['A slight aesthetic transition mismatch could use a little polish.']}],
    }
    assert _defer_minor_creative_findings(
        creative, {'status':'pass'}, {'status':'fail'},
    )==[]
    assert creative['unresolved_scene_indices']==[1]


def test_nonmaterial_creative_notes_can_be_reported_without_blocking_non_strict_delivery():
    from services.native_render_policy import _nonmaterial_creative_advisories

    creative={
        'status':'needs_revision','unresolved_scene_indices':[4,10],
        'scenes':[
            {'scene_index':4,'status':'needs_revision','repair_kind':'layout',
             'issues':['Consider a more distinctive visual flourish for this beat.']},
            {'scene_index':10,'status':'needs_revision','repair_kind':'design',
             'issues':['The composition could use more stylistic variation.']},
        ],
    }
    assert [row['scene_index'] for row in _nonmaterial_creative_advisories(
        creative, {'status':'pass'},
    )] == [4,10]


@pytest.mark.parametrize('issue', [
    'Wrong player identity in the main image.',
    'The score is unsupported by the script.',
    'A missing label makes the diagram ambiguous.',
])
def test_nonmaterial_creative_delivery_advisory_never_hides_material_findings(issue):
    from services.native_render_policy import _nonmaterial_creative_advisories

    creative={
        'status':'needs_revision','unresolved_scene_indices':[2],
        'scenes':[{'scene_index':2,'status':'needs_revision','repair_kind':'layout','issues':[issue]}],
    }
    assert _nonmaterial_creative_advisories(creative, {'status':'pass'}) == []


def test_creative_media_repair_only_runs_for_actual_source_mismatch():
    from services.native_render_policy import _is_actual_media_mismatch

    assert not _is_actual_media_mismatch({
        'repair_kind':'media','media_repair_required':True,
        'issues':['The diagram is missing the narrated game-day date.'],
    })
    assert _is_actual_media_mismatch({
        'repair_kind':'design',
        'issues':['Visual mismatch: frame shows another player, but the narration describes Malcolm Butler.'],
    })


def test_optional_creative_media_repair_returns_at_its_deadline():
    from threading import Event
    from time import monotonic
    from services.native_render_policy import _run_bounded_media_repair

    release = Event()
    def slow_repair(*args):
        release.wait(1.0)
        return True

    started = monotonic()
    assert not _run_bounded_media_repair(slow_repair, {}, [], 0.1)
    assert monotonic() - started < 0.6
    release.set()


def test_preview_review_timeout_falls_back_only_when_non_strict():
    from services.native_render_policy import _preview_review_fallback

    record = _preview_review_fallback('preview timed out', strict=False)
    assert record == {
        'status': 'skipped', 'fallback': 'direct_render',
        'reason': 'preview timed out', 'llm_review_certified': False,
    }
    with pytest.raises(TimeoutError, match='preview timed out'):
        _preview_review_fallback('preview timed out', strict=True)
