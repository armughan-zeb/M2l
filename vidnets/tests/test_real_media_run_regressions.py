from types import SimpleNamespace
import pytest


def test_trending_sports_and_news_cannot_pass_on_publisher_trust():
    from services.web_image.hybrid_search import _relevance_gate
    unrelated = [
        {'title': 'Cristiano Ronaldo Portugal suspension', 'domain': 'nbcnews.com',
         'image_url': 'https://nbcnews.com/ronaldo-1920.jpg'},
        {'title': 'BBC NEWS portrait', 'domain': 'bbc.com',
         'image_url': 'https://bbc.com/news/portrait.jpg'},
    ]
    assert not _relevance_gate(unrelated, 'British beers from the past documentary photograph')
    brewery = {'title': 'Brewing ale in a brewery', 'domain': 'flickr.com',
               'image_url': 'https://flickr.com/brewing.jpg'}
    assert _relevance_gate([brewery], 'British beers reference image') == [brewery]


def test_stock_rescue_uses_shared_concrete_queries_for_beer_prose():
    from services.real_media_service import _stock_last_resort_queries
    title = '15 British Beers from the Past: Which Names Still Exist?'
    a = _stock_last_resort_queries({'text': 'That is a useful starting point for fifteen names.'}, title, {})
    b = _stock_last_resort_queries({'text': 'The article describes a market in motion.'}, title, {})
    assert a == b == ['beer pint pub bar', 'beer pouring glass']
    brewing = _stock_last_resort_queries({'text': 'Malt and hops enter the brewing process.'}, title, {})
    assert brewing[0] == 'beer brewing brewery'
    assert max(map(lambda q: len(q.split()), brewing)) <= 5


def test_zero_relevance_search_rows_do_not_consume_youtube_probe_pool():
    from services.youtube_pool_renderer import _source_shortlist, _pool_probe_plan
    def record(score, title):
        return SimpleNamespace(metadata_score=score, title=title, channel='Archive', duration=60, pool_keys=['beer'])
    pools = SimpleNamespace(source_records={'beer-source': record(100, 'British brewery footage'),
        'cricket-source': record(0, 'Virat Kohli batting')}, rows=[{'pool_key': 'beer'}],
        pools={'beer': []}, moment_assignments={})
    assert _source_shortlist(pools) == ['beer-source']
    candidates, _ = _pool_probe_plan(pools)
    assert candidates == {'beer': ['beer-source']}


def test_unknown_size_hd_format_is_admitted_but_known_oversize_is_not():
    from services.youtube_pipeline_v2 import _youtube_quality_formats
    from services.yt_dlp_runtime import load_yt_dlp
    ytdlp = load_yt_dlp()
    with ytdlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
        select = ydl.build_format_selector('/'.join(_youtube_quality_formats(source=True, max_filesize=1_000_000)))
        hd = {'format_id': 'hls-1080', 'url': 'https://example.test/video.mp4',
              'height': 1080, 'width': 1920, 'vcodec': 'h264', 'acodec': 'aac', 'ext': 'mp4'}
        def selected(fmt):
            return list(select({'formats': [fmt], 'has_merged_format': False, 'incomplete_formats': False}))
        assert selected(hd), 'A real HD HLS rendition can omit filesize metadata'
        assert not selected({**hd, 'filesize': 2_000_000, 'filesize_approx': 2_000_000})


def test_owned_export_is_removed_before_youtube_ranking(monkeypatch):
    from services import youtube_pipeline_v2 as youtube
    monkeypatch.setenv('VIDZEO_YT_V2_SKIP_DOWNLOAD', '1')
    monkeypatch.setattr(youtube, '_get_authenticated_youtube_session', lambda **kw: None)
    monkeypatch.setattr(youtube, '_build_scene_rows', lambda *a, **kw: [{'scene_text': 'British beer brewing', 'section_subject': 'British beer'}])
    monkeypatch.setattr(youtube, '_search_container_pools', lambda *a, **kw: {'beer': [
        {'id': 'ownexport001', 'title': 'British beers from the past'},
        {'id': 'realarchive', 'title': 'British brewery raw footage'}]})
    seen = []
    def assign(rows, pools, *args, **kwargs):
        seen.extend(row['id'] for values in pools.values() for row in values)
        return [None]
    monkeypatch.setattr(youtube, '_assign_candidates', assign)
    youtube.get_youtube_clips('British beer brewing', 'British beer', 1,
        scene_texts=['British beer brewing'], excluded_video_ids=['ownexport001'])
    assert seen == ['realarchive']


def test_native_advert_floor_requires_narrated_advertising_context():
    from services.youtube_pipeline_v2 import _candidate_source_height_floor
    source = {'title': 'Whitbread Trophy Bitter Advert'}
    assert _candidate_source_height_floor(source) >= 720
    assert _candidate_source_height_floor({**source,
        'scene_text': 'Whitbread Trophy Bitter television advertising is remembered.'}) == 360
    assert _candidate_source_height_floor({**source, 'description': 'AI-generated recreation',
        'scene_text': 'Whitbread Trophy Bitter television advertising is remembered.'}) >= 720


def test_source_citations_do_not_become_the_visual_subject():
    from services.visual_requirement import _scene_subject, rebase_visual_requirement_for_scene
    from services.documentary_subject import is_source_attribution_subject
    assert is_source_attribution_subject('Retrowow', 'The Retrowow article describes British beers.')
    assert _scene_subject('The Retrowow article describes the 1960s through categories.') == ''
    assert _scene_subject('CAMRA’s account traces India Pale Ale to the early nineteenth century.') == 'India Pale Ale'
    assert not is_source_attribution_subject('Whitbread Trophy Bitter', 'Whitbread Trophy Bitter television advertising is remembered.')
    repaired = rebase_visual_requirement_for_scene(
        {'canonical_entity': 'The Retrowow 1960s article describes', 'subject_type': 'product',
         'strict_identity': True, 'required_terms': ['Retrowow'], 'requirement_hash': 'obsolete'},
        title='15 British Beers from the Past: Which Names Still Exist?',
        scene_text='The Retrowow article describes the 1960s through categories.',
        script='This is the story of British beers and pub brewing.')
    assert 'retrowow' not in repaired['canonical_entity'].casefold()
    assert 'beer' in repaired['canonical_entity'].casefold()
    assert repaired['requirement_hash'] != 'obsolete'


def test_director_visible_shot_stays_ahead_of_narrative_filler():
    from services.media_query_strategy import enrich_scene_queries
    planned = ['British pub beer taps 1960s', 'British beer vintage bottle labels']
    rows = enrich_scene_queries(planned, {'canonical_entity': 'British Beers', 'subject_type': 'product'},
        'The article describes a useful starting point.', provider='web', preserve_planned_order=True)
    assert rows[:2] == planned


def test_beer_category_allows_brewery_metadata_without_borrowing_named_brand_identity():
    from services.visual_requirement import beverage_category_aliases
    assert 'brewery' in beverage_category_aliases('British Beers from the Past')
    assert not beverage_category_aliases('Whitbread Trophy Bitter')


def test_visible_director_subject_is_not_truncated_to_an_adjective():
    from services.container_guard import canonical_container_subject
    assert canonical_container_subject('Busy British pub beer taps', '', 'British beers') == 'Busy British pub beer taps'
    assert canonical_container_subject('Historical British beer styles', '', 'British beers') == 'Historical British beer styles'
    assert canonical_container_subject('The Titanic was designed as', '', 'Titanic') == 'Titanic'


def test_collection_headline_does_not_crowd_out_visible_query():
    import app
    title = '15 British Beers from the Past: Which Names Still Exist?'
    query = app._subject_safe_scene_brain_query('British beer brewing archival footage', '',
        'British Beers from the Past', title)
    assert query == 'British beer brewing archival footage'
    assert app._clean_scene_brain_subject('Busy', 'British beer') == 'British beer'
    assert 'Grand Theft Auto VI' in app._subject_safe_scene_brain_query('Vice City skyline', '', 'Vice City', 'Grand Theft Auto VI')


def test_youtube_extracted_frame_never_counts_as_moving_footage():
    from services.media_resolution_coordinator import _is_moving_youtube_source
    assert not _is_moving_youtube_source({'source': 'youtube', 'path': 'frame.jpg', 'media_type': 'image'})
    assert not _is_moving_youtube_source({'source': 'youtube', 'path': 'frame.jpg', 'media_type': 'video'})
    assert _is_moving_youtube_source({'source': 'youtube', 'path': 'clip.mp4', 'media_type': 'video'})


def test_distinct_locked_beer_brands_keep_separate_youtube_source_pools():
    from services.youtube_pipeline_v2 import _build_scene_rows, _group_rows
    names = ['Whitbread Trophy Bitter', 'Stella Artois']
    overrides = [{'_semantic_locked': True, 'section_subject': name, 'scene_subject': name,
        'domain_anchor': name, 'title_anchor': name, 'youtube_query': name + ' advert footage'} for name in names]
    rows = _build_scene_rows('British Beers from the Past', [name + ' television advertising.' for name in names], overrides)
    assert [row['candidate_subject'] for row in rows] == names
    assert len(_group_rows(rows)) == 2


@pytest.mark.parametrize('moving_available', [True, False])
def test_slideshow_candidate_is_replaced_before_youtube_handoff(monkeypatch, tmp_path, moving_available):
    from services import youtube_pipeline_v2 as youtube
    from services import youtube_pool_renderer as quality
    from services import youtube_media_cache as cache
    monkeypatch.setenv('VIDZEO_YT_RANGED_MOMENTS', '0')
    rows = [{'scene_text': 'Beer flows from a pub tap.', 'section_subject': 'British Beer',
             'candidate_subject': 'British Beer', 'domain_category': 'food'}]
    candidates = [{'id': vid, 'title': 'British beer pub footage', 'duration': 60,
                   'best_timestamp': 20} for vid in (['still000001', 'moving00001'] if moving_available else ['still000001'])]
    monkeypatch.setattr(youtube, '_get_authenticated_youtube_session', lambda **kw: None)
    monkeypatch.setattr(youtube, '_build_scene_rows', lambda *a, **kw: rows)
    monkeypatch.setattr(youtube, '_search_container_pools', lambda *a, **kw: {'british beer': candidates})
    monkeypatch.setattr(youtube, '_assign_candidates', lambda *a, **kw: [candidates[0]])
    monkeypatch.setattr(youtube, '_score_candidate', lambda *a, **kw: 100)
    monkeypatch.setattr(cache, 'get_verified_cut', lambda *a, **kw: None)
    stored, downloaded = [], []
    monkeypatch.setattr(cache, 'put_verified_cut', lambda video_id, *a, **kw: stored.append(video_id))
    def download(candidate, out, *a, **kw):
        downloaded.append(candidate['id'])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b'\x00\x00\x00\x20ftyp' + b'x'*9000)
        return True
    monkeypatch.setattr(youtube, '_download_clip', download)
    monkeypatch.setattr(youtube, '_clean_downloaded_clip_overlays', lambda path, **kw: (path, {'checked': True, 'clean': True}))
    monkeypatch.setattr(quality, '_screen_moment_quality', lambda path: {
        'checked': True, 'reject': 'still000001' in str(path), 'reason': 'still_or_slideshow_frame'})
    result = youtube.get_youtube_clips('Beer flows from a pub tap.', 'British Beer', 1,
        scene_texts=[rows[0]['scene_text']], scene_durations=[4], output_dir=tmp_path,
        download_candidate_limit=2, max_download_attempts_per_scene=2, pool_first=False,
        visual_context=SimpleNamespace(enabled=False, visual_required=False))
    if moving_available:
        assert result[0]
        assert downloaded == ['still000001', 'moving00001']
        assert stored == ['moving00001']
    else:
        assert not result[0], 'quota salvage must not resurrect a rejected still clip'
        assert not stored, 'a slideshow must never enter the verified moving-clip cache'
