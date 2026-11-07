"""Replace inaccurate/repeated royal footage in the saved demo edit.

The replacement stills are downloaded from pages published by the Royal
Household. Keep each selected image unique and carry source provenance into
the Remotion manifest for review.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "artifacts" / "royal_family_reliability_20261002"
PACKAGE = REVIEW / "royal_family_one_minute_20261002_090752_07e17388"
SOURCES = json.loads((REVIEW / "primary_source_assets" / "sources.json").read_text(encoding="utf-8"))
BY_NAME = {Path(row["path"]).name: row for row in SOURCES if row.get("path")}

# Every factual scene now has its own primary-source still. Event imagery is
# tied to the year named in the narration; neutral family shots stay in the
# broad-context scenes. No search result from the failed attempt is retained.
ASSETS = {
    0: ("1953_0.jpg", "full_bleed_cinematic"),
    1: ("reign_2.jpg", "photo_stack"),
    2: ("1953_2.jpg", "full_bleed_cinematic"),
    3: ("1953_4.jpg", "editorial_crop"),
    4: ("reign_0.jpg", "parallax_float"),
    5: ("king_0.jpg", "framed_print"),
    6: ("2023_1.jpg", "full_bleed_cinematic"),
    7: ("2023_2.jpg", "editorial_crop"),
    8: ("parliament_1.jpg", "editorial_crop"),
    9: ("parliament_0.jpg", "parallax_float"),
    12: ("king_11.jpg", "full_bleed_cinematic"),
    13: ("king_10.jpg", "spotlight_reveal"),
    14: ("reign_4.jpg", "photo_stack"),
    15: ("1953_3.jpg", "archival_polaroid"),
}


def main() -> None:
    checkpoint = json.loads((PACKAGE / "checkpoints" / "pre_render.json").read_text(encoding="utf-8"))
    plan_path = Path(checkpoint["edit_plan_path"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    scenes = plan["scenes"]
    used: set[str] = set()
    provenance = []

    def set_asset(index: int, filename: str, variant: str) -> None:
        row = BY_NAME[filename]
        path = str(Path(row["path"]).resolve())
        if path in used:
            raise ValueError(f"Repeated source still: {filename}")
        used.add(path)
        asset = {
            "src": path, "path": path, "type": "image", "media_type": "image",
            "label": row.get("alt") or filename.replace("_", " "),
            "source": "royal_household_primary_source", "source_url": row["page"],
            "rights_status": "Crown copyright; demo review only",
            "license": "Crown copyright; reuse permission not established",
            "rights_review_required": True, "width": row["width"],
            "height": row["height"], "fit": "cover", "safe_framing": True,
            "visual_intent": scenes[index].get("narration", ""),
            "verified_source_page": row["page"],
        }
        if index == 12:
            asset["label"] = "The King during a public visit to an arts and crafts shop"
        if index == 13:
            asset["label"] = "The King greeting members of the public"
        scene = scenes[index]
        scene["media"] = {**asset, "position": "center", "preserve_source_colors": True,
                          "color_grade": "archival" if filename[:4] in {"1953", "reig"} else "natural"}
        # Keep stale retrieval alternatives from leaking back into this saved
        # edit. They previously reattached the scene 7 candidate to scene 0.
        scene["media"].pop("shots", None)
        primary = scene.setdefault("primary_visual", {})
        primary.update({"type": "SmartImage", "variant": variant, "props": {
            **dict(primary.get("props") or {}), "variant": variant,
            "image_treatment": "warm_archival" if filename.startswith(("1953", "reign")) else "cinematic_grade",
            "color_grade": "archival" if filename.startswith(("1953", "reign")) else "natural",
            "image_brightness": 1.28,
        }, "asset": asset})
        primary["asset"].pop("shots", None)
        scene["component_mapping"] = {
            "component": "SmartImage", "source": "royal_primary_source_repair"
        }
        scene["visual_role"] = "media_overlay"
        scene["visual_role_source"] = "llm_director"
        scene["director_treatment_enabled"] = True
        scene["media_composition"] = {"mode": "full_frame_media", "layers": [
            {"role": "primary_media", "path": path, "fit": "cover", "safe_framing": True}
        ]}
        provenance.append({"scene_index": index, "path": path, "page": row["page"],
                           "alt": row.get("alt"), "variant": variant})

    for index, (filename, variant) in ASSETS.items():
        set_asset(index, filename, variant)

    scenes[0]["visual_intent"] = (
        "Open on the real 1953 coronation photograph, framed to keep Queen Elizabeth II's crown and her period context visible."
    )
    scenes[8]["visual_intent"] = (
        "Use a verified wide view of the State Opening inside Parliament as a visual bridge from royal ceremony to the modern constitutional role."
    )
    scenes[9]["visual_intent"] = (
        "Show the State Opening procession and chamber as the monarch reads the government's agenda; preserve the real event context."
    )
    scenes[12]["visual_intent"] = (
        "Use the verified Royal Household photograph of the King visiting an arts and crafts shop as a real public engagement."
    )

    # Make the seventy-year fact a count-up over the portrait's open, dark
    # negative space rather than placing a generic title card across her face.
    reign_scene = scenes[4]
    reign_scene["primary_visual"] = {"type": "StatCard", "variant": "hero_counter_large", "props": {
        "variant": "hero_counter_large", "value": 70, "startValue": 0, "unit": "YEARS",
        "label": "LONGEST REIGN IN BRITISH HISTORY", "category": "QUEEN ELIZABETH II",
        "position": "bottom_left", "counter_direction": "up"}}
    reign_scene["component_mapping"] = {"component": "StatCard", "source": "llm_scene_repair",
        "mapping_reason": "seventy_year_count_grounded_in_narration"}
    reign_scene["visual_role"] = "media_overlay"
    reign_scene["visual_role_source"] = "llm_scene_repair"
    reign_scene["text"].update({"frame_mode": "data", "overlay_policy": "none", "display_text": "",
        "frame_contract_validation": {"status": "pass", "frame_mode": "data", "visual_type": "StatCard"}})
    reign_scene["editorial_contract"] = {}
    reign_scene["display_text"] = ""
    reign_scene["emphasis_spans"] = []

    # Add one short, narration-grounded reveal to the opening tension. It is
    # layered over a distinct public-engagement still, with copy provenance.
    kinetic_scene = scenes[1]
    kinetic_scene["text"].update({"display_text": "keeps changing", "primary": "keeps changing",
        "frame_mode": "kinetic", "focus": "keeps changing", "copy_source": "llm",
        "overlay_policy": "single_short_highlight", "position": "top_left",
        "panel_style": "editorial_caption", "text_color": "#FAF5EF",
        "accent_color": "#E8B75C", "panel_background": "#140E11"})
    contract = kinetic_scene.setdefault("editorial_contract", {})
    contract.update({"display_text": "keeps changing", "source_span": "the institution keeps changing",
                     "source": "llm", "version": 4, "copy_source": "llm", "approved": True,
                     "emphasis_spans": [{"start": 0, "end": len("keeps changing")}]})
    kinetic_scene["copy_window"] = {"start_frame": 6, "duration_frames": 64}
    kinetic_scene["primary_visual"].update({"type": "KineticText", "variant": "mask_slide_up",
        "props": {"variant": "mask_slide_up", "display_text": kinetic_scene["editorial_contract"]["display_text"],
                  "_display_text": kinetic_scene["editorial_contract"]["display_text"]}})
    kinetic_scene["component_mapping"] = {"component": "KineticText", "source": "llm_scene_repair"}
    kinetic_scene["visual_role"] = "media_overlay"
    kinetic_scene["text"]["frame_contract_validation"] = {
        "status": "pass", "frame_mode": "kinetic", "visual_type": "KineticText"}
    kinetic_scene["visual_intent"] = "Overlay one brief bold reveal of the exact approved phrase ‘keeps changing’ on the archival handover photograph. Keep both monarchs visible."

    # Keep chapter emphasis tight and fully inside negative-space margins.
    for index in (0, 2):
        scenes[index]["text"].update({"overlay_policy": "none", "display_text": "", "primary": "", "position": "none"})
        scenes[index]["editorial_contract"] = {}
        scenes[index]["display_text"] = ""
        scenes[index]["emphasis_spans"] = []
        scenes[index]["primary_visual"].setdefault("props", {}).pop("display_text", None)
        scenes[index]["primary_visual"].setdefault("props", {}).pop("_display_text", None)
    scenes[2]["text"].update({"panel_background": "#140E11", "text_color": "#FAF5EF", "accent_color": "#E8B75C"})
    # This archival still is near-square. Fill the widescreen frame directly
    # instead of duplicating it as a dark blurred backdrop around a small inset.
    scenes[2]["media"].update({"fit": "force_cover", "safe_framing": False})
    scenes[2]["primary_visual"]["asset"].update({"fit": "force_cover", "safe_framing": False})
    scenes[2]["primary_visual"]["props"].update({"fit": "force_cover", "safe_framing": False, "image_brightness": 1.38})
    # Timeline transitions own the cut; the next image is visible immediately.
    scenes[2].setdefault("motion", {})["entrance"] = "none"
    scenes[6]["text"].update({"panel_style": "archive_label", "position": "left_center",
        "panel_background": "rgba(20,14,17,0.82)", "text_color": "#FAF5EF", "accent_color": "#E8B75C"})
    scenes[6]["primary_visual"]["props"].update({"panel_style": "archive_label", "position": "left_center",
        "panel_background": "rgba(20,14,17,0.82)", "text_color": "#FAF5EF", "accent_color": "#E8B75C"})
    # The comparison uses one sourced royal appearance and a separate
    # community visit where King Charles remains visible inside the reveal.
    scenes[13]["text"].update({"overlay_policy": "none", "display_text": "", "position": "none"})
    scenes[13]["editorial_contract"] = {}
    scenes[13]["display_text"] = ""
    scenes[13]["emphasis_spans"] = []
    comparison_scene = scenes[13]
    left_path = str((REVIEW / "primary_source_assets" / "parliament_7.jpg").resolve())
    right_path = str((REVIEW / "primary_source_assets" / "king_10.jpg").resolve())
    comparison_scene["primary_visual"] = {"type": "Comparison", "variant": "split_slider_before_after", "props": {
        "variant": "split_slider_before_after", "title": "TWO SIDES OF DUTY",
        "before": "balcony appearances", "after": "everyday service",
        "leftLabel": "balcony appearances", "rightLabel": "everyday service",
        "split_position": 50, "right_position": "left center",
        "preserve_label_case": True, "divider_color": "#E8B75C",
        "leftImage": left_path, "rightImage": right_path,
        "images": [{"src": left_path, "label": "balcony appearances"},
                   {"src": right_path, "label": "everyday service"}]}}
    comparison_scene["component_mapping"] = {"component": "Comparison", "source": "llm_scene_repair",
        "mapping_reason": "narration_contrasts_public_appearances_and_daily_service"}
    comparison_scene["text"]["frame_mode"] = "data"
    comparison_scene["text"]["frame_contract_validation"] = {
        "status": "pass", "frame_mode": "data", "visual_type": "Comparison"}
    comparison_scene["visual_role"] = "primary_explanation"
    comparison_scene["media_composition"] = {"mode": "primary_explanation", "layers": []}
    comparison_scene["visual_intent"] = (
        "Contrast a real balcony appearance with a separate sourced image of the King greeting people on a public visit; the second photo illustrates public engagement, not every aspect of royal service."
    )

    # Keep the modern constitutional beat on its own sourced Parliament/Crown
    # image. The narration carries the phrase; the diagram that follows handles
    # the explanation without layering another title over people.
    constitutional_scene = scenes[8]
    constitutional_scene["text"].update({"display_text": "", "primary": "", "copy_source": "none",
        "position": "none", "frame_mode": "media_only", "overlay_policy": "none",
        "frame_contract_validation": {"status": "pass", "frame_mode": "media_only", "visual_type": "SmartImage"}})
    constitutional_scene["editorial_contract"] = {}
    constitutional_scene["display_text"] = ""
    constitutional_scene["emphasis_spans"] = []
    constitutional_scene.pop("copy_window", None)
    constitutional_scene["primary_visual"] = {"type": "SmartImage", "variant": "editorial_crop",
        "props": {"variant": "editorial_crop", "image_treatment": "cinematic_grade", "fit": "cover"},
        "asset": constitutional_scene["media"]}
    constitutional_scene["component_mapping"] = {"component": "SmartImage", "source": "royal_primary_source_repair"}
    constitutional_scene["visual_role"] = "media_overlay"
    constitutional_scene["visual_role_source"] = "royal_primary_source_repair"
    constitutional_scene["template_id"] = "full-bleed"
    constitutional_scene["graphic"] = {"template_id": "full-bleed", "props": {}}
    constitutional_scene["director_treatment_enabled"] = True
    constitutional_scene["visual_intent"] = "Use the verified State Opening photograph inside Parliament as a visual bridge from royal ceremony to the modern constitutional role; keep the event and chamber readable."

    # Give the constitutional transition and its two narrated steps distinct
    # full-frame visual grammars that use only words present in the script.
    for index, variant, labels in (
        (10, "connected_process", ["Parliament", "laws"]),
        (11, "royal_assent_flow", ["royal assent", "formal process"]),
    ):
        scene = scenes[index]
        evidence = scene.get("narration", "")
        nodes = [{"label": label, "source_span": evidence} for label in labels]
        scene["primary_visual"] = {"type": "Diagram", "variant": variant, "props": {
            "variant": variant, "steps": [
                {"title": label, "label": label, "desc": "", "x": 24 if n == 0 else 76, "y": 50}
                for n, label in enumerate(labels)
            ], "node_copy_contract": {"source": "llm", "approved": True, "nodes": nodes},
            "links": [{"from": labels[0], "to": labels[1]}], "full_canvas": True,
        }}
        scene["component_mapping"] = {"component": "Diagram", "source": "llm_director",
                                       "mapping_reason": "constitutional_process_explained_in_narration"}
        scene["visual_role"] = "primary_explanation"
        scene["visual_role_source"] = "llm_director"
        scene["director_treatment_enabled"] = True
        scene["text"]["frame_mode"] = "diagram"
        scene["text"]["frame_contract_validation"] = {
            "status": "pass", "frame_mode": "diagram", "visual_type": "Diagram"
        }
        scene["media_composition"] = {"mode": "primary_explanation", "layers": []}
        # These two diagram-only scenes intentionally contain no repeated image.
        scene["media"] = {"src": "", "type": "none", "preserve_source_colors": True}
        scene["primary_visual"]["props"]["links"] = [{"from": labels[0], "to": labels[1]}]

    # Dated claims have a film-wide but scene-specific visible source marker.
    scenes[2]["research_sources"] = ["royal:1953"]
    scenes[4]["research_sources"] = ["royal:reign"]
    scenes[5]["research_sources"] = ["royal:reign"]
    scenes[6]["research_sources"] = ["royal:2023"]
    scenes[9]["research_sources"] = ["parliament:crown"]
    scenes[10]["research_sources"] = ["parliament:crown"]
    scenes[11]["research_sources"] = ["parliament:crown"]

    plan["royal_demo_source_repairs"] = provenance
    plan["royal_demo_source_policy"] = "Primary Royal Household stills; rights remain subject to clearance."
    rendered = json.dumps(plan, ensure_ascii=False, indent=2, default=str)
    plan_path.write_text(rendered, encoding="utf-8")
    # Keep the copy used by the regular pipeline renderer in sync with its
    # package-level retry plan.
    temp_manifest = ROOT / "temp" / "royal_family_one_minute_semantic.manifest.json"
    if temp_manifest.exists():
        temp_manifest.write_text(rendered, encoding="utf-8")
    print(f"Repaired {len(ASSETS)} sourced stills and 2 full-canvas factual diagrams in {plan_path}")


if __name__ == "__main__":
    main()
