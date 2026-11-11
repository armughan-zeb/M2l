from __future__ import annotations

from services.visual_requirement import (
    build_visual_requirement,
    candidate_nearest_match,
    candidate_requirement_match,
    graphic_fallback_spec,
    provider_query_variants,
    rebase_visual_requirement_for_scene,
)


def _jaguar_requirement():
    return build_visual_requirement(
        title="The Jaguar E-Type: The Old Car That Changed Everything",
        script="This documentary examines the 1961 Jaguar E-Type sports car and its inline-six engine.",
        scene_text="The 1961 Jaguar E-Type introduced a revolutionary sports car design.",
    )


def test_locomotive_mechanics_do_not_require_automobile_footage():
    for narration in (
        'A diesel locomotive is powered by a diesel engine.',
        'Its driving wheels receive power through a transmission.',
        'The crew uses the dashboard inside the cab.',
        'Installing a powerful diesel engine into a train car created mechanical problems.',
    ):
        req = build_visual_requirement(title='Inside the Cab of a Diesel Locomotive',
            script=narration, scene_text=narration,
            semantic_context={'domain_category': 'automotive'})
        assert req['subject_type'] == 'named_entity'
        assert req['domain'] == 'rail_transport'
        assert 'automobile' not in req['required_terms']
        matched, reason, _ = candidate_requirement_match(req,
            {'title': 'Diesel locomotive cab and engine cold start footage'})
        assert matched, reason


def test_cached_locomotive_automobile_contract_is_repaired_and_review_invalidated():
    old = {'canonical_entity': 'Diesel Locomotive', 'subject_type': 'automobile',
           'domain': 'automotive', 'required_terms': ['automobile', 'diesel locomotive'],
           'aliases': ['Diesel Locomotive car'], 'requirement_hash': 'obsolete-car-review'}
    repaired = rebase_visual_requirement_for_scene(old,
        title='Inside the Cab of a Diesel Locomotive', scene_text='The engine drives the generator.')
    assert repaired['domain'] == 'rail_transport'
    assert repaired['subject_type'] == 'named_entity'
    assert 'automobile' not in repaired['required_terms']
    assert 'Diesel Locomotive car' not in repaired['aliases']
    assert repaired['requirement_hash'] != old['requirement_hash']


def test_required_term_case_and_order_do_not_change_semantic_hash():
    from dataclasses import replace
    from services.visual_requirement import VisualRequirementV1
    original = VisualRequirementV1(canonical_entity='Tesla', subject_type='automobile', domain='automotive',
        required_terms=('Tesla', 'automobile'))
    normalized = replace(original, required_terms=('automobile', 'tesla'), requirement_hash='')
    assert normalized.requirement_hash == original.requirement_hash
    other = replace(original, required_terms=('Powerwall', 'electricity'), requirement_hash='')
    assert other.requirement_hash != original.requirement_hash


def test_football_drive_does_not_create_an_automotive_visual_requirement():
    requirement = build_visual_requirement(
        title="Super Bowl XLIX: One Yard, One Decision",
        script=(
            "Seattle has one final drive. A catch by Jermaine Kearse keeps it alive. "
            "The Seahawks face second-and-goal at the Patriots' one-yard line."
        ),
        scene_text="A catch by Jermaine Kearse has kept the drive alive.",
    )

    assert requirement["subject_type"] != "automobile"
    assert requirement["domain"] != "automotive"
    assert "automobile" not in requirement["forbidden_subjects"]


def test_named_automobile_requirement_rejects_jaguar_animal():
    requirement = _jaguar_requirement()
    matched, reason, _evidence = candidate_requirement_match(
        requirement,
        {"title": "Jaguar wildlife documentary about a big cat"},
    )
    assert not matched
    assert reason == "forbidden_confusable_entity"


def test_person_requirement_starts_with_full_name_portrait_and_photo_queries():
    requirement = build_visual_requirement(
        title="Virat Kohli biography",
        script="Virat Kohli is an Indian cricketer known for batting and leadership.",
        scene_text="Virat Kohli bats in a cricket match.",
    )
    assert requirement["canonical_entity"] == "Virat Kohli"
    assert requirement["subject_type"] == "person"
    queries = provider_query_variants(requirement, provider="web")
    assert queries[:2] == ["Virat Kohli portrait", "Virat Kohli photo"]
    assert any("cricket action photograph" in query for query in queries[:5])


def test_dated_person_queries_keep_the_year_in_the_first_retrieval_wave():
    from services.visual_requirement import VisualRequirementV1
    requirement = VisualRequirementV1(
        canonical_entity="Elizabeth II", subject_type="person",
        aliases=("Princess Elizabeth",), required_attributes={"year": "1952"},
    ).to_dict()
    queries = provider_query_variants(requirement, provider="web")
    assert queries[:2] == ["Elizabeth II 1952 portrait", "Elizabeth II 1952 photo"]
    assert queries[2:4] == ["Princess Elizabeth 1952 portrait", "Princess Elizabeth 1952 photo"]


def test_broad_royal_family_title_uses_scene_local_person_queries():
    title = "The British Royal Family"
    scene = (
        "After six years of living in the United States, Prince Harry and "
        "Meghan Markle are preparing to return to the United Kingdom."
    )
    requirement = build_visual_requirement(
        title=title,
        script=scene,
        scene_text=scene,
        visual_intent="documentary_context",
    )
    assert requirement["canonical_entity"] == "Prince Harry"
    queries = provider_query_variants(requirement, provider="web")
    assert queries[:2] == ["Prince Harry portrait", "Prince Harry photo"]
    assert all("British Royal Family" not in query for query in queries[:4])


def test_explicitly_different_regnal_ordinal_is_rejected_before_vision():
    requirement = build_visual_requirement(
        title="The British Royal Family",
        script="King Charles III is rebuilding his relationship with Prince Harry.",
        scene_text="King Charles III appears more welcoming toward his son.",
    )
    assert requirement["canonical_entity"] == "King Charles III"

    matched, reason, evidence = candidate_requirement_match(
        requirement,
        {"title": "An iconic royal portrait of King Charles I on horseback"},
    )

    assert not matched
    assert reason == "conflicting_person_ordinal"
    assert evidence["required_person_ordinal"] == "III"
    assert evidence["candidate_person_ordinals"] == ["i"]
    assert evidence["conflicting_entity"] is True


def test_missing_regnal_ordinal_stays_eligible_for_visual_review():
    requirement = build_visual_requirement(
        title="The British Royal Family",
        script="King Charles III is rebuilding his relationship with Prince Harry.",
        scene_text="King Charles III appears more welcoming toward his son.",
    )

    matched, reason, _evidence = candidate_requirement_match(
        requirement,
        {"title": "A formal portrait of King Charles"},
    )

    assert not matched
    assert reason != "conflicting_person_ordinal"


def test_person_requirement_rejects_virtual_world_catalog_asset():
    requirement = build_visual_requirement(
        title="The British Royal Family",
        script="Prince William is the heir to the throne.",
        scene_text="Prince William is the heir to the throne.",
    )
    matched, reason, evidence = candidate_requirement_match(
        requirement,
        {
            "title": "Prodigy - Male 25",
            "description": (
                "Harry skin, Prince hair, Prodigy eyes, anatomy and clothing. "
                "Now at We Love Roleplay."
            ),
        },
    )

    assert not matched
    assert reason == "person_confusable_entity"
    assert "virtual_avatar_catalog" in evidence["confusable_hits"]


def test_person_requirement_rejects_wax_museum_figure_metadata():
    requirement = build_visual_requirement(
        title="The British Royal Family",
        script="Prince William and Kate Middleton arrive at Balmoral.",
        scene_text="Prince William and Kate Middleton arrive at Balmoral.",
    )
    matched, reason, evidence = candidate_requirement_match(
        requirement,
        {
            "title": "Royal Family at Madame Tussauds",
            "description": "Wax figures of Prince William and Kate Middleton in a museum.",
        },
    )

    assert not matched
    assert reason == "person_confusable_entity"
    assert "madame tussauds" in evidence["confusable_hits"]


def test_broad_royal_family_intro_inherits_first_script_person_anchor():
    requirement = build_visual_requirement(
        title="The British Royal Family",
        script=(
            "The British Royal Family is facing a new chapter. "
            "Prince Harry and Meghan Markle are preparing to return."
        ),
        scene_text="The monarchy is entering an unexpected new chapter.",
        visual_intent="documentary_context",
    )
    assert requirement["canonical_entity"] == "Prince Harry"
    assert provider_query_variants(requirement, provider="web")[0] == "Prince Harry portrait"


def test_person_nearest_match_requires_the_name_beyond_the_honorific():
    william = build_visual_requirement(
        title="The British Royal Family",
        script="Prince William is the heir to the throne.",
        scene_text="Prince William may still have reservations.",
    )
    matched, reason, _evidence = candidate_nearest_match(
        william,
        {"title": "Prince Harry and Meghan Markle at a royal event"},
    )
    assert not matched
    assert reason == "canonical_entity_mismatch"


def test_scene_person_query_strips_possessive_suffix():
    requirement = build_visual_requirement(
        title="The British Royal Family",
        script="Prince William's position remains central to the story.",
        scene_text="But Prince William's position could be very different.",
    )
    assert requirement["canonical_entity"] == "Prince William"
    assert provider_query_variants(requirement, provider="web")[0] == "Prince William portrait"


def test_typed_non_person_requirement_does_not_use_portrait_fallback():
    place = build_visual_requirement(
        title="Paris travel guide",
        script="A guide to Paris and its landmarks.",
        scene_text="The Eiffel Tower defines the Paris skyline.",
    )
    place_queries = provider_query_variants(place, provider="web")
    assert place_queries[0].startswith("Paris landmark photograph")
    assert not any("portrait" in query for query in place_queries)

    food = build_visual_requirement(
        title="Apple nutrition",
        script="Apples provide fiber and vitamin C.",
        scene_text="Fresh apples at a market.",
    )
    food_queries = provider_query_variants(food, provider="web")
    assert food_queries[:2] == [
        "Apple nutrition food photograph",
        "Apple nutrition fresh produce photo",
    ]
    assert not any("portrait" in query for query in food_queries)


def test_named_automobile_requirement_rejects_modern_honda_for_old_jaguar():
    requirement = _jaguar_requirement()
    matched, reason, _evidence = candidate_requirement_match(
        requirement,
        {"title": "2024 Honda Civic Type R review and driving footage"},
    )
    assert not matched
    assert reason == "forbidden_confusable_entity"


def test_named_automobile_requirement_accepts_matching_model_year_and_type():
    requirement = _jaguar_requirement()
    matched, reason, evidence = candidate_requirement_match(
        requirement,
        {"title": "Jaguar E-Type 1961 classic sports car archival footage"},
    )
    assert matched
    assert reason == "identity_evidence_passed"
    assert evidence["metadata_source"] == "asset"


def test_named_automobile_model_is_evidence_even_without_generic_car_word():
    requirement = _jaguar_requirement()
    matched, reason, _evidence = candidate_requirement_match(
        requirement,
        {"title": "1961 Jaguar E-Type launch footage"},
    )
    assert matched
    assert reason == "identity_evidence_passed"


def test_generic_old_car_requirement_rejects_current_model_but_accepts_vintage_car():
    requirement = build_visual_requirement(
        title="Old Cars: A History of the Automobile",
        script="This documentary explores the history of old cars and classic automobiles.",
        scene_text="Vintage cars transformed the automobile.",
    )
    modern, modern_reason, _ = candidate_requirement_match(
        requirement,
        {"title": "2024 Honda Civic Type R review and driving footage"},
    )
    vintage, vintage_reason, _ = candidate_requirement_match(
        requirement,
        {"title": "Vintage classic cars historical automobile footage"},
    )
    assert not modern
    assert modern_reason == "forbidden_confusable_entity"
    assert vintage
    assert vintage_reason == "identity_evidence_passed"


def test_sentence_openers_are_not_promoted_to_subjects():
    requirement = build_visual_requirement(
        title="Old Cars: A History of the Automobile",
        script="We begin with the history of classic automobiles.",
        scene_text="Unlike modern vehicles, vintage cars used different engineering.",
    )
    assert requirement["canonical_entity"] == "Old Cars"
    assert requirement["canonical_entity"] not in {"We", "Unlike", "Automobile"}


def test_editorial_title_wrapper_does_not_change_jaguar_identity():
    requirement = build_visual_requirement(
        title="The History of the Jaguar E-Type",
        script="The Jaguar E-Type became a defining classic sports car.",
        scene_text="Its launch established a new standard for performance.",
    )
    assert requirement["canonical_entity"] == "Jaguar E-Type"
    assert requirement["subject_type"] == "automobile"


def test_incidental_animal_word_cannot_flip_vehicle_subject_type():
    requirement = build_visual_requirement(
        title="Jaguar E-Type",
        script="The name recalled the animal, but this chapter is about the car's design.",
        scene_text="The sports car's long bonnet defined its silhouette.",
    )
    assert requirement["subject_type"] == "automobile"


def test_manufacturer_and_chassis_code_are_automobile_evidence():
    requirement = build_visual_requirement(
        title="Toyota Corolla AE101 - The Engineering Behind the Legend",
        script="The Toyota Corolla AE101 was manufactured throughout the 1990s.",
        scene_text="Its engineering made the AE101 famous around the world.",
    )
    assert requirement["canonical_entity"] == "Toyota Corolla AE101"
    assert requirement["subject_type"] == "automobile"
    assert requirement["domain"] == "automotive"


def test_bare_ambiguous_name_uses_scene_domain_instead_of_first_script_keyword():
    requirement = build_visual_requirement(
        title="Jaguar",
        script="The name is also used for a wild animal, but this film follows the classic car on the road.",
        scene_text="A Jaguar automobile is shown in archival driving footage.",
        semantic_context={"domain_category": "automotive", "container_type": "product"},
    )
    assert requirement["canonical_entity"] == "Jaguar"
    assert requirement["subject_type"] == "automobile"
    assert "animal" in requirement["confusable_entities"]


def test_query_text_alone_can_never_approve_a_candidate():
    requirement = _jaguar_requirement()
    matched, reason, evidence = candidate_requirement_match(
        requirement,
        {"query": "Jaguar E-Type classic automobile archival photograph"},
    )
    assert not matched
    assert reason == "missing_asset_identity_metadata"
    assert evidence["metadata_source"] == "query_only"


def test_named_make_category_is_normalized_without_brand_specific_rules():
    requirement = build_visual_requirement(
        title="Jaguar Cars",
        script="This documentary follows the marque's classic road cars.",
        scene_text="The Jaguar E-Type became an automotive icon.",
    )
    assert requirement["canonical_entity"] == "Jaguar"
    assert all("jaguar wildlife" not in value.casefold() for value in requirement["forbidden_subjects"])

    matching, matching_reason, _ = candidate_requirement_match(
        requirement,
        {"title": "Jaguar E-Type classic automobile archival footage"},
    )
    ambiguous, ambiguous_reason, _ = candidate_requirement_match(
        requirement,
        {"title": "Jaguar wildlife close-up"},
    )
    assert matching and matching_reason == "identity_evidence_passed"
    assert not ambiguous and ambiguous_reason == "forbidden_confusable_entity"


def test_queries_are_identity_preserving_and_not_sports_fallbacks():
    requirement = _jaguar_requirement()
    queries = provider_query_variants(requirement, provider="youtube")
    assert all("jaguar" in query.casefold() for query in queries)
    assert all("e-type" in query.casefold() or "e type" in query.casefold() for query in queries)
    assert all(term not in " ".join(queries).casefold() for term in ("goals highlights", "training footage"))
    assert all("-jaguar" not in query.casefold() for query in queries)
    # YouTube receives only the positive identity phrase; forbidden
    # look-alikes are enforced by metadata/vision gates after download.
    assert all("-animal" not in query.casefold() and "-wildlife" not in query.casefold() for query in queries)


def test_train_crew_contract_accepts_role_aliases_without_exact_phrase_match():
    requirement = build_visual_requirement(
        title="Train Crew: Conductors and Locomotive Engineers",
        script="This documentary follows conductors, locomotive engineers, dispatchers, and station staff.",
        scene_text="A conductor checks the doors before departure.",
    )
    assert requirement["subject_type"] == "rail_crew"
    assert requirement["domain"] == "rail_transport"
    assert "train conductor" in requirement["aliases"]

    matched, reason, evidence = candidate_requirement_match(
        requirement,
        {"title": "Train Conductor on Duty, 1960s - Archival Film"},
    )
    assert matched
    assert reason == "identity_evidence_passed"
    assert evidence["metadata_source"] == "asset"


def test_train_crew_contract_rejects_animal_decoy_and_query_only_evidence():
    requirement = build_visual_requirement(
        title="Train Crew Life",
        script="The film follows railway crews working safely with passengers.",
        scene_text="The crew prepares a train for departure.",
    )
    animal, animal_reason, _ = candidate_requirement_match(
        requirement,
        {"title": "Animal crew and safari wildlife documentary"},
    )
    query_only, query_reason, evidence = candidate_requirement_match(
        requirement,
        {"query": "Train Crew railway conductor footage"},
    )
    assert not animal
    assert animal_reason == "forbidden_confusable_entity"
    assert not query_only
    assert query_reason == "missing_asset_identity_metadata"
    assert evidence["metadata_source"] == "query_only"


def test_provider_query_echo_is_not_asset_identity_evidence():
    requirement = build_visual_requirement(
        title="Train Crew Life",
        script="The film follows railway crews working safely with passengers.",
        scene_text="The crew prepares a train for departure.",
    )
    matched, reason, evidence = candidate_requirement_match(
        requirement,
        {
            "title": "47 ideas de John Lennon, rock clásico",
            "description": "Yandex result for: Train Crew railway crew departure documentary photograph",
        },
    )
    assert not matched
    assert reason == "missing_rail_crew_evidence"
    assert evidence["metadata_source"] == "asset"
    assert evidence["rail_hits"] == []


def test_train_crew_queries_include_role_recall_without_dropping_identity():
    requirement = build_visual_requirement(
        title="Train Crew Life",
        script="This documentary follows railway crews and conductors.",
        scene_text="A locomotive engineer reads the signals ahead.",
    )
    queries = provider_query_variants(requirement, provider="youtube")
    assert all("train" in query.casefold() and "crew" in query.casefold() for query in queries)
    assert any("conductor" in query.casefold() for query in queries)
    assert any("locomotive" in query.casefold() for query in queries)


def test_provider_queries_drop_narrative_glue_and_keep_a_canonical_pool_wave():
    requirement = build_visual_requirement(
        title="Train Crew: Conductors and Locomotive Engineers",
        script="This documentary follows railway crews.",
        scene_text=(
            "These moments do not appear on a timetable, but they are part of "
            "what makes the work meaningful. Behind every arrival, remember "
            "that the journey depends on people."
        ),
    )
    queries = provider_query_variants(requirement, provider="web")
    joined = " ".join(queries).casefold()
    assert len(queries) == 4
    assert all(term not in joined.split() for term in (
        "they", "what", "meaningful", "behind", "every", "remember", "journey", "depends",
    ))
    assert any(
        "train crew railway crew conductor locomotive engineer" in query.casefold()
        for query in queries
    )


def test_provider_queries_carry_scene_context_without_redefining_identity():
    first = build_visual_requirement(
        title="Train Crew Life",
        script="A documentary follows railway crews.",
        scene_text="Before departure, the conductor checks the doors and platform.",
    )
    second = build_visual_requirement(
        title="Train Crew Life",
        script="A documentary follows railway crews.",
        scene_text="Inside the cab, the locomotive engineer reads signals and gradients.",
    )
    first_queries = provider_query_variants(first, provider="web")
    second_queries = provider_query_variants(second, provider="web")

    assert first_queries != second_queries
    assert any(term in " ".join(first_queries).casefold() for term in ("departure", "conductor", "doors", "platform"))
    assert any(term in " ".join(second_queries).casefold() for term in ("cab", "locomotive", "engineer", "signals", "gradients"))
    assert all(query.casefold().startswith("train crew") for query in first_queries + second_queries)


def test_youtube_allows_nearest_same_model_year_but_web_does_not():
    requirement = _jaguar_requirement()
    youtube_candidate = {
        "provider": "youtube",
        "title": "1963 Jaguar E-Type 3.8 Series 1 walkaround driving footage",
        "description": "Classic automobile road footage.",
    }
    web_candidate = {
        "provider": "web",
        "title": "1963 Jaguar E-Type 3.8 Series 1 photograph",
    }
    nearest, nearest_reason, evidence = candidate_requirement_match(requirement, youtube_candidate)
    exact_web, web_reason, _ = candidate_requirement_match(requirement, web_candidate)
    assert nearest
    assert nearest_reason == "identity_evidence_passed"
    assert evidence["year_match_mode"] == "youtube_nearest_same_model_year"
    assert not exact_web
    assert web_reason == "wrong_required_year"


def test_youtube_query_variants_include_motion_retrieval_waves():
    queries = provider_query_variants(_jaguar_requirement(), provider="youtube")
    joined = " ".join(queries).casefold()
    assert len(queries) >= 8
    assert "walkaround" in joined
    assert "driving" in joined
    assert "engine" in joined
    assert "restoration" in joined


def test_nearest_match_handoff_keeps_same_domain_archival_candidate_for_vision():
    requirement = _jaguar_requirement()
    matched, reason, evidence = candidate_nearest_match(
        requirement,
        {"provider": "web", "title": "Classic sports car archival road photograph"},
    )
    assert matched
    assert reason == "nearest_context_pending_visual_required"
    assert evidence["nearest_match"] is True
    assert evidence["verification_required"] is True


def test_nearest_match_handoff_rejects_wrong_named_person_role():
    requirement = build_visual_requirement(
        title="Prince Harry biography",
        script="A documentary about Prince Harry and the British royal family.",
        scene_text="Prince Harry attends a royal ceremony.",
    )
    matched, reason, evidence = candidate_nearest_match(
        requirement,
        {"provider": "web", "title": "Harry Styles singer music video"},
    )
    assert not matched
    assert reason == "person_role_mismatch"
    assert evidence["nearest_match"] is False


def test_nearest_match_handoff_rejects_gta_v_for_gta_vi_without_explicit_comparison():
    requirement = build_visual_requirement(
        title="Grand Theft Auto VI trailer",
        script="Grand Theft Auto VI is the next Rockstar Games video game.",
        scene_text="Use open-world driving gameplay as supporting footage.",
    )
    matched, reason, evidence = candidate_nearest_match(
        requirement,
        {"provider": "youtube", "title": "Grand Theft Auto V gameplay driving footage"},
    )
    assert not matched
    assert reason == "game_installment_mismatch"
    assert evidence["nearest_match"] is False


def test_nearest_match_handoff_allows_gta_v_only_for_explicit_prior_game_comparison():
    requirement = build_visual_requirement(
        title="Grand Theft Auto VI trailer",
        script="Grand Theft Auto VI returns to Vice City.",
        scene_text="Compare GTA V's earlier open-world driving as a predecessor reference.",
    )
    matched, reason, evidence = candidate_nearest_match(
        requirement,
        {"provider": "youtube", "title": "Grand Theft Auto V gameplay driving footage"},
    )
    assert matched
    assert reason == "nearest_context_pending_visual_required"
    assert evidence["nearest_basis"] == "same_franchise_version"


def test_nearest_match_handoff_rejects_unrelated_gaming_result():
    requirement = build_visual_requirement(
        title="Grand Theft Auto VI trailer",
        script="Grand Theft Auto VI is the next Rockstar Games video game.",
        scene_text="The next entry returns to Vice City.",
    )
    matched, _reason, evidence = candidate_nearest_match(
        requirement,
        {"provider": "web", "title": "Minecraft gameplay survival guide"},
    )
    assert not matched
    assert evidence["nearest_match"] is False


def test_schedule_language_does_not_invent_a_historical_era():
    requirement = build_visual_requirement(
        title="Train Crew Life",
        script=(
            "This documentary follows railway crews. Early starts and late finishes "
            "are part of the profession, and older passengers may need assistance "
            "during long periods away from home."
        ),
        scene_text="The conductor checks the platform before departure.",
    )
    assert requirement["subject_type"] == "rail_crew"
    assert "era" not in requirement["required_attributes"]
    queries = provider_query_variants(requirement, provider="web")
    assert all("archival" not in query.casefold() for query in queries)


def test_graphic_fallback_is_grounded_to_requirement():
    requirement = _jaguar_requirement()
    fallback = graphic_fallback_spec(requirement, "The 1961 Jaguar E-Type changed sports car design.", scene_index=3)
    assert fallback["grounded"] is True
    assert fallback["template_id"] == "date_card"
    assert fallback["entity"] == "Jaguar E-Type"
    assert fallback["requirement_hash"] == requirement["requirement_hash"]


def test_scene_attributes_do_not_leak_first_script_year_into_every_scene():
    requirement = build_visual_requirement(
        title="Toyota Corolla",
        script=(
            "Toyota introduced the Corolla in 1966. Later generations changed "
            "the design and markets."
        ),
        scene_text="The model became a familiar family car around the world.",
    )
    assert requirement["canonical_entity"] == "Toyota Corolla"
    assert "year" not in requirement["required_attributes"]


def test_timeline_scene_uses_local_named_entity_instead_of_first_script_entity():
    script = (
        "On April 14, 1912, the RMS Titanic sank. "
        "In 1915, a German U-boat torpedoed the British liner Lusitania. "
        "In 2012, the Costa Concordia capsized off Giglio."
    )
    requirement = build_visual_requirement(
        title="1915",
        script=script,
        scene_text="In 1915, a German U-boat torpedoed the British liner Lusitania.",
        positive_subjects=("1915",),
    )
    assert requirement["canonical_entity"] == "Lusitania"
    assert requirement["required_attributes"]["year"] == "1915"


def test_listicle_shell_does_not_become_every_scene_identity():
    script = (
        "On April 14, 1912, the RMS Titanic sank. "
        "In 1915, a German U-boat torpedoed the British liner Lusitania."
    )
    requirement = build_visual_requirement(
        title="Top 4 Famous Shipwrecks (Web + YT)",
        script=script,
        scene_text="In 1915, a German U-boat torpedoed the British liner Lusitania.",
    )
    assert requirement["canonical_entity"] == "Lusitania"


def test_cached_requirement_is_rebased_to_scene_local_attributes():
    cached = build_visual_requirement(
        title="Toyota Corolla",
        script="Toyota introduced the Corolla in 1966.",
        scene_text="The Corolla debuted in 1966.",
    )
    refreshed = rebase_visual_requirement_for_scene(
        cached,
        title="Toyota Corolla",
        scene_text="Later generations adapted the car for new markets.",
    )
    assert "year" not in refreshed["required_attributes"]
    assert "1966" not in refreshed["required_terms"]


def test_universal_named_entity_rejects_single_token_mismatch():
    requirement = build_visual_requirement(
        title="Grand Theft Auto VI: The Next Chapter",
        script="Grand Theft Auto VI is the most anticipated video game release in history.",
        scene_text="Rockstar Games prepared the next entry in the Grand Theft Auto series.",
    )
    # Grand Canyon only matches 'grand' (1 token out of 3)
    canyon_match, canyon_reason, _ = candidate_requirement_match(
        requirement,
        {"title": "Visiter le Grand Canyon en Arizona guide complet photograph"},
    )
    assert not canyon_match
    assert canyon_reason == "canonical_entity_mismatch"

    # Brussels Grand-Place only matches 'grand'
    brussels_match, brussels_reason, _ = candidate_requirement_match(
        requirement,
        {"title": "Grand-Place de Bruxelles en direct tourisme europe"},
    )
    assert not brussels_match
    assert brussels_reason == "canonical_entity_mismatch"

    # Valid GTA candidate matches
    gta_match, gta_reason, _ = candidate_requirement_match(
        requirement,
        {"title": "Grand Theft Auto VI official trailer screenshot from Rockstar Games"},
    )
    assert gta_match
    assert gta_reason == "identity_evidence_passed"


def test_single_event_year_is_inherited_by_opening_and_aftermath_beats():
    script = (
        "The 1967 Ice Bowl was the NFL Championship game at Lambeau Field. "
        "The cold changed the game. Bart Starr made the winning sneak."
    )
    opening = build_visual_requirement(
        title="The Ice Bowl: One Yard, One Decision",
        script=script,
        scene_text="The cold changed the game.",
    )
    ending = build_visual_requirement(
        title="The Ice Bowl: One Yard, One Decision",
        script=script,
        scene_text="Bart Starr made the winning sneak.",
    )
    assert opening["required_attributes"]["year"] == "1967"
    assert ending["required_attributes"]["year"] == "1967"
    cached = build_visual_requirement(title="The Ice Bowl", script=script,
                                      scene_text="The 1967 championship ended in Green Bay.")
    rebased = rebase_visual_requirement_for_scene(
        cached, title="The Ice Bowl", scene_text="The cold changed the game.", script=script,
    )
    assert rebased["required_attributes"]["year"] == "1967"


def test_video_game_release_year_stays_on_the_scene_that_states_the_date():
    title = "GTA VI: Is Vice City Only the Doorway?"
    script = (
        "Grand Theft Auto VI returns to Vice City. The game is scheduled for "
        "November 19, 2026, on PlayStation 5 and Xbox Series X|S."
    )
    opening = build_visual_requirement(
        title=title,
        script=script,
        scene_text="What if Vice City is only the doorway?",
        positive_subjects=("Grand Theft Auto VI",),
    )
    date = build_visual_requirement(
        title=title,
        script=script,
        scene_text="The game is scheduled for November 19, 2026, on PlayStation 5 and Xbox Series X|S.",
        positive_subjects=("Grand Theft Auto VI",),
    )
    assert "year" not in opening["required_attributes"]
    assert date["required_attributes"]["year"] == "2026"


def test_mixed_event_years_do_not_inherit_an_arbitrary_global_period():
    requirement = build_visual_requirement(
        title="Famous Shipwrecks",
        script="The Titanic sank in 1912; the Lusitania sank in 1915.",
        scene_text="The Lusitania was torpedoed in 1915.",
    )
    assert requirement["required_attributes"].get("year") == "1915"


def test_gaming_subject_type_and_clean_queries():
    requirement = build_visual_requirement(
        title="Grand Theft Auto VI is still months away",
        script="Rockstar Games announced Grand Theft Auto VI gameplay details for PlayStation and Xbox.",
        scene_text="The next Grand Theft Auto entry takes players back to Vice City.",
    )
    assert requirement["canonical_entity"] == "Grand Theft Auto VI"
    assert requirement["subject_type"] == "video_game"
    web_queries = provider_query_variants(requirement, provider="web")
    yt_queries = provider_query_variants(requirement, provider="youtube")
    assert any("gameplay" in q for q in web_queries)
    assert any("trailer" in q for q in yt_queries)
    assert not any("documentary documentary" in q for q in web_queries)


def test_rail_operator_footage_is_not_accepted_for_another_operator():
    """A CSX freight frame must not illustrate an Amtrak scene, and a real
    Amtrak frame must carry identity proof for finalist ranking."""
    req = build_visual_requirement(
        title="Amtrak's Long Run Through the Crew's Day",
        script="The Amtrak crew works a long day on the rails.",
        scene_text="Passengers board the Amtrak passenger train as the crew prepares for departure.",
    )
    assert "csx" in req["forbidden_subjects"]
    assert "bnsf" in req["forbidden_subjects"]
    matched, reason, _evidence = candidate_requirement_match(req, {
        "title": "CSX freight locomotive 6232 hauling coal",
        "description": "CSX Transportation freight train",
        "provider": "youtube",
    })
    assert not matched
    assert reason == "forbidden_confusable_entity"
    _matched, _reason, evidence = candidate_requirement_match(req, {
        "title": "Amtrak passenger train boarding at the station",
        "description": "Amtrak Northeast Regional departs",
        "provider": "youtube",
    })
    assert evidence.get("identity_proof")
    bnsf_req = build_visual_requirement(
        title="BNSF Whiteout Crossing",
        script="A BNSF train crosses in a whiteout.",
        scene_text="The BNSF locomotive pushes through snow.",
    )
    assert "amtrak" in bnsf_req["forbidden_subjects"]
    assert "csx" in bnsf_req["forbidden_subjects"]
