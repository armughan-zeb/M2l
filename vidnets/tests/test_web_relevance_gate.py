import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.web_image.resolver import _pre_score_candidate, _score_candidate  # noqa: E402

SCENE = (
    "Deep-Sea Welder. Far below the ocean surface, deep-sea welders repair "
    "underwater pipelines in crushing pressure and total darkness. One mistake "
    "can mean instant disaster, and the decompression process takes hours after "
    "every shift."
)

# Terms shared across other chapters of the same script must not satisfy the
# per-scene gate ("deep sea" also appears in the cave-diver and crab chapters).
WEAK = {"deep", "sea", "underwater", "pressure", "surface", "dark", "far", "hours", "water"}


class TestWebRelevanceGate:
    def test_keyword_bleed_blog_rejected(self):
        score = _score_candidate(
            Path("C:/temp/web_images_simple_x/scene_1/img.jpg"),
            1280,
            720,
            "deep sea welder",
            SCENE,
            set(),
            title="Deep Sea Animals Names at Tayla Sligo blog",
            url="https://storage.googleapis.com/diflrlaagprxe/deep-sea-animals-names.html",
            source_url="https://storage.googleapis.com/diflrlaagprxe/deep-sea-animals-names.html",
            weak_terms=WEAK,
        )
        assert score <= 0

    def test_generic_deep_sea_page_rejected_without_junk_title(self):
        score = _pre_score_candidate(
            url="https://example.com/deep-sea-fish-gallery",
            title="Deep Sea Fish | Marine Life Facts",
            source="bing_hybrid",
            query="deep sea welder",
            scene_text=SCENE,
            identity_terms=set(),
            weak_terms=WEAK,
        )
        assert score < 0

    def test_underwater_pipeline_welder_accepted(self):
        score = _pre_score_candidate(
            url="https://www.flickr.com/photos/marine/5213854190",
            title="Underwater pipeline welding crew",
            source="flickr",
            query="deep sea pipeline welding",
            scene_text=SCENE,
            identity_terms=set(),
            weak_terms=WEAK,
        )
        assert score > 0

    def test_junk_pattern_rejects_in_post_download_scorer(self):
        score = _score_candidate(
            Path("C:/temp/web_images_simple_x/scene_2/img.jpg"),
            1600,
            900,
            "volcano researcher",
            "Volcanologists sprint toward erupting mountains to collect lava samples.",
            set(),
            title="Named at Wordpress Blog | Volcano Facts",
            url="https://example.wordpress.com/volcano",
            source_url="https://example.wordpress.com/volcano",
        )
        assert score <= 0