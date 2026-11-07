"""Unit tests for grounded emphasis selection and whisper alignment.

Verifies:
1. Multi-word compound proper names are detected intact ("Grand Theft Auto VI", "Vice City", "Stephen Elop").
2. Negation phrases are preserved ("never sleeps", "without warning", "not exist").
3. Number and unit pairs remain linked ("$2 billion", "14 percent", "24 hours").
4. Stopwords alone are never selected as highlights.
5. Whisper word timestamps accurately bound emphasis timing without hallucinated offsets.
"""

from __future__ import annotations

import pytest
from server.services.highlight_selector import (
    STOPWORDS,
    NEGATION_WORDS,
    align_highlight_with_whisper,
    clean_highlight_tokens,
    highlight_tokens_for,
    select_grounded_highlight,
)


def test_compound_proper_names_detected_intact():
    text1 = "Fans waited a decade for Grand Theft Auto VI across the globe."
    highlight1 = select_grounded_highlight(text1, max_words=4)
    assert highlight1 == "Grand Theft Auto VI"

    text2 = "Rockstar Games invested hundreds of millions into the new physics engine."
    highlight2 = select_grounded_highlight(text2, max_words=3)
    assert highlight2 == "Rockstar Games"

    text3 = "The story returns players to the neon streets of Vice City."
    highlight3 = select_grounded_highlight(text3, max_words=3)
    assert highlight3 == "Vice City"

    text4 = "In February 2011 Stephen Elop issued the burning platform memo."
    highlight4 = select_grounded_highlight(text4, max_words=3)
    assert highlight4 == "Stephen Elop"


def test_negation_preservation():
    text1 = "A metropolitan sprawl that never sleeps through the night."
    highlight1 = select_grounded_highlight(text1)
    assert "never sleeps" in highlight1.lower()

    text2 = "The market collapsed without warning on Monday morning."
    highlight2 = select_grounded_highlight(text2)
    assert "without warning" in highlight2.lower()

    # Negation tokens survive clean_highlight_tokens when modifying meaningful words
    tokens = clean_highlight_tokens("never sleeps")
    assert "never" in [t.lower() for t in tokens]
    assert "sleeps" in [t.lower() for t in tokens]


def test_number_and_unit_pairs():
    text1 = "Analysts estimated the development budget at $2 billion."
    highlight1 = select_grounded_highlight(text1)
    assert "$2 billion" in highlight1

    text2 = "Nokia lost 14 percent of its valuation in a single trading session."
    highlight2 = select_grounded_highlight(text2)
    assert "14 percent" in highlight2

    text3 = "The trailer accumulated 90 million views within 24 hours."
    highlight3 = select_grounded_highlight(text3)
    assert any(unit in highlight3 for unit in ("million", "hours", "views"))


def test_stopwords_never_become_standalone_highlights():
    for word in ("the", "and", "is", "about", "which", "there", "because"):
        assert word in STOPWORDS
        tokens = clean_highlight_tokens(f"{word} {word}")
        assert tokens == []


def test_align_highlight_with_whisper_exact_boundaries():
    whisper_words = [
        {"word": "Fans", "start": 1.0, "end": 1.3},
        {"word": "waited", "start": 1.35, "end": 1.7},
        {"word": "for", "start": 1.72, "end": 1.85},
        {"word": "Grand", "start": 1.9, "end": 2.2},
        {"word": "Theft", "start": 2.22, "end": 2.45},
        {"word": "Auto", "start": 2.48, "end": 2.75},
        {"word": "VI", "start": 2.78, "end": 3.1},
        {"word": "worldwide", "start": 3.15, "end": 3.6},
    ]

    start, end = align_highlight_with_whisper("Grand Theft Auto VI", whisper_words, scene_start=0.0)
    assert pytest.approx(start, 0.01) == 1.9
    assert pytest.approx(end, 0.01) == 3.1

    # Fallback to scene_start when phrase is missing
    fallback_start, fallback_end = align_highlight_with_whisper("Unknown Entity", whisper_words, scene_start=5.0)
    assert fallback_start == 5.0
    assert fallback_end == 7.5


def test_no_hallucinated_words_in_highlight():
    script_text = "Ocean Drive hosts the densest gathering of supercars in Leonida."
    highlight = select_grounded_highlight(script_text)
    script_words = {w.lower().strip(".,!?:") for w in script_text.split()}
    highlight_words = [w.lower().strip(".,!?:") for w in highlight.split()]

    for hw in highlight_words:
        assert hw in script_words, f"Highlight word '{hw}' was not in the original script"
