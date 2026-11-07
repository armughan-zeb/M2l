import copy
import json
import time
import pytest

from services.editorial_director.review_context import editorial_review_brief, narration_opportunities, review_budget
from services.editorial_director.pixel_revision import review_and_revise, _suppress_grounded_transition_false_positives, apply_visual_patch
from services.native_render_policy import _run_bounded_media_repair


def test_day_month_year_is_one_date_and_not_a_counter_opportunity():
    text = "She became queen on 6 February 1952, aged 25."
    spans = narration_opportunities(text)
    assert [row['source_span'] for row in spans if row['kind'] == 'date'] == ['6 February 1952']
    assert [row['source_span'] for row in spans if row['kind'] == 'measurement'] == ['25']


def scene():
    return {'narration': 'On November 24, 1971, he demanded $200,000.',
            'from': 0, 'duration': 4, 'duration_in_frames': 120,
            'primary_visual': {'type': 'StatCard', 'variant': 'hero_counter_large',
                               'props': {'value': 200000}},
            'text': {'frame_mode': 'data'}}


def test_review_brief_uses_actual_niche_and_neighboring_choices():
    rows = [scene(), scene()]
    brief = editorial_review_brief({'category': 'general', 'editing_profile': {'niche': 'crime'}}, rows, 1)
    assert brief['niche'] == 'crime'
    assert brief['adjacent_matching_treatments'] == [0]
    assert brief['neighboring_edits'][0]['narration'] == rows[0]['narration']
    assert brief['visual_grammar']


def test_opportunities_keep_currency_and_full_date_without_counting_date_parts():
    spans = narration_opportunities(scene()['narration'])
    assert [(row['kind'], row['source_span']) for row in spans] == [
        ('date', 'November 24, 1971'), ('measurement', '$200,000')]
    assert all(scene()['narration'][row['start']:row['end']] == row['source_span'] for row in spans)


def test_assessment_pass_cannot_apply_or_design_another_patch():
    film = {'fps': 30, 'scenes': [scene()]}
    before = copy.deepcopy(film)
    class Client:
        def review_editorial_frames(self, paths, context):
            return {'status': 'needs_revision', 'confidence': 90, 'issues': ['Unreadable value'],
                    'patch': {'variant': 'impact_slab'}}
    result = review_and_revise(film, [{'frame': 40, 'path': 'actual.png'}], lambda _: Client(), assessment_only=True)
    assert film == before
    assert result['changed_scene_indices'] == []
    assert result['unresolved_scene_indices'] == [0]
    assert result['llm_review_certified'] is False


def test_measured_text_overflow_cannot_be_passed_by_vision():
    seen = []
    class Client:
        def review_editorial_frames(self, paths, context):
            seen.append(json.loads(context))
            return {'status': 'pass', 'confidence': 90, 'issues': []}
    layout = [{'frame': 40, 'failures': [{'text': '$200,000', 'overflow': True}],
               'measurements': [{'text': '$200,000', 'width': 110}]}]
    result = review_and_revise({'fps': 30, 'scenes': [scene()]}, [{'frame': 40, 'path': 'actual.png'}],
                              lambda _: Client(), assessment_only=True, layout_audit=layout)
    assert result['status'] == 'needs_revision'
    assert seen[0]['rendered_text_layout'] == layout
    assert any('Measured text layout' in issue for issue in result['scenes'][0]['issues'])


def test_intended_compact_copy_cannot_hide_observed_huge_backing():
    row = {'primary_visual': {'type': 'SmartImage'},
           'text': {'position': 'top_left', 'display_text': 'Dan Cooper'}}
    issue = 'Full-width background exceeds frame bounds.'
    assert _suppress_grounded_transition_false_positives(row, [issue]) == [issue]


def test_timed_out_media_callback_cannot_mutate_the_export_plan():
    film = {'scenes': [scene()]}
    before = copy.deepcopy(film)
    def repair(candidate, findings, budget):
        time.sleep(.18)
        candidate['scenes'][0]['narration'] = 'Late mutation'
        return True
    assert not _run_bounded_media_repair(repair, film, [{'scene_index': 0}], .1)
    time.sleep(.15)
    assert film == before


def test_successful_media_repair_commits_only_after_completion():
    film = {'scenes': [scene()]}
    def repair(candidate, findings, budget):
        candidate['scenes'][0]['media'] = {'src': 'verified.jpg'}
        return True
    assert _run_bounded_media_repair(repair, film, [{'scene_index': 0}], .2)
    assert film['scenes'][0]['media']['src'] == 'verified.jpg'


def test_review_budget_is_bounded_and_configurable():
    assert review_budget({'creative_review_budget_seconds': 9999, 'creative_revision_rounds': 100}, 300, 900)['total_seconds'] == 300
    assert review_budget({'creative_review_budget_seconds': 60, 'creative_revision_rounds': 1}, 3, 9)['revision_rounds'] == 1
    assert review_budget({'creative_review_budget_seconds': 'invalid', 'creative_revision_rounds': float('inf')}, 1, 4)['revision_rounds'] == 2


def test_empty_film_cannot_claim_creative_acceptance():
    assert review_and_revise({'scenes': []}, [], lambda _: None)['llm_review_certified'] is False


def test_revision_without_findings_is_not_accepted():
    class Client:
        def review_editorial_frames(self, *args):
            return {'status': 'needs_revision', 'confidence': 90, 'issues': [], 'patch': {'variant': 'impact_slab'}}
    result = review_and_revise({'scenes': [scene()]}, [{'frame': 40, 'path': 'actual.png'}], lambda _: Client())
    assert result['status'] == 'incomplete'
    assert result['scenes'][0]['reason'] == 'Revision verdict needs an observed finding'


@pytest.mark.parametrize('component,kind', [('Chart', 'chart'), ('Comparison', 'comparison'), ('Timeline', 'timeline')])
def test_supported_specialist_redesign_reaches_its_native_recipe(component, kind):
    row = scene()
    row['narration'] = 'London had 20 flights. Paris had 30 flights.'
    row['primary_visual'] = {'type': 'SmartImage', 'asset': {'src': 'verified.jpg'}}
    recipe = {'version': 2, 'id': 'grounded-review', 'name': 'Grounded review', 'layers': [
        {'kind': kind, 'x': 8, 'y': 12, 'width': 84, 'height': 55,
         'data': [{'label': 'London', 'value': 20}, {'label': 'Paris', 'value': 30}]}]}
    revised = apply_visual_patch(row, {'component_type': component, 'visual_role': 'primary_explanation', 'template_recipe': recipe},
                                 allowed_components=[component])
    assert revised['primary_visual']['type'] == component
    assert revised['template_recipe']['layers'][0]['kind'] == kind
    recipe['layers'][0]['data'][0]['value'] = 999
    with pytest.raises(ValueError, match='unsupported'):
        apply_visual_patch(row, {'component_type': component, 'template_recipe': recipe}, allowed_components=[component])
