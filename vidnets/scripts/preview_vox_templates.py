"""Preview all 35+ Vox templates -- generates a showcase MP4.

Each template gets 2.5 seconds with sample props matching LLM output style.
"""

import sys, os, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
os.makedirs(ROOT / "output", exist_ok=True)

from services.native_engine.libraries.vox_templates import VOX_TEMPLATES

# Sample palette (crime niche)
PALETTE = {
    "background": "#0a0a12",
    "surface": "#16263b",
    "primary": "#f4f0e6",
    "accent": "#f2c14e",
    "secondary": "#49a6d8",
    "danger": "#ef6f6c",
}

# Sample props per template — these match what the LLM would generate
SAMPLE_PROPS = {
    "cold_open": {"kicker": "THE QUESTION", "title": "WHAT REALLY HAPPENED?", "subtitle": "New evidence casts doubt"},
    "kinetic_statement": {"text": "THE SYSTEM WAS BROKEN", "detail": "Decades of oversight failure"},
    "word_cascade": {"text": "TOO FAST TOO LATE", "detail": "The central conclusion"},
    "keyword_reveal": {"text": "BREAKTHROUGH", "detail": "A key discovery that changed everything"},
    "typewriter_reveal": {"text": "CLASSIFIED", "label": "DOCUMENT"},
    "split_statement": {"line1": "THE SHIP", "line2": "WAS NOT PREPARED", "detail": "A key finding"},
    "stat_counter": {"value": "8,473", "label": "DOCUMENTS DECLASSIFIED"},
    "money_counter": {"value": "24000000", "label": "UNTRACEABLE FUNDS", "unit": "M"},
    "percentage_gauge": {"value": "73", "label": "OF THE TOTAL"},
    "metric_grid": {"value": "3,241", "label": "TOTAL CASES", "detail": "Pending review"},
    "bar_chart": {"label": "ANNUAL BUDGET", "items": [{"label": "2020", "value": 82}, {"label": "2021", "value": 61}, {"label": "2022", "value": 39}]},
    "ranked_list": {"title": "KEY FINDINGS", "items": ["CLASSIFIED DOCUMENTS", "WITNESS TESTIMONY", "FINANCIAL TRAIL"]},
    "evidence_callout": {"stamp": "EVIDENCE", "text": "THE RECORDS DID NOT MATCH", "detail": "Flight logs contradicted"},
    "redaction_reveal": {"text": "THE DETAIL THAT CHANGED EVERYTHING", "label": "RESTRICTED FILE"},
    "case_file": {"title": "THE MISSING WARNING", "detail": "CASE FILE 04", "stamp": "CLASSIFIED"},
    "source_citation": {"text": "A critical finding that changed the investigation.", "source": "NATIONAL ARCHIVES"},
    "stamp_mark": {"text": "DECLASSIFIED", "label": "TOP SECRET"},
    "date_reveal": {"kicker": "HISTORIC DATE", "date": "JULY 20, 1969"},
    "calendar_rip": {"date": "APRIL 15", "kicker": "THE NIGHT", "event": "THE SINKING"},
    "timeline_trace": {"label": "KEY EVENTS", "items": [{"label": "1973", "text": "Oil Crisis"}, {"label": "1979", "text": "Revolution"}, {"label": "1989", "text": "Wall Falls"}]},
    "quote_card": {"quote": "One small step for a man, one giant leap for mankind.", "attribution": "NEIL ARMSTRONG"},
    "witness_quote": {"quote": "I saw the documents myself.", "attribution": "FORMER ANALYST"},
    "cause_effect": {"cause": "DEREGULATION", "effect": "MARKET CRASH", "label": "CAUSE → EFFECT"},
    "before_after": {"before_label": "BEFORE", "before_value": "$0", "after_label": "AFTER", "after_value": "$1M"},
    "split_compare": {"left_label": "USA", "right_label": "USSR", "left_value": "12", "right_value": "1", "label": "MOON LANDINGS"},
    "location_label": {"location": "NORTH ATLANTIC", "detail": "APRIL 1912"},
    "map_route": {"from": "NEW YORK", "to": "LOS ANGELES", "label": "ROUTE"},
    "end_card": {"title": "FOLLOW THE STORY", "subtitle": "More revelations coming"},
    "cta_card": {"title": "SUBSCRIBE", "subtitle": "For more stories like this"},
    "field_caption": {"title": "NORTH ATLANTIC", "subtitle": "APRIL 1912"},
    "exhibit_label": {"title": "RMS TITANIC", "subtitle": "ARCHIVE OBJECT"},
    "newsprint_reveal": {"headline": "THE WARNING WAS ALREADY THERE", "deck": "ARCHIVE REPORT"},
    "countdown_clock": {"value": "T-30", "label": "SECONDS TO IMPACT"},
    "signal_scan": {"message": "DISTRESS SIGNAL DETECTED", "label": "RADIO INTERCEPT"},
    "index_card": {"title": "WIRELESS WARNING", "detail": "Filed before the collision."},
    "profile_dossier": {"title": "JAMES DONOVAN", "subtitle": "NEGOTIATOR", "detail": "Brooklyn-born lawyer"},
}


def main():
    from services.native_engine.vox_composer import VoxComposer

    scenes = []
    for tid, template_fn in VOX_TEMPLATES.items():
        props = SAMPLE_PROPS.get(tid, {"text": tid.upper().replace("_", " ")})
        # Add font info
        props["_font"] = "BebasNeue-Regular.ttf"
        props["_body_font"] = "Poppins-Bold.ttf"

        scenes.append({
            "duration": 2.5,
            "background_asset": "",  # will generate placeholder
            "template_id": tid,
            "props": props,
            "palette": PALETTE,
            "image_treatment": "static_hold",
            "transition": {"id": "fadeblack", "duration": 0.3},
        })

    output = str(ROOT / "output" / "vox_templates_preview.mp4")
    print(f"\n[preview] Rendering {len(scenes)} Vox templates -> {output}")
    start = time.monotonic()

    composer = VoxComposer(width=1920, height=1080, fps=30)
    result = composer.render({"scenes": scenes}, output)

    elapsed = time.monotonic() - start
    print(f"[preview] Done in {elapsed:.0f}s -- {result['size_mb']} MB")
    print(f"[preview] Output: {result['output_path']}")
    return result


if __name__ == "__main__":
    main()
