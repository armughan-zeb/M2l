from server.services.visual_intelligence.semantic_guard import explicit_domain_conflict


def test_simulator_mods_are_not_real_world_documentary_sources():
    assert explicit_domain_conflict('Tesla Supercharger stations', 'Tesla SuperCharger FS25 - KingMods') == 'simulation_for_real_world'
    assert not explicit_domain_conflict('Farming Simulator game mods', 'Tesla SuperCharger FS25 - KingMods')


def test_nfl_is_not_association_football():
    assert explicit_domain_conflict('NFL quarterback passing play', 'UEFA Champions League highlights') == 'different_sport'
    assert not explicit_domain_conflict('NFL quarterback passing play', 'Kansas City Chiefs training')
    assert not explicit_domain_conflict('NFL and soccer comparison', 'UEFA Champions League highlights')


def test_metaphorical_passing_window_does_not_authorize_literal_windows():
    url = 'https://sc04.alicdn.com/German-Standard-Aluminum-Tilt-and-Turn-Passive-Window-Quadruple-Pane.jpg'
    assert explicit_domain_conflict('NFL receiver opens a window in coverage', url) == 'literal_hardware_for_sports_metaphor'
    assert not explicit_domain_conflict('Architecture: tilt and turn window modes', url)
    assert not explicit_domain_conflict('NFL passing window', 'Receiver finds an open passing window')
