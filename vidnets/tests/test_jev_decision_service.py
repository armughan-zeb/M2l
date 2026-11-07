from services.jev_decision_service import (
    DEFAULT_ENDPOINT, DEFAULT_MODEL, choose_youtube_candidate, classify_review_findings,
)


class _Response:
    def __init__(self, payload):
        self.payload=payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_jev_uses_bounded_typed_choices_and_requires_high_confidence():
    captured={}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response({
            'model':'typesafe/jev-1.13-20260917', 'id':'decision-1',
            'answers':{
                'scene_0':{
                    'type':'choice','choice':'minor_polish','confidence':0.91,
                    'probabilities':{
                        'minor_polish':0.91,'material_blocker':0.03,'uncertain':0.06,
                    },
                },
                'scene_1':{
                    'type':'choice','choice':'minor_polish','confidence':0.65,
                    'probabilities':{
                        'minor_polish':0.90,'material_blocker':0.03,'uncertain':0.07,
                    },
                },
            },
        })

    decisions=classify_review_findings([
        {'scene_index':3,'issues':['A small decorative motion accent could be smoother.']},
        {'scene_index':5,'issues':['This visual treatment is optional polish.']},
    ],'test-openrouter-key',post=post)

    assert captured['url']==DEFAULT_ENDPOINT
    assert captured['headers']['Authorization']=='Bearer test-openrouter-key'
    assert captured['json']['model']==DEFAULT_MODEL
    assert list(captured['json']['questions'])==['scene_0','scene_1']
    assert decisions[3]['eligible_minor_polish'] is True
    assert decisions[3]['decision_id']=='decision-1'
    assert decisions[5]['eligible_minor_polish'] is False


def test_jev_timeout_or_malformed_output_does_not_authorize_a_fallback():
    def raises(*_args, **_kwargs):
        raise TimeoutError('provider timeout')

    assert classify_review_findings([{'scene_index':1,'issues':['minor polish']}],
                                    'test-key',post=raises)=={}
    assert classify_review_findings([{'scene_index':1,'issues':['minor polish']}],
                                    'test-key',post=lambda *_a,**_k:_Response({'answers':{}}))=={}


def test_jev_decision_is_only_used_for_cosmetic_findings(monkeypatch):
    import services.native_render_policy as policy

    from services import jev_decision_service
    monkeypatch.setattr(jev_decision_service, 'classify_review_findings', lambda rows, *_a, **_k: {
        int(rows[0]['scene_index']): {
            'choice':'minor_polish','eligible_minor_polish':True,
            'confidence':0.93,'probabilities':{'minor_polish':0.94,'material_blocker':0.02,'uncertain':0.04},
        },
    })
    creative={
        'status':'needs_revision','unresolved_scene_indices':[2],
        'scenes':[{'scene_index':2,'status':'needs_revision','repair_kind':'layout',
                   'issues':['A slightly uneven decorative timing treatment.']}],
    }
    manifest={'scenes':[{'narration':'The important fact is clearly visible.',
                         'visual_intent':'A restrained line reveal.','primary_visual':{'type':'Diagram'}}]}
    deferred=policy._defer_minor_creative_findings(
        creative, {'status':'pass'}, {'status':'pass'},
        manifest=manifest, openrouter_api_key='test-key',
    )
    assert len(deferred)==1
    assert creative['scenes'][0]['jev_triage']['eligible_minor_polish'] is True


def test_jev_cannot_override_known_material_blocker(monkeypatch):
    from services import jev_decision_service
    import services.native_render_policy as policy

    monkeypatch.setattr(jev_decision_service, 'classify_review_findings', lambda *_a, **_k: {
        2:{'choice':'minor_polish','eligible_minor_polish':True,'confidence':0.99,
           'probabilities':{'minor_polish':0.99,'material_blocker':0.0,'uncertain':0.01}},
    })
    creative={
        'status':'needs_revision','unresolved_scene_indices':[2],
        'scenes':[{'scene_index':2,'status':'needs_revision','repair_kind':'layout',
                   'issues':['The subtitle collision obscures the player.']}],
    }
    assert policy._defer_minor_creative_findings(
        creative, {'status':'pass'}, {'status':'pass'},
        manifest={'scenes':[{}]}, openrouter_api_key='test-key',
    )==[]
    assert creative['unresolved_scene_indices']==[2]


def test_jev_can_reorder_only_a_shortlisted_youtube_candidate():
    captured={}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response({
            'model':DEFAULT_MODEL,'id':'decision-yt',
            'answers':{'best_candidate':{
                'type':'choice','choice':'candidate_1','confidence':0.88,
                'probabilities':{'candidate_0':0.03,'candidate_1':0.88,'keep_ranked':0.09},
            }},
        })

    decision=choose_youtube_candidate(
        scene_subject='Jermaine Kearse Super Bowl XLIX catch',
        scene_narration='A four-bobble catch keeps Seattle alive.',
        candidates=[
            {'title':'Super Bowl XLIX full game','transcript':'Kickoff and opening drive'},
            {'title':'Jermaine Kearse amazing catch replay','transcript':'Kearse bobbles and secures the catch',
             'timestamp':122.3},
        ],
        api_key='test-key',post=post,
    )
    assert decision['selected_index']==1
    assert decision['confidence']==0.88
    assert captured['json']['questions']['best_candidate']['type']=='choice'
    assert captured['json']['state']['candidates'][1]['matched_transcript']=='Kearse bobbles and secures the catch'


def test_jev_low_confidence_or_keep_ranked_preserves_existing_youtube_order():
    candidates=[{'title':'one'}, {'title':'two'}]
    low=_Response({'answers':{'best_candidate':{
        'type':'choice','choice':'candidate_1','confidence':0.55,
        'probabilities':{'candidate_0':0.2,'candidate_1':0.6,'keep_ranked':0.2},
    }}})
    tied=_Response({'answers':{'best_candidate':{
        'type':'choice','choice':'keep_ranked','confidence':0.8,
        'probabilities':{'candidate_0':0.4,'candidate_1':0.4,'keep_ranked':0.2},
    }}})
    for response in (low,tied):
        assert choose_youtube_candidate(
            scene_subject='NFL catch',scene_narration='The catch completes.',
            candidates=candidates,api_key='test-key',post=lambda *_a,**_k:response,
        ) is None
