import pytest
from services.web_image.query import _is_person_fallback_subject

@pytest.mark.parametrize('subject',['Turbocharger','Solar Eclipse','GPS Clocks','Compound Returns','Octopus','Tetris','Torii Gates','Hurricane Katrina','Government Policy'])
def test_recognized_documentary_subject_families_never_use_people_fallback(subject):
    assert not _is_person_fallback_subject(subject)

def test_named_people_keep_person_media_queries():
    assert _is_person_fallback_subject('Lionel Messi')
    assert _is_person_fallback_subject('Prince Charles')
