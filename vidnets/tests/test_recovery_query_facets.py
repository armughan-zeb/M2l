from server.services.media_query_strategy import recovery_web_query_pack
from server.services.visual_requirement import build_visual_requirement


def test_terminal_rescue_keeps_director_touchscreen_query_first():
    narration = 'Inside the car, software lives on the central touchscreen.'
    req = build_visual_requirement(title='Tesla', script=narration, scene_text=narration)
    pack = recovery_web_query_pack({'_llm': True,
        'search_query': 'Tesla Model 3 central touchscreen interior photo',
        'supporting_search_query': 'Tesla infotainment software interface close up'}, req, narration)
    assert pack['search_query'] == 'Tesla Model 3 central touchscreen interior photo'
    assert pack['web_query_variants'][0] == pack['search_query']
    assert pack['director_web_query'] is True


def test_home_battery_and_charger_are_not_vehicle_requirements():
    for text in ('At home, Powerwall batteries store electricity.',
                 'Supercharger stations help drivers recharge between journeys.'):
        req = build_visual_requirement(title='Tesla', script='Tesla makes electric cars. '+text,
                                      scene_text=text, semantic_context={'domain_category': 'automotive'})
        assert req['subject_type'] == 'organization'
        assert 'automobile' not in req['required_terms']


def test_rescue_queries_remain_anchored_and_concrete_without_model():
    text = 'Inside the car, software lives on the central touchscreen.'
    req = build_visual_requirement(title='Tesla', script=text, scene_text=text)
    pack = recovery_web_query_pack({}, req, text)
    assert pack['search_query'].startswith('Tesla ')
    assert 'touchscreen' in pack['search_query']


def test_rescue_does_not_promote_unmarked_legacy_rewrite():
    text = 'The dashboard displays the software interface.'
    req = build_visual_requirement(title='Tesla', script=text, scene_text=text)
    pack = recovery_web_query_pack({'search_query': 'Elon Musk portrait'}, req, text)
    assert 'portrait' not in pack['search_query']


def test_institutional_alias_queries_preserve_concrete_brand_beat():
    from server.services.media_query_strategy import enrich_scene_queries
    req = build_visual_requirement(title='Royal Warrant System', scene_text='Cadbury supplies chocolate.')
    queries = enrich_scene_queries(['Royal Warrant certificate', 'Royal Warrant seal', 'Royal Warrant holders'],
                                  req, 'Cadbury supplies chocolate.', provider='web')
    assert 'Cadbury' in queries[0] and 'Royal Warrant' in queries[0]
    assert 'Royal Warrant certificate' in queries


def test_director_company_focus_must_be_explicitly_in_this_narration():
    from server.services.media_query_strategy import grounded_institutional_focus
    assert grounded_institutional_focus({'_llm': True, 'section_subject': 'Aston Martin'},
                                       'He owns an Aston Martin modified for bioethanol.') == 'Aston Martin'
    assert grounded_institutional_focus({'_llm': True, 'section_subject': "Burberry's"},
                                       "Burberry's stock was burned.") == 'Burberry'
    assert grounded_institutional_focus({'_llm': True, 'section_subject': 'Cathedral City and Cadbury'},
                                       'Firms like Cathedral City and Cadbury supply food.') == 'Cathedral City'
    for subject in ('Royal Warrant Holder List', 'Companies', 'Bureaucratic Machinery', 'Unmentioned Brand', 'Commerce'):
        assert not grounded_institutional_focus({'_llm': True, 'section_subject': subject}, 'Companies hold the Royal Warrant.')
    assert not grounded_institutional_focus({'_llm': True, 'section_subject': 'Commerce'}, 'A living contract binds commerce to the Crown.')
    assert not grounded_institutional_focus({'section_subject': 'Cadbury'}, 'Cadbury supplies chocolate.')


def test_business_warrants_do_not_retrieve_military_pension_documents():
    from server.services.visual_intelligence.semantic_guard import explicit_domain_conflict
    story = 'Royal Warrant holders supply goods as commercial brands.'
    assert explicit_domain_conflict(story, 'WW2 Ministry Of Pensions Royal Warrant booklet') == 'military_pension_warrant_for_commercial_appointment'
    assert not explicit_domain_conflict(story, 'Royal Warrant by appointment bookbinder certificate')
    assert not explicit_domain_conflict('The Ministry of Pensions Royal Warrant established retired pay.',
                                        'Ministry of Pensions Royal Warrant booklet')


def test_camera_directions_cannot_become_locked_scene_subjects():
    import server.app as app
    assert app._clean_scene_brain_subject('Close-up of Tesla', 'Tesla') == 'Tesla'
    assert app._clean_scene_brain_subject('Split screen showing cars', 'Tesla') == 'Tesla'
    assert app._clean_scene_brain_subject('Tesla Powerwall', 'Tesla') == 'Tesla Powerwall'


def test_bnsf_cab_scene_queries_keep_viewpoint_and_drop_negated_lineside():
    """The BNSF cab-in-whiteout scene: narration says "shown from the cab,
    not from the lineside". Queries must carry the viewpoint (from the cab)
    plus the operator and weather term (BNSF, whiteout), and must never
    carry the negated viewpoint (lineside) as a positive facet."""
    from server.services.media_query_strategy import enrich_scene_queries
    title = "A BNSF Whiteout Crossing, Seen From the Driver's Cab"
    narration = "It is shown from the cab, not from the lineside."
    req = build_visual_requirement(
        title=title,
        script="A BNSF freight train crosses a whiteout at a grade crossing. " + narration,
        scene_text=narration,
    )
    queries = enrich_scene_queries(
        ["train snow", "freight train snow footage", "BNSF train snow"],
        req, narration, provider='youtube',
    )
    first = queries[0]
    assert "BNSF" in first
    assert "whiteout" in first.casefold()
    assert "from the cab" in first.casefold()
    assert "train snow" != first
    assert "lineside" not in " ".join(queries).casefold()
    assert "not lineside" not in " ".join(queries).casefold()

    web = enrich_scene_queries(["train snow photo"], req, narration, provider='web')
    assert "from the cab" in web[0].casefold()
    assert "lineside" not in " ".join(web).casefold()


def test_inside_cab_viewpoint_phrase_and_cab_interior_survive():
    from server.services.media_query_strategy import enrich_scene_queries
    req = build_visual_requirement(
        title="BNSF cab ride",
        script="Inside the cab, the engineer watches the whiteout.",
        scene_text="Inside the cab, the engineer watches the whiteout.",
    )
    queries = enrich_scene_queries(["train snow"], req,
                                   "Inside the cab, the engineer watches the whiteout.",
                                   provider='youtube')
    assert "inside the cab" in queries[0].casefold()
    assert "whiteout" in queries[0].casefold()
