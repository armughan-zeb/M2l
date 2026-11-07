import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from services.editorial_director.director import _extract_numbers_from_text
from services.editorial_director.director import _normalized_emphasis_spans


def test_spoken_endpoint_is_one_amount_not_an_inner_hundred():
    rows=_extract_numbers_from_text('The account could grow to roughly one hundred twenty thousand dollars before taxes.')
    assert [row['raw'] for row in rows]==[120000]
    assert rows[0]['prefix']=='$'
    assert rows[0]['display']=='$120 THOUSAND'


def test_nested_scales_do_not_multiply_the_million_twice():
    rows=_extract_numbers_from_text('One million five hundred thousand dollars.')
    assert [row['raw'] for row in rows]==[1500000]
    assert rows[0]['display']=='$1.5 MILLION'


def test_separate_spoken_amounts_are_not_merged_across_punctuation():
    rows=_extract_numbers_from_text('One hundred dollars, then two thousand dollars.')
    assert [row['raw'] for row in rows]==[100,2000]
    assert rows[0]['display']=='$100'


def test_highlight_does_not_color_a_partial_word():
    assert _normalized_emphasis_spans('one hundred dollars',[{'start':0,'end':18}])==[]
    assert _normalized_emphasis_spans('one hundred dollars',[{'start':0,'end':19}])==[{'start':0,'end':19}]


def test_spoken_counts_reach_graphic_triggers_and_keep_their_units():
    from services.editorial_director.script_analyzer import analyze_sentence_triggers
    from services.editorial_director.director import _build_stat_card_props
    text = 'An NFL offense gets four downs to move the ball ten yards.'
    rows = _extract_numbers_from_text(text)
    assert [(r['raw'], r['unit']) for r in rows] == [(4, 'downs'), (10, 'yards')]
    assert 'four downs' in analyze_sentence_triggers(text).numbers
    props = _build_stat_card_props('floating_hero_counter', text, [], None)
    assert props['value'] == 4 and props['label'] == 'DOWNS'


def test_measurement_words_are_not_generic_prose_or_duplicate_zero():
    assert _extract_numbers_from_text('One small step. Two different ideas.') == []
    rows = _extract_numbers_from_text('Twenty-six seconds and zero goals.')
    assert [(r['raw'], r['unit']) for r in rows] == [(26, 'seconds'), (0, 'goals')]
    assert _extract_numbers_from_text('November 24, 1971 at 8:30 PM.') == []


def test_counted_scale_keeps_the_complete_count_and_measurement():
    rows = _extract_numbers_from_text('One hundred twenty passengers and two engines.')
    assert [(r['raw'], r['unit']) for r in rows] == [(120, 'passengers'), (2, 'engines')]
