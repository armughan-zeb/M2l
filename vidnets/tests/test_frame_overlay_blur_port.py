"""Regression coverage for the offline Blurrr frame-cleanup port."""

from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def _write_caption(path: Path) -> None:
    image = np.full((240, 420, 3), 48, dtype=np.uint8)
    # Textured background makes the outside-of-mask assertion meaningful.
    for y in range(image.shape[0]):
        image[y, :, 0] = (30 + y) % 255
        image[y, :, 1] = (70 + y * 2) % 255
    cv2.putText(
        image, "DOCUMENTARY CAPTION", (55, 216), cv2.FONT_HERSHEY_SIMPLEX,
        0.65, (245, 245, 245), 2, cv2.LINE_AA,
    )
    assert cv2.imwrite(str(path), image)


def test_blurrr_port_uses_polygon_masks_and_preserves_outside(tmp_path):
    from services.scene_understanding.frame_overlay_blur import apply_gaussian_overlay_blur

    source = tmp_path / "source.png"
    output = tmp_path / "cleaned.png"
    _write_caption(source)
    original = cv2.imread(str(source), cv2.IMREAD_COLOR)
    regions = [{
        "type": "caption",
        "bbox": [0.12, 0.82, 0.72, 0.93],
        "polygon": [[0.13, 0.845], [0.67, 0.845], [0.67, 0.905], [0.13, 0.905]],
        "polygons": [[[0.13, 0.845], [0.67, 0.845], [0.67, 0.905], [0.13, 0.905]]],
        "confidence": 92,
    }]
    assert apply_gaussian_overlay_blur(str(source), regions, output_path=str(output)) == str(output)
    result = cv2.imread(str(output), cv2.IMREAD_COLOR)
    assert result is not None and original is not None
    # The untouched upper half must remain exactly unchanged: no full-frame
    # blur, no background pinching, and no accidental crop compositing.
    assert np.array_equal(result[:170], original[:170])
    assert float(result[198:232, 40:380].std()) < float(original[198:232, 40:380].std())


def test_rapidocr_detector_geometry_is_admitted_without_recognition(monkeypatch, tmp_path):
    import services.scene_understanding.frame_overlay_blur as module

    source = tmp_path / "ocr.png"
    _write_caption(source)

    class FakePost:
        box_thresh = 0.4
        unclip_ratio = 1.6

    class FakeDetector:
        postprocess_op = FakePost()

        def __call__(self, image):
            return np.array([[[55, 198], [300, 198], [300, 220], [55, 220]]], dtype=np.float32), 0.001

    class FakeEngine:
        text_detector = FakeDetector()

    monkeypatch.setattr(module, "_RAPIDOCR_ENGINE", FakeEngine())
    regions = module.detect_overlay_regions(str(source), confidence_threshold=0.4)
    captions = [item for item in regions if item.get("type") == "caption"]
    assert captions
    assert captions[0].get("method") == "rapidocr_dbnet_polygon"
    assert len(captions[0].get("polygon") or []) >= 4
    assert captions[0]["bbox"][1] > 0.55
