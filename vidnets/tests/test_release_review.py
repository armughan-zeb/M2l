from services.editorial_director.release_review import release_review


def test_technical_pass_does_not_certify_creative_or_factual_quality():
    report=release_review({}, {'status':'pass'})
    assert report['ready_to_publish'] is False
    assert report['status']=='needs_review'


def test_contested_evidence_and_repeated_pixels_block_acceptance():
    report=release_review({'research_ledger':{'claims':[{'status':'contested'}]},
        'media_shot_plan':{'perceptual_repetition_audit':{'unresolved_repeats':[{'scene_index':1}]}}}, {'status':'pass'})
    assert report['status']=='needs_revision'
    assert len(report['blockers'])==2


def test_all_reviewed_tracks_can_be_accepted():
    report=release_review({'creative_review':{'status':'pass'},'research_ledger':{'status':'reviewed_source_support'}}, {'status':'pass'})
    assert report['ready_to_publish'] is True


def test_timeout_preserves_pending_media_findings_until_final_verification():
    creative = {'status': 'incomplete', 'rounds': [{'scenes': [
        {'scene_index': 2, 'status': 'needs_revision', 'media_repair_required': True},
        {'scene_index': 4, 'status': 'pass', 'media_repair_required': False},
    ]}]}
    report = release_review({'creative_review': creative}, {'status': 'pass'})
    assert any('scenes: 2' in finding for finding in report['pending'])
    assert report['ready_to_publish'] is False
    creative['status'] = 'pass'
    report = release_review({'creative_review': creative,
                             'research_ledger': {'status': 'reviewed_source_support'}}, {'status': 'pass'})
    assert report['ready_to_publish'] is True
