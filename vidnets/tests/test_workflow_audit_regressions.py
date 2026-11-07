from unittest.mock import patch

import pytest
import app
from services.visual_requirement import build_visual_requirement, candidate_requirement_match


@pytest.mark.parametrize('title', ['GTA VI Workflow Audit', 'GTA 6 news', 'Grand Theft Auto VI trailers'])
def test_gaming_display_title_is_not_identity(title):
    req = build_visual_requirement(title=title, script='Grand Theft Auto VI is a video game.', scene_text='Official trailer footage.')
    assert req['canonical_entity'] == 'Grand Theft Auto VI'
    for name in ['GTA 6 Official Trailer', 'GTA VI trailer', 'Grand Theft Auto 6 trailer']:
        assert candidate_requirement_match(req, {'title': name, 'provider': 'youtube'})[0]
    assert not candidate_requirement_match(req, {'title': 'GTA 4 gameplay', 'provider': 'youtube'})[0]
    assert app._resolve_topic_anchor_for_config(title, '', {'_topic_anchor': title}) == 'Grand Theft Auto VI'


def test_explicit_vision_enabled_initializes_context():
    with patch('services.visual_intelligence.context.VisualDecisionContext.from_config', return_value='context') as factory:
        assert app._build_web_visual_context({'visual_understanding': True, 'openrouter_api_key': 'test-key'}, 2) == 'context'
        assert factory.call_args.args[0]['openrouter_api_key'] == 'test-key'


def test_progress_does_not_label_failed_image_searches_ready():
    assert app._sanitize_user_progress_message(.4, 'Web image scene 4/8 checked (0 resolved)') == 'Finding scene 4/8'
    assert app._sanitize_user_progress_message(.76, 'Stock footage scene 3/8 retrying') == 'Finding scene 3/8'


def test_progress_reporter_does_not_regress_when_shadow_branch_reports_subset():
    updates = []
    report = app._make_progress_reporter(lambda fraction, message: updates.append((fraction, message)))
    report(0.54, 'Web images 5/6 checked (4 resolved)')
    report(0.54, 'Web images 2/6 ready')
    assert [message for _fraction, message in updates] == ['Finding scene 5/6']


def test_progress_keeps_caption_mux_in_finalizing_state_until_terminal_fraction():
    assert app._sanitize_user_progress_message(
        .965, 'Rendering video complete; preparing captions...'
    ) == 'Finalizing video'
    assert app._sanitize_user_progress_message(
        .985, 'Finalizing captions...'
    ) == 'Finalizing video'
    assert app._sanitize_user_progress_message(1.0, 'Video Ready') == 'Video Ready'


def test_tts_does_not_retry_payment_errors():
    from services import openrouter_tts_service as tts
    from unittest.mock import Mock
    response = Mock(status_code=402)
    response.json.return_value = {'error': {'message': 'Insufficient credits'}}
    with patch.object(tts.requests, 'post', return_value=response) as request:
        with pytest.raises(RuntimeError, match='HTTP 402'):
            tts._request_audio(model_id='fish-audio/s2.1-pro-free:free', script='Test.', voice='', api_key='test', response_format='mp3', options=None)
        assert request.call_count == 1


def test_brain_does_not_retry_payment_errors():
    from services import openrouter_llm as llm
    from unittest.mock import Mock
    with patch.object(llm.requests, 'post', return_value=Mock(status_code=402)) as request:
        with pytest.raises(ValueError, match='spending limit'):
            llm.OpenRouterLLM('test').chat('Test', max_attempts=3)
        assert request.call_count == 1


def test_cached_gaming_contract_gets_current_aliases():
    from services.visual_requirement import rebase_visual_requirement_for_scene
    req = rebase_visual_requirement_for_scene({'canonical_entity': 'GTA VI Workflow Audit', 'subject_type': 'video_game'})
    assert req['canonical_entity'] == 'Grand Theft Auto VI'
    assert candidate_requirement_match(req, {'title': 'GTA 6 trailer'})[0]


def test_verified_streamer_is_not_an_official_game_publisher():
    from services.youtube_pipeline_v2 import _is_trusted_official_channel
    context = {'domain_category': 'gaming', 'container_subject': 'Grand Theft Auto VI'}
    candidate = {'title': 'FIRST OFFICIAL GTA 6 GAMEPLAY IS HERE!', 'channel_is_verified': True}
    assert not _is_trusted_official_channel('Ujjwal', context, candidate)
    assert _is_trusted_official_channel('Rockstar Games', context, candidate)
