from pathlib import Path

from server.services.documentary_template_system import direction_pack, resolve_template_design
from server.services.editorial_director.visual_dna import generate_visual_dna


ROOT = Path(__file__).resolve().parents[1]


def _is_legacy_serif(value: str) -> bool:
    return any(token in str(value).lower() for token in ("playfair", "cinzel", "lora", "georgia", "times", "serif"))


def test_generated_documentary_dna_is_modern_sans_first():
    for topic, category, script in (
        ("The Royal Succession", "history", "A king, a palace, and a public institution."),
        ("GTA VI Vice City", "gaming", "A city changes the way players move through it."),
        ("The Archive", "history", "A record changes what investigators believe."),
    ):
        dna = generate_visual_dna(topic, category, script)
        assert not _is_legacy_serif(dna.typography["display"])
        assert not _is_legacy_serif(dna.typography["body"])


def test_template_resolver_does_not_select_serif_faces_for_new_scenes():
    for direction in ("editorial", "archive", "cinematic", "investigative"):
        pack = direction_pack(direction, 17)
        design = resolve_template_design("source-highlight", seed=17, art_direction=pack, position="center")
        assert not _is_legacy_serif(design["display_font"])
        assert not _is_legacy_serif(design["body_font"])


def test_remotion_legacy_paths_have_the_open_typography_gate():
    for relative in (
        "src/remotion/components/TextCard.jsx",
        "src/remotion/components/DocumentHighlight.jsx",
        "src/remotion/components/ChapterTitle.jsx",
        "src/remotion/components/Diagram.jsx",
        "src/remotion/components/EditorialSceneCanvas.jsx",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "Archivo Black" in source or "documentaryTypography(visualDNA)" in source
        assert "backdropFilter" not in source
        assert "background: '#f4ede1'" not in source
        assert "SOURCE EXCERPT" not in source


def test_short_director_copy_uses_content_sized_minimal_highlight():
    source = (ROOT / "src" / "remotion" / "components" / "TextCard.jsx").read_text(encoding="utf-8")
    assert "['color-band', 'minimal-outline'].includes(treatment)" in source
    assert "placementStyle.width = 'fit-content'" in source
    assert "placementStyle.maxWidth = 'min(57vw, 950px)'" in source


def test_approved_copy_keeps_director_position_and_never_flashes_an_empty_panel():
    card = (ROOT / "src" / "remotion" / "components" / "TextCard.jsx").read_text(encoding="utf-8")
    typography = (ROOT / "src" / "remotion" / "components" / "EditorialType.jsx").read_text(encoding="utf-8")
    assert "const directedPosition = clean(scene.text?.position || contract.position || scene.design?.position)" in card
    assert "if (approvedCopy && !directedPosition && scene.primary_visual?.type === 'SmartImage'" in card
    assert "const containerEnter = interpolate" in typography
    assert "opacity: exit * containerEnter" in typography


def test_impact_headline_fits_the_media_safe_region_and_uses_a_tight_emphasis_slab():
    typography = (ROOT / "src" / "remotion" / "components" / "EditorialType.jsx").read_text(encoding="utf-8")
    assert "scene.media_composition?.safe_region" in typography
    assert "height * safeHeight * .78" in typography
    assert "width: 'fit-content', maxWidth: `${safeWidth * 100}%`" in typography
    assert "background: focused ? accent : 'transparent'" in typography
    assert "parts.map((part, index)" in typography


def test_document_graphic_stays_text_free_without_a_verified_excerpt():
    from server.services.editorial_director.director import _build_document_props

    props = _build_document_props(
        "yellow_marker_highlight",
        "The report exposes a concealed warning inside the record.",
        None,
    )
    assert props["title"] == ""
    assert props["no_data"] is True
    assert props["reason"] == "A verified document excerpt was not supplied"


def test_person_profile_scales_and_fits_whole_words():
    source = (ROOT / 'src/remotion/components/PersonProfile.jsx').read_text(encoding='utf-8')
    assert 'width / 1920' in source
    assert 'fittedDocumentarySize(word' in source
    assert "overflowWrap: 'anywhere'" not in source
    assert "wordBreak: 'normal'" in source


def test_standalone_profile_keeps_specialist_before_media_shot_dispatch():
    source = (ROOT / 'src/remotion/DocumentaryComposition.jsx').read_text(encoding='utf-8')
    render = source[source.index('const renderPrimarySystem ='):source.index('// Detect if scene has a centered data chart')]
    assert "if (primaryType === 'PersonProfile') return renderSceneSystem(targetScene, visualDNA, { mediaFirst: false })" in render
    assert render.index("primaryType === 'PersonProfile'") < render.index('<MediaShotSequence')
