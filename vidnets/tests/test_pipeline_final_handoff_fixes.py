import hashlib
import pytest


def test_physical_subsystem_is_not_a_generic_brand_exterior():
    from services.visual_intelligence.semantic_guard import explicit_visual_facet_conflict
    description = 'A red Tesla sedan driving along a highway.'
    assert explicit_visual_facet_conflict('Supercharger stations help drivers recharge.', description)
    assert explicit_visual_facet_conflict('Inside the car software lives on the central touchscreen.', description)
    assert not explicit_visual_facet_conflict('Vehicles, charging and energy storage.',
                                             'Tesla interior with a central touchscreen.')


def test_native_component_does_not_trigger_actorless_diagram_design():
    from services.editorial_director.director import _normalize_conflicting_frame_mode
    decision = {'type':'SmartVideo', 'frame_mode':'diagram', 'diagram_nodes':[]}
    assert _normalize_conflicting_frame_mode(decision, 'A car travels along a highway.')
    assert decision['frame_mode'] == 'media_only'
    genuine = {'type':'Diagram', 'frame_mode':'diagram', 'diagram_nodes':[]}
    assert _normalize_conflicting_frame_mode(genuine, 'A process connects two systems.')
    assert genuine['frame_mode'] == 'media_only'


def test_alternate_needs_its_own_exact_review_in_current_factual_jobs(tmp_path):
    from services.media_shot_plan import _candidate_rows
    primary, alternate = tmp_path/'primary.jpg', tmp_path/'alternate.jpg'
    primary.write_bytes(b'primary-approved')
    alternate.write_bytes(b'other-pixels')
    requirement = {'strict_identity':True, 'requirement_hash':'scope-a'}
    verdict = {'accepted':True, 'visual_checked':True, 'visual_available':True,
        'verified_asset_sha256':hashlib.sha256(primary.read_bytes()).hexdigest(),
        'verified_requirement_hash':'scope-a'}
    item = {'path':str(primary), 'media_type':'image', 'visual_requirement':requirement,
        'resolution':{'visual_requirement':requirement, 'visual_decision':verdict,
            'alternates':[{'path':str(alternate), 'verified':True}]}}
    assert len(_candidate_rows(item)) == 1
    own_review = dict(verdict, verified_asset_sha256=hashlib.sha256(alternate.read_bytes()).hexdigest())
    item['resolution']['alternates'][0]['visual_decision'] = own_review
    assert len(_candidate_rows(item)) == 2


def test_app_reads_dedicated_caption_placement():
    from pathlib import Path
    app = (Path(__file__).resolve().parents[1]/'server/app.py').read_text(encoding='utf-8')
    assert 'job_config.get("caption_position") or job_config.get("position")' in app


def test_brand_identity_cannot_borrow_a_different_reviewed_scene_facet():
    from services.media_shot_plan import _visual_identity_key
    def row(scope):
        return {'visual_requirement':{'canonical_entity':'Tesla', 'domain':'business', 'requirement_hash':scope},
                'visual_decision':{'verified_asset_sha256':'reviewed-bytes', 'verified_requirement_hash':scope}}
    assert _visual_identity_key(row('charging')) != _visual_identity_key(row('garage'))
    assert _visual_identity_key(row('charging')) == _visual_identity_key(row('charging'))


def test_opportunity_copy_keeps_grounding_and_cooldown():
    from services.editorial_director.director import _apply_opportunity_highlights
    rows = [{'scene_index':i,'frame_mode':'media_only'} for i in range(3)]
    texts = ['Tesla started with a Roadster.', 'A central touchscreen shows software.', 'Powerwall batteries store electricity.']
    proposals = [{'scene_index':0,'display_text':'Roadster','source_span':texts[0],'variant':'impact_slab','placement':'center'},
                 {'scene_index':1,'display_text':'central touchscreen','source_span':texts[1],'variant':'mask_slide_up','placement':'top_left'},
                 {'scene_index':2,'display_text':'invented claim','source_span':texts[2],'variant':'impact_slab','placement':'center'}]
    assert _apply_opportunity_highlights(rows, proposals, texts) == [0]
    assert rows[0]['visual_role'] == 'media_overlay'
    assert rows[0]['display_text'] == 'Roadster'
    assert rows[1].get('display_text') is None


def test_pixel_pass_does_not_bypass_material_media_review():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]/'server/services/native_render_policy.py').read_text(encoding='utf-8')
    assert "if (safe_advisories and review.get('pixel_audit', {}).get('status') == 'pass'" in source


def test_highlight_cannot_end_on_an_unfinished_transitive_verb():
    from services.editorial_director.director import _valid_director_copy
    narration = 'Larger Megapack installations bring battery storage to energy projects.'
    assert not _valid_director_copy(narration, 'Larger Megapack installations bring', narration)
    assert _valid_director_copy(narration, 'Megapack installations', narration)
    charging = 'Supercharger stations help drivers recharge between journeys.'
    assert not _valid_director_copy(charging, 'Supercharger stations help', charging)


def test_small_landscape_preview_keeps_requested_aspect_ratio():
    from services.semantic_edit_engine import build_semantic_edit_manifest
    manifest = build_semantic_edit_manifest(scene_texts=['A precise scene.'], scene_durations=[3],
        media=[{'type':'image','src':'example.jpg'}],
        config={'width':426,'height':240,'fps':30}, variation_seed=1)
    assert (manifest['width'], manifest['height']) == (426,240)


def test_export_copy_failure_keeps_previous_valid_result(monkeypatch, tmp_path):
    import app
    import shutil
    source, target = tmp_path/'new.mp4', tmp_path/'published.mp4'
    source.write_bytes(b'new-validated-content')
    target.write_bytes(b'previous-good-video')
    def failed_copy(src, dst):
        dst.write_bytes(b'partial')
        raise OSError('copy interrupted')
    monkeypatch.setattr(shutil, 'copy2', failed_copy)
    import pytest
    with pytest.raises(OSError):
        app._publish_validated_export(source, target)
    assert target.read_bytes() == b'previous-good-video'
    assert not list(tmp_path.glob('*.validated-*'))


def test_master_is_validated_before_publishing_download():
    import app
    import inspect
    source = inspect.getsource(app._render_video_with_semantic_editing)
    assert source.index('quality_checks = _validate_remotion_master(') < source.index('_publish_validated_export(')


def test_pixel_reviewer_keeps_exact_primary_shot_identity(tmp_path):
    from services.editorial_director.pixel_revision import _reviewed_media_identity
    source = tmp_path/'roadster.jpg'
    source.write_bytes(b'actual-roadster')
    verdict = {'scene_description':'A Tesla Roadster.', 'reason':'Verified Tesla vehicle.',
               'verified_requirement_hash':'tesla-scene',
               'verified_asset_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    asset = {'src':str(source), 'shots':[{'path':str(source), 'visual_decision':verdict}]}
    assert _reviewed_media_identity(asset)['scene_description'] == 'A Tesla Roadster.'
    source.write_bytes(b'changed-image')
    assert _reviewed_media_identity(asset) is None


def test_pixel_reviewer_cannot_borrow_alternate_identity(tmp_path):
    from services.editorial_director.pixel_revision import _reviewed_media_identity
    source, alternate = tmp_path/'primary.jpg', tmp_path/'alternate.jpg'
    source.write_bytes(b'primary')
    alternate.write_bytes(b'other')
    verdict = {'verified_requirement_hash':'tesla-scene',
               'verified_asset_sha256':hashlib.sha256(alternate.read_bytes()).hexdigest()}
    assert _reviewed_media_identity({'src':str(source),
        'shots':[{'path':str(alternate),'visual_decision':verdict}]}) is None


def test_kinetic_variants_share_subject_safe_box_and_scaled_padding():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    kinetic = (root/'src/remotion/components/KineticText.jsx').read_text(encoding='utf-8')
    assert 'const placementStyle = safeRegion ?' in kinetic
    assert 'withinWidth: copyWidth * .76' in kinetic
    editorial = (root/'src/remotion/components/EditorialType.jsx').read_text(encoding='utf-8')
    assert '${(compact ? 8 : 10) * scale}px' in editorial
    render = (root/'scripts/render-remotion-manifest.js').read_text(encoding='utf-8')
    assert 'const reviewScale = Math.min(1, Math.max(.5, 640 /' in render
    assert 'frame, scale: reviewScale' in render


@pytest.mark.parametrize('value', ['0.5', '20', 'inf', 'nan', '-1', 'invalid'])
def test_worker_drain_cannot_hold_a_short_retrieval_wall(monkeypatch, value):
    from services.web_image.resolver import _bounded_worker_drain_seconds
    monkeypatch.setenv('VIDZEO_WEB_WORKER_DRAIN_SECONDS', value)
    assert 0 <= _bounded_worker_drain_seconds(1) <= .1
    assert 0 <= _bounded_worker_drain_seconds(240) <= .5


def test_measured_caption_words_do_not_load_another_model(monkeypatch, tmp_path):
    from services import transcription_service as transcription
    monkeypatch.setattr(transcription, '_available', False)
    monkeypatch.setattr(transcription, '_whisper_model', None)
    monkeypatch.setattr(transcription, 'init_whisper', lambda: pytest.fail('Unnecessary model load'))
    source = tmp_path/'voice.mp3'
    result = transcription.generate_captions(str(source),
        precomputed_words=[{'text':'Measured', 'start':0.1, 'end':0.5},
                           {'text':'words.', 'start':0.5, 'end':1.0}])
    from pathlib import Path
    assert result and Path(result).is_file()
    assert 'measured' in Path(result).read_text(encoding='utf-8').casefold()


@pytest.mark.parametrize('narration,title', [
    ('Supercharger stations help drivers recharge.', 'Tesla Supercharger charging footage'),
    ('Powerwall batteries store electricity at home.', 'Tesla Powerwall battery installation footage'),
    ('Solar panels capture sunlight.', 'Tesla solar panels footage'),
])
def test_specific_footage_metadata_beats_generic_same_brand(narration,title):
    from services.youtube_pipeline_v2 import _score_candidate
    context={'domain_category':'technology'}
    def candidate(label):
        return {'title':label, 'description':'', 'channel':'Tesla', 'duration':300}
    assert _score_candidate(candidate(title),'Tesla',narration,'Tesla footage',context) > \
        _score_candidate(candidate('Tesla car exterior footage'),'Tesla',narration,'Tesla footage',context)


def test_constructed_vision_client_cannot_skip_live_health_check(monkeypatch, tmp_path):
    import time
    from types import SimpleNamespace
    from services.visual_intelligence import context as module
    from services.visual_intelligence.cache import VisualDecisionCache
    context=module.VisualDecisionContext(enabled=True, api_key='test-key', model='qwen/test',
        fallback_model='',scene_count=1,max_calls=4,budget_usd=.1,
        deadline_at=time.monotonic()+30,cache=VisualDecisionCache(str(tmp_path/'cache.db')))
    context._client=SimpleNamespace(_client=SimpleNamespace(model='qwen/test'))
    monkeypatch.setattr(module,'_maybe_probe_vision',lambda raw:(False,'DNS timeout'))
    assert context.get_client() is None
    assert context._client is None
    assert context.degraded_reason == ''  # Transport failure remains retryable.


def test_youtube_child_output_has_a_nonblocking_preserved_file_sink(monkeypatch, tmp_path):
    import app
    from types import SimpleNamespace
    def spawn(*args, **kwargs):
        assert kwargs['stdout'] is not None
        assert kwargs['stderr'] == app.subprocess.STDOUT
        kwargs['stdout'].write(b'provider progress and failure details\n')
        return SimpleNamespace()
    monkeypatch.setattr(app.subprocess,'Popen',spawn)
    future=app._YoutubeProcessFuture({'output_dir':str(tmp_path/'run'),
        'worker_log_dir':str(tmp_path/'package/logs')}, cancellation=None)
    assert future._log_path.is_file()
    assert 'provider progress' in future._log_path.read_text()


def test_recall_query_uses_visible_action_instead_of_sentence_opener():
    from services.media_query_strategy import enrich_scene_queries
    queries=enrich_scene_queries([],{'canonical_entity':'Kylian Mbappe'},
        'Few players can sprint like Kylian Mbappe in open space.',provider='web')
    assert 'sprint' in queries[0].casefold()
    assert 'few' not in ' '.join(queries).casefold()
    assert all('Kylian Mbappe' in q for q in queries)


@pytest.mark.parametrize('policy', ['visual_required', 'strict_identity'])
def test_youtube_quota_cannot_certify_unseen_pixels(tmp_path, policy):
    from services.pipeline_v2_media_adapter import _visual_requirement_issue
    source=tmp_path/'charging.mp4'
    source.write_bytes(b'charging-video')
    requirement={'requirement_hash':'charging-scene',
                 'visual_verification_required':policy == 'visual_required',
                 'strict_identity':policy == 'strict_identity'}
    scene={'text':'Supercharger stations help drivers recharge.','visual_requirement':requirement}
    verdict={'accepted':True,'identity_score':100,'action_score':100,
        'visual_checked':False,'visual_available':False,'deterministic_only':True}
    row={'provider':'youtube','status':'accepted','verification_status':'youtube_quota_upgrade',
         'requirement_hash':'charging-scene','selected_path':str(source),'visual_decision':verdict}
    assert _visual_requirement_issue(scene,row,None) == 'visual_verification_pending'
    verdict.update(visual_checked=True,visual_available=True,verified_requirement_hash='charging-scene',
        verified_asset_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    assert _visual_requirement_issue(scene,row,None) == ''
    verdict['entity_conflict']=True
    assert _visual_requirement_issue(scene,row,None) == 'visual_verdict_rejected'


def test_nearest_fallback_preserves_target_pixel_scope(tmp_path):
    from services.media_resolution_coordinator import SceneResolution, _review_bound_to_scene
    from services.pipeline_v2_media_adapter import _visual_requirement_issue
    source=tmp_path/'storage.jpg'
    source.write_bytes(b'actual-storage-image')
    requirement={'strict_identity':True,'requirement_hash':'megapack-scene'}
    scene={'text':'Megapack installations bring battery storage to energy projects.',
           'visual_requirement':requirement}
    row={'provider':'nearest_fallback','resolution_kind':'nearest_verified_fallback',
         'selected_path':str(source),'requirement_hash':'megapack-scene',
         'status':'accepted','verification_status':'verified'}
    assert _visual_requirement_issue(scene,row,None) == 'visual_verification_pending'
    verdict={'accepted':True,'visual_checked':True,'verified_requirement_hash':'charging-scene',
             'verified_asset_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    resolution=SceneResolution(scene_index=1,provider='web',selected_path=str(source),
                               visual_decision=verdict)
    assert not _review_bound_to_scene(scene,resolution)
    verdict['verified_requirement_hash']='megapack-scene'
    assert _review_bound_to_scene(scene,resolution)
    row['visual_decision']=verdict
    assert _visual_requirement_issue(scene,row,None) == ''
    source.write_bytes(b'different-image')
    assert not _review_bound_to_scene(scene,resolution)
    assert _visual_requirement_issue(scene,row,None) == 'visual_verification_pending'


def test_export_success_does_not_claim_incomplete_review_is_approved():
    from services.delivery_quality import delivery_quality_summary
    result=delivery_quality_summary({'creative_review':{'status':'incomplete',
        'llm_review_certified':False}})
    assert result['ready_to_publish'] is False
    assert result['quality_status'] == 'review_needed'
    assert result['warnings']
    approved={'quality_checks': {'status': 'pass'}, 'creative_review':{'status':'pass','llm_review_certified':True}}
    assert delivery_quality_summary(approved)['ready_to_publish'] is True
    assert delivery_quality_summary(approved,{'metrics':{'graphic_scene_duration_ratio':0,
        'graphic_coverage_target':.3}})['ready_to_publish'] is False


def test_binary_generation_response_exposes_review_status(tmp_path):
    import app
    video=tmp_path/'export.mp4'
    video.write_bytes(b'export-placeholder')
    with app.app.test_request_context('/api/generate',method='POST',json={'response_mode':'video'}):
        response=app._generation_response({'success':True,'video_path':str(video),
            'ready_to_publish':False,'quality_status':'review_needed'})
        assert response.headers['X-Vidnets-Ready-To-Publish'] == 'false'
        assert response.headers['X-Vidnets-Quality-Status'] == 'review_needed'
        response.close()


def test_cost_receipt_counts_openrouter_gateway_not_upstream_host(tmp_path):
    import json
    from services.openrouter_usage import write_job_receipt, _api_provider
    path=tmp_path/'usage.jsonl'
    path.write_text('\n'.join(json.dumps(row) for row in [
        {'attempt_id':'audit','provider':'Alibaba','billing':'openrouter_credits','reported_cost_usd':.02},
        {'attempt_id':'audit','provider':'external_agent','reported_cost_usd':None},
    ]),encoding='utf-8')
    write_job_receipt({'job_id':'test','attempt_id':'audit','usage_path':str(path)},'completed')
    receipt=json.loads((tmp_path/'cost-audit.json').read_text(encoding='utf-8'))
    assert receipt['openrouter_request_count'] == 1
    assert receipt['external_agent_request_count'] == 1
    assert receipt['provider_reported_usd'] == .02
    assert receipt['unpriced_generation_count'] == 1
    assert _api_provider({'provider':'Alibaba','api_provider':'openrouter'}) == 'openrouter'
