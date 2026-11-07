"""Cross-domain subject and temporal retrieval regressions, without providers."""
from types import SimpleNamespace
import pytest

from services.visual_requirement import build_visual_requirement, rebase_visual_requirement_for_scene, candidate_requirement_match
from services.media_query_strategy import enrich_scene_queries


@pytest.mark.parametrize('title,text,subject,kind', [
    ('How locomotives start', 'Rudolf Diesel, who patented his engine in 1898, changed transport.', 'Rudolf Diesel', 'person'),
    ('The history of computing', 'Grace Hopper developed the compiler.', 'Grace Hopper', 'person'),
    ('The royal household', 'St George’s Chapel hosts the service.', 'St George’s Chapel', 'place'),
    ('Inside the financial system', 'The Bank of England announced its decision.', 'Bank of England', 'organization'),
    ('How electric cars work', 'The battery pack stores electrical energy.', 'battery pack', 'component'),
    ('Life on the reef', 'Acropora cervicornis builds branching colonies.', 'Acropora cervicornis', 'species'),
    ('A history of vaccines', 'Katalin Karikó developed messenger RNA research.', 'Katalin Karikó', 'person'),
    ('French art', 'Claude Monet painted water lilies.', 'Claude Monet', 'person'),
    ('A season of cricket', 'Virat Kohli scored a century.', 'Virat Kohli', 'person'),
    ('How jet engines work', 'The combustion chamber heats compressed air.', 'combustion chamber', 'component'),
    ('Exploring Japan', '京都 is the destination in this scene.', '京都', 'place'),
    ('Video game development', 'Unreal Engine renders the game environment.', 'Unreal Engine', 'software'),
])
def test_grounded_local_subject_is_preserved_across_niches(title, text, subject, kind):
    context = {'visual_subject': subject, 'visual_subject_type': kind,
        'subject_evidence': text, 'visible_target': subject, 'identity_scope': 'subject'}
    requirement = build_visual_requirement(title=title, script=text, scene_text=text, semantic_context=context)
    assert requirement['canonical_entity'] == subject
    assert requirement['subject_context']['scene_local']
    assert requirement['subject_context']['origin'] == 'grounded_director'
    if kind not in {'component', 'species', 'software'}:
        assert requirement['subject_type'] == kind
    else:
        assert requirement['subject_type'] == 'named_entity'
    queries = enrich_scene_queries(['unrelated global subject photograph'], requirement, text, provider='web')
    assert queries and subject.casefold() in queries[0].casefold()
    assert not any('unrelated global' in query for query in queries)


def test_old_film_contract_allows_inventor_portrait_after_rebase():
    narration = 'The story begins with Rudolf Diesel, who patented his engine in 1898.'
    old = {'canonical_entity': 'Diesel Locomotive', 'subject_type': 'automobile', 'domain': 'automotive',
           'required_terms': ['automobile', '1898'], 'required_attributes': {'year': '1898'},
           'requirement_hash': 'old-incorrect-receipt'}
    repaired = rebase_visual_requirement_for_scene(old, title='Inside a Diesel Locomotive', scene_text=narration)
    assert repaired['canonical_entity'] == 'Rudolf Diesel'
    assert repaired['subject_type'] == 'person'
    assert 'year' not in repaired['required_attributes']
    assert repaired['subject_context']['narrated_year'] == '1898'
    assert candidate_requirement_match(repaired, {'title': 'Rudolf Diesel historical inventor portrait'})[0]
    assert not candidate_requirement_match(repaired, {'title': 'Thomas Edison historical inventor portrait'})[0]
    assert repaired['requirement_hash'] != old['requirement_hash']


@pytest.mark.parametrize('context', [
    {'visual_subject': 'Albert Einstein', 'visual_subject_type': 'person'},
    {'visual_subject': 'Rudolf Diesel', 'subject_evidence': 'a sentence absent from the narration'},
])
def test_ungrounded_director_fields_do_not_override_scene(context):
    req = build_visual_requirement(title='Diesel Locomotive', script='The engine starts.',
        scene_text='The engine starts.', semantic_context=context)
    assert req['canonical_entity'] == 'Diesel Locomotive'


def test_event_scope_keeps_event_year_when_local_subject_is_selected():
    text = 'Marie Curie received an award in 1911.'
    req = build_visual_requirement(title='The history of science', scene_text=text,
        semantic_context={'visual_subject': 'Marie Curie', 'visual_subject_type': 'person',
            'subject_evidence': text, 'identity_scope': 'event'})
    assert req['required_attributes']['year'] == '1911'


def test_local_identity_does_not_inherit_the_films_event_date():
    text = 'Katherine Johnson calculated the trajectories.'
    req = build_visual_requirement(title='Apollo 11 launch in 1969', script=text, scene_text=text,
        semantic_context={'visual_subject': 'Katherine Johnson', 'visual_subject_type': 'person',
                          'subject_evidence': text, 'identity_scope': 'subject'})
    assert req['canonical_entity'] == 'Katherine Johnson'
    assert 'year' not in req['required_attributes']


def test_pronoun_resolution_uses_the_immediate_validated_antecedent():
    req = build_visual_requirement(title='A history of science', scene_text='She continued the research.',
        semantic_context={'visual_subject': 'Marie Curie', 'visual_subject_type': 'person',
            'subject_evidence': 'She continued the research.',
            '_previous_scene_subject': 'Marie Curie', '_previous_scene_text': 'Marie Curie discovered radium.'})
    assert req['canonical_entity'] == 'Marie Curie'
    assert req['subject_context']['origin'] == 'grounded_antecedent'


def test_component_queries_retain_the_films_physical_context():
    text = 'The combustion chamber heats the air.'
    req = build_visual_requirement(title='Jet Engines', scene_text=text,
        semantic_context={'visual_subject': 'combustion chamber', 'visual_subject_type': 'component',
                          'subject_evidence': text})
    queries = enrich_scene_queries([], req, text, provider='youtube')
    assert 'Jet Engines' in queries[0] and 'combustion chamber' in queries[0]
    assert all('Jet Engines' in query for query in queries)


def test_ranged_moments_find_action_in_transcript_before_final_cut():
    from services.youtube_ranged_moments import prepare_ranged_moments
    from services.youtube_transcript_index import TranscriptDocument, TranscriptSegment
    seen = {}
    video = 'abcdefghijk'
    document = TranscriptDocument(video_id=video, status='ready', segments=[
        TranscriptSegment(0, 8, 'Welcome and subscribe', 'welcome and subscribe'),
        TranscriptSegment(120, 130, 'The locomotive starter cranks the diesel engine.',
            'the locomotive starter cranks the diesel engine', confidence=1),
    ])
    class Index:
        def fetch_many(self, ids, **kwargs):
            seen.update(ids=list(ids), **kwargs)
            return {video: document}
    rows = [{'scene_text': 'The locomotive starter cranks the diesel engine.',
             'candidate_subject': 'locomotive', 'pool_key': 'rail'}]
    candidate = {'id': video, 'duration': 300}
    moments, audit = prepare_ranged_moments(rows, [candidate], {'rail': [candidate]}, [5],
        index=Index(), deadline_seconds=3)
    assert seen['ids'] == [video]
    assert seen['deadline_seconds'] == 3
    assert moments[0][0]['start'] == 120
    assert not moments[0][0]['heuristic']
    assert audit['sources_available'] == audit['scenes_with_moments'] == 1


def test_missing_transcript_does_not_block_existing_retrieval_lanes():
    from services.youtube_ranged_moments import prepare_ranged_moments
    index = SimpleNamespace(fetch_many=lambda *_args, **_kwargs: {})
    moments, audit = prepare_ranged_moments([{'scene_text': 'Scene'}],
        [{'id': 'abcdefghijk'}], {}, [4], index=index)
    assert moments == {}
    assert audit['sources_available'] == 0


def test_image_shortlist_keeps_relevance_and_an_alternate_host_reserve():
    from services.web_image.resolver import _diverse_download_candidates
    rows = [(10 - i * .1, {'url': f'https://main.test/{i}'}) for i in range(20)]
    rows += [(9.7, {'url': 'https://equal.test/1'}), (1, {'url': 'https://weak.test/1'})]
    chosen = _diverse_download_candidates(rows, 4)
    assert chosen[0] == rows[0]
    assert rows[-2] in chosen
    assert rows[-1] not in chosen


def test_long_job_transcript_shortlist_is_bounded_and_spans_the_timeline():
    from services.youtube_ranged_moments import prepare_ranged_moments
    seen = []
    index = SimpleNamespace(fetch_many=lambda ids, **_kw: (seen.extend(ids) or {}))
    assignments = [{'id': f'{i:011d}'} for i in range(80)]
    prepare_ranged_moments([{} for _ in assignments], assignments, {}, [4] * 80, index=index)
    assert len(seen) == 32
    assert seen[0] == assignments[0]['id'] and seen[-1] == assignments[-1]['id']


def test_runtime_warnings_keep_categories_without_sensitive_provider_text():
    from services.yt_dlp_runtime import YouTubeRuntimeLog, runtime_receipt
    logger = YouTubeRuntimeLog()
    logger.warning('Sign in with cookies: https://example.invalid/?token=private-value')
    assert logger.categories == ['authentication']
    assert 'private-value' not in repr(vars(logger))
    assert runtime_receipt()['yt_dlp'] != 'missing'


def test_shared_youtube_pool_does_not_acquire_another_scenes_review(monkeypatch):
    from services import youtube_pipeline_v2 as yt
    rows = [{'scene_index': i, 'candidate_subject': 'topic', 'section_subject': 'topic', 'scene_text': f'beat{i}',
        'visual_requirement': {'canonical_entity': f'entity{i}', 'requirement_hash': f'hash{i}'}}
        for i in range(2)]
    source = {'id': 'abcdefghijk', 'title': 'topic film', 'duration': 300}
    monkeypatch.setattr(yt, '_score_candidate', lambda *_args: 100)
    monkeypatch.setattr(yt, 'candidate_requirement_match', lambda req, _c: (True, '', {'entity': req['canonical_entity']}))
    monkeypatch.setattr(yt, '_pick_timestamp', lambda _c, i, *_args: i * 100 + 10)
    result = yt._assign_candidates(rows, {yt._row_pool_key(rows[0]): [source]}, [4, 4], allow_same_source_reuse=True)
    assert [r['requirement_hash'] for r in result] == ['hash0', 'hash1']
    assert 'requirement_hash' not in source and 'candidate_evidence' not in source


def test_completed_batch_scene_is_delivered_before_cleanup_and_survives_later_error():
    import threading
    from services.pipeline_v2_media_adapter import LazyBatchLane
    finish = threading.Event()
    candidate = [{'path': 'verified-local-cut.mp4', 'requirement_hash': 'scene-0'}]
    def load():
        lane.publish_row(0, candidate)
        assert finish.wait(3)
        raise RuntimeError('Unrelated scene cleanup failed')
    lane = LazyBatchLane(load)
    try:
        assert lane({'scene_index': 0}, threading.Event()) == candidate
        assert lane._state == 'loading'
        assert lane.get_row(0) == candidate
    finally:
        finish.set()
    with lane._condition:
        lane._condition.wait_for(lambda: lane._state == 'failed', timeout=3)
    assert lane.get_row(0) == candidate
    assert lane({'scene_index': 0}, threading.Event()) == candidate


def test_discovery_copy_does_not_probe_or_transcode_candidates(tmp_path, monkeypatch):
    import app
    source = tmp_path / 'candidate.mp4'
    source.write_bytes(b'unselected-candidate-bytes' * 200)
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Discovery cannot wait for render codec work')
    monkeypatch.setattr(app.subprocess, 'run', forbidden)
    copied = app._snapshot_media_for_render([str(source)], root_dir=tmp_path / 'run',
        normalize_videos=False)
    assert copied[0] != str(source)
    assert __import__('pathlib').Path(copied[0]).read_bytes() == source.read_bytes()


@pytest.mark.parametrize('subject,kind', [('Marie Curie', 'person'), ('京都', 'place'),
    ('Acropora cervicornis', 'species'), ('Unreal Engine', 'software'), ('combustion chamber', 'component')])
def test_moving_clip_policy_cannot_override_a_grounded_subject(subject, kind):
    from services.pipeline_v2_media_adapter import _score_verdict
    from services.media_resolution_coordinator import _youtube_relaxed_visual_gate
    text = f'{subject} is the focus of this scene.'
    requirement = build_visual_requirement(title='A documentary', scene_text=text,
        semantic_context={'visual_subject': subject, 'visual_subject_type': kind,
                          'subject_evidence': text, 'identity_scope': 'subject'})
    scene = {'text': text, 'visual_requirement': requirement,
             'source_policy': {'youtube_relaxed_visual_gate': True}}
    assert not _youtube_relaxed_visual_gate(scene)
    verdict = {'accepted': True, 'identity_score': 95, 'action_score': 90, 'context_score': 90,
        'quality': 90, 'final_score': 95, 'entity_conflict': True, 'fatal_mismatch': True,
        'reason': 'wrong entity: a different subject is visible', 'domain_match': True,
        'scene_match': False, 'visual_checked': True, 'visual_available': True}
    assert not _score_verdict(scene, verdict)['accepted']


def test_grounded_component_direction_accepts_supported_inflection_and_word_order():
    text = 'Furthermore, those massive clutches would wear out far too quickly to be useful in daily service.'
    context = {'visual_subject': 'clutch wear', 'visual_subject_type': 'component',
               'subject_evidence': 'those massive clutches would wear out far too quickly to be useful in daily service',
               'identity_scope': 'context', 'visible_target': 'worn clutch plates'}
    req = build_visual_requirement(title='Diesel Locomotive', scene_text=text, semantic_context=context)
    assert req['canonical_entity'] == 'clutch wear'
    assert req['subject_context']['scene_local']
    assert 'clutch wear' in enrich_scene_queries([], req, text, provider='web')[0]
    queries = enrich_scene_queries(['Diesel Locomotive cab clutch wear photo'], req, text, provider='web')
    assert 'worn clutch plates' in queries[0]
    assert 'Diesel Locomotive' not in queries[0]
    context['visual_subject'] = 'quantum hydraulic clutch wear'
    bad = build_visual_requirement(title='Diesel Locomotive', scene_text=text, semantic_context=context)
    assert bad['canonical_entity'] != context['visual_subject']


def test_low_pixel_match_cannot_be_promoted_by_clip_quota_or_relaxed_policy():
    from services.media_resolution_coordinator import _verification_result, _quota_gate_is_hard_rejection
    from services.pipeline_v2_media_adapter import _score_verdict
    verdict = {'accepted': False, 'identity_score': 0., 'context_score': 40., 'action_score': 0.,
        'final_score': 10., 'entity_conflict': False, 'fatal_mismatch': False,
        'scene_match': False, 'domain_match': True, 'visual_checked': True, 'visual_available': True,
        'scene_description': 'Rail yards and industrial smokestacks.',
        'reason': 'The scene subject is absent.', 'rejection_reason': 'scene_subject_or_facet_not_visible'}
    scene = {'text': 'Those clutches would wear out.', 'source_policy': {
        'youtube_relaxed_visual_gate': True, 'youtube_quota': True}}
    assert not _score_verdict(scene, verdict)['accepted']
    accepted, reason, _, decision = _verification_result(scene,
        {'source': 'youtube', 'media_type': 'video', 'overlay_clean': True},
        lambda *_args: dict(verdict))
    assert not accepted
    assert reason == 'insufficient_visible_subject_match'
    decision['spurious_mismatch'] = True
    assert _quota_gate_is_hard_rejection(reason, decision)
    # A saved soft-pass receipt must also fail the final handoff, before render.
    from services.pipeline_v2_media_adapter import _visual_requirement_issue
    decision.update(accepted=True, status='soft_visual_pass')
    scene['visual_requirement'] = {'canonical_entity': 'Diesel Locomotive'}
    assert _visual_requirement_issue(scene, {'visual_decision': decision}, None, 'YouTube Clips') == reason


def test_disk_recovery_does_not_fabricate_a_scene_bound_pixel_review(tmp_path):
    from services.pipeline_v2_media_adapter import _youtube_transport_review, VISUAL_REVIEW_POLICY_VERSION
    from services.visual_intelligence.decision import asset_fingerprint
    path = tmp_path / 'cut.mp4'
    path.write_bytes(b'exact-recovered-cut')
    requirement = {'requirement_hash': 'scene-1'}
    recovered = {'accepted': True, 'semantic_verified': True, 'visual_checked': True,
        'visual_available': True, 'identity_score': 100, 'action_score': 100, 'final_score': 75,
        'verified_requirement_hash': '', 'verified_asset_sha256': asset_fingerprint(str(path)),
        'visual_review_policy_version': 17, 'recovery_kind': 'disk_verified_pool_cut'}
    pending = _youtube_transport_review(recovered, requirement, str(path))
    assert pending['status'] == 'visual_verification_pending'
    assert not pending['accepted'] and not pending['semantic_verified']
    assert 'identity_score' not in pending and 'verified_requirement_hash' not in pending
    reviewed = {**recovered, 'verified_requirement_hash': 'scene-1',
                'visual_review_policy_version': VISUAL_REVIEW_POLICY_VERSION}
    assert _youtube_transport_review(reviewed, requirement, str(path)) == reviewed
    path.write_bytes(b'changed-recovered-cut')
    assert not _youtube_transport_review(reviewed, requirement, str(path))['accepted']


def test_web_recovery_also_rejects_an_explicit_low_pixel_match():
    from services.media_resolution_coordinator import _verification_result
    accepted, reason, _, _ = _verification_result({}, {'source': 'web'}, lambda *_args: {
        'accepted': False, 'identity_score': 0, 'context_score': 40, 'final_score': 10,
        'visual_available': True, 'visual_checked': True, 'entity_conflict': False})
    assert not accepted and reason == 'insufficient_visible_subject_match'


def test_selected_non_qwen_vision_model_disables_reasoning_by_default(monkeypatch):
    from services.visual_intelligence.vision_client import QwenVisionClient, VisionClientError
    monkeypatch.delenv('VIDZEO_QWEN_VISION_THINKING', raising=False)
    client = QwenVisionClient('test-key', model='nvidia/nemotron-3.5-super-vl:free', fallback_models=[], max_attempts=1)
    payloads = []
    def post(_headers, payload):
        payloads.append(dict(payload))
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
            json=lambda: {'choices': [{'message': {'content': '{"accepted":true}'}}]})
    monkeypatch.setattr(client, '_post_with_hard_timeout', post)
    assert client._post([{'type': 'text', 'text': 'Inspect pixels'}], json_mode=True) == '{"accepted":true}'
    assert payloads[0]['model'] == 'nvidia/nemotron-3.5-super-vl:free'
    assert payloads[0]['reasoning'] == {'enabled': False}
    def reasoning_only(_headers, payload):
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
            json=lambda: {'choices': [{'message': {'content': '', 'reasoning': '{"accepted":true}'}}]})
    monkeypatch.setattr(client, '_post_with_hard_timeout', reasoning_only)
    with pytest.raises(VisionClientError, match='empty content'):
        client._post([{'type': 'text', 'text': 'Inspect pixels'}], json_mode=True)


def test_unusable_composition_box_does_not_reject_a_complete_pixel_review():
    from services.pipeline_v2_media_adapter import _validate_qwen_verdict_schema
    result = {'candidate_id': 'clip1', 'entity_conflict': False, 'identity_score': 90,
        'action_score': 80, 'context_score': 90, 'quality': 85, 'final_score': 88,
        'visible_overlays': [], 'safe_text_regions': [], 'subject_box': {'left': 50, 'top': 50}}
    valid, checked, errors = _validate_qwen_verdict_schema({'candidate_id': 'clip1'}, result)
    assert valid and not errors
    assert checked['subject_box'] == {}
    assert checked['identity_score'] == 90
    assert checked['geometry_advisories'] == ['subject_box_discarded_invalid']


@pytest.mark.parametrize('subject,target,query', [
    ('large clutch', 'oversized clutch mechanism beside locomotive frame outline',
     'large industrial clutch assembly photograph'),
    ('diesel engine', 'diesel engine connected to generator equipment',
     'diesel locomotive engine generator coupling'),
])
def test_contextual_component_keeps_the_concrete_model_query(subject, target, query):
    text = f'The {subject} supports this mechanism.'
    req = build_visual_requirement(title='Inside the Cab of a Diesel Locomotive', scene_text=text,
        semantic_context={'visual_subject': subject, 'visual_subject_type': 'component',
                          'subject_evidence': text, 'visible_target': target, 'identity_scope': 'context'})
    # The adapter can prepend its exact identity before the model phrase.
    rows = enrich_scene_queries([f'{subject} {query}'], req, text, provider='web')
    assert rows[0] == query


@pytest.mark.parametrize('defect', ['missing_binding', 'wrong_scene', 'changed_bytes', 'old_policy', 'none'])
def test_old_branch_acceptance_cannot_skip_current_scene_review(tmp_path, defect):
    import hashlib
    from services.media_resolution_coordinator import _verification_result
    from services.pipeline_v2_media_adapter import VISUAL_REVIEW_POLICY_VERSION
    path = tmp_path / 'cut.mp4'
    path.write_bytes(b'current-clip-bytes')
    old = {'accepted': True, 'status': 'accepted', 'semantic_verified': True,
        'visual_checked': True, 'visual_available': True, 'final_score': 90,
        'verified_requirement_hash': 'current-scene', 'verification_status': 'verified',
        'verified_asset_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'visual_review_policy_version': VISUAL_REVIEW_POLICY_VERSION}
    if defect == 'missing_binding': old.pop('verified_requirement_hash')
    elif defect == 'wrong_scene': old['verified_requirement_hash'] = 'another-scene'
    elif defect == 'changed_bytes': path.write_bytes(b'changed-clip-bytes')
    elif defect == 'old_policy': old['visual_review_policy_version'] = 17
    calls = []
    def verify(*_args):
        calls.append(True)
        return {'accepted': True, 'identity_score': 90, 'context_score': 90,
            'action_score': 80, 'final_score': 90, 'visual_checked': True,
            'visual_available': True, 'semantic_verified': True}
    _verification_result({'visual_requirement': {'requirement_hash': 'current-scene'}},
        {'source': 'youtube', 'path': str(path), 'qwen_result': old}, verify)
    assert calls == ([] if defect == 'none' else [True])


@pytest.mark.parametrize('title,script,expected', [
    ('Why Did the Titanic Sink', 'The Titanic struck an iceberg in the North Atlantic.', 'Titanic'),
    ('Why the Titanic Sank', 'The Titanic struck an iceberg in the North Atlantic.', 'Titanic'),
    ('How did Rome fall', 'Rome collapsed after centuries of barbarian invasions.', 'Rome'),
    ('What Happened to Flight 370', 'Flight 370 disappeared over the southern Indian Ocean.', 'Flight 370'),
    ('What Really Happened to the Titanic', 'The Titanic set sail from Southampton on its maiden voyage.', 'Titanic'),
    ('The Mystery of the Mary Celeste', 'The Mary Celeste was discovered derelict and abandoned.', 'Mary Celeste'),
    ('The Tragic Story of the RMS Lusitania', 'The Lusitania was sunk by a German U-boat in 1915.', 'RMS Lusitania'),
    ('The Rise and Fall of the Roman Empire', 'The Roman Empire commanded legions across the Mediterranean.', 'Roman Empire'),
    ('The Downfall of Napoleon', 'Napoleon marched his army into Russia before Waterloo.', 'Napoleon'),
    ('Inside the Gardner Museum Heist', 'Thieves entered the Gardner Museum in Boston in 1990.', 'Gardner Museum'),
    ('Concorde - Why the Supersonic Airliner Failed', 'Concorde was retired following high operating costs.', 'Concorde'),
    ('Titanic: The Sinking of the Unsinkable Ship', 'The Titanic sank on April 15, 1912.', 'Titanic'),
    ('The Sinking of the Titanic', 'The Titanic was an ocean liner that sank in 1912.', 'Titanic'),
    ('The Fall of the Berlin Wall', 'The Berlin Wall divided the city for decades before it fell.', 'Berlin Wall'),
    ('The Destruction of Pompeii', 'Pompeii was buried under volcanic ash from Mount Vesuvius.', 'Pompeii'),
    ('The Day the Titanic Sank', 'The Titanic struck an iceberg late on April 14.', 'Titanic'),
    ('When Rome Fell', 'Rome was sacked by the Visigoths in 410 AD.', 'Rome'),
    ('The Tacoma Narrows Bridge Collapsed in 1940', 'The Tacoma Narrows Bridge twisted violent in strong winds.', 'Tacoma Narrows Bridge'),
    ('The Chernobyl Reactor Exploded in 1986', 'The Chernobyl Reactor exploded during a safety test.', 'Chernobyl Reactor'),
    ('Apollo 11 Landed on the Moon', 'Apollo 11 made history when humans walked on lunar soil.', 'Apollo 11'),
])
def test_documentary_title_hook_subjects_across_narrative_forms(title, script, expected):
    from services.documentary_subject import title_hook_subject
    assert title_hook_subject(title, script) == expected


def test_canonical_container_subject_handles_documentary_titles():
    from services.container_guard import canonical_container_subject
    assert canonical_container_subject('The Tacoma Narrows Bridge Collapsed in 1940') == 'Tacoma Narrows Bridge'
    assert canonical_container_subject('The Sinking of the Titanic') == 'Titanic'
    assert canonical_container_subject('The Fall of the Berlin Wall') == 'Berlin Wall'
    assert canonical_container_subject('Why the Concorde Crashed in 2000') == 'Concorde'
    assert canonical_container_subject('The Chernobyl Reactor Exploded in 1986') == 'Chernobyl Reactor'


def test_history_query_suffixes_do_not_inject_museum_or_artifact():
    import app
    # Test query suffix repair
    rows = [{'primary_query': 'RMS Titanic', 'query_variants': ['RMS Titanic', 'RMS Titanic', 'RMS Titanic']}]
    repaired = app._repair_anchor_preserving_query_rows(rows, 'RMS Titanic', ['The ship sailed through icy waters.'])
    for row in repaired:
        for q in row.get('query_variants', []):
            assert 'museum' not in q.lower(), f"Unexpected museum in query: {q}"
            assert 'artifact' not in q.lower(), f"Unexpected artifact in query: {q}"
        assert 'museum' not in str(row.get('youtube_query', '')).lower()
        assert 'artifact' not in str(row.get('youtube_query', '')).lower()

