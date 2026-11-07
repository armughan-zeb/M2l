from services.youtube_query_normalizer import normalize_youtube_query
from services.youtube_pipeline_v2 import _queries_for_row


def test_sentence_and_negative_bundle_are_not_sent_to_youtube():
    query = normalize_youtube_query(
        "prince charles king iii grew inside constitutional 1969 raw footage clean "
        "-facecam -webcam -streamer -reaction -podcast -subtitles -captioned "
        "-lyrics -slideshow -presentation -breaking",
        anchor="Prince Charles",
        category="person",
    )
    lowered = query.casefold()
    assert query.casefold().startswith("prince charles")
    assert "grew" not in lowered
    assert "inside" not in lowered
    assert "-facecam" not in lowered
    assert "-streamer" not in lowered
    assert "-breaking" not in lowered
    assert len(query.split()) <= 12


def test_full_name_is_restored_when_llm_only_returns_a_surname():
    query = normalize_youtube_query(
        "harry public appearance footage -webcam -reaction",
        anchor="Prince Harry",
        category="person",
    )
    assert query.startswith("Prince Harry")
    assert "harry harry" not in query.casefold()


def test_pipeline_rows_are_positive_and_compact():
    queries = _queries_for_row(
        {
            "domain_category": "documentary",
            "container_subject": "Prince Charles",
            "primary_container_subject": "Prince Charles",
            "container_type": "person",
        },
        "Prince Charles",
        "Prince Charles King III grew inside constitutional 1969",
    )
    assert queries
    assert all("-facecam" not in query and "-webcam" not in query for query in queries)
    assert all("grew" not in query.casefold() and "inside" not in query.casefold() for query in queries)
    assert all("Prince Charles" in query for query in queries)
    assert any("1969" in query for query in queries)
    assert all(len(query.split()) <= 12 for query in queries)


def test_gaming_and_transport_keep_visual_intent_without_sentence_glue():
    gaming = _queries_for_row(
        {
            "domain_category": "gaming",
            "container_subject": "Grand Theft Auto VI",
            "primary_container_subject": "Grand Theft Auto VI",
            "container_type": "product",
        },
        "Grand Theft Auto VI",
        "Grand Theft Auto VI returns to Vice City.",
    )
    rail = _queries_for_row(
        {
            "domain_category": "transportation",
            "container_subject": "Train Crew",
            "primary_container_subject": "Train Crew",
            "container_type": "topic",
        },
        "Train Crew",
        "Train crew behind railway crew dispatchers protect passengers.",
    )
    assert any("gameplay" in query.casefold() or "trailer" in query.casefold() for query in gaming)
    assert any("locomotive" in query.casefold() or "railway" in query.casefold() for query in rail)
    assert all("protect" not in query.casefold() for query in rail)
    assert all(not any(token.startswith("-") for token in query.split()) for query in gaming + rail)
