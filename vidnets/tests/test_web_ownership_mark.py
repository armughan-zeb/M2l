from __future__ import annotations

from PIL import Image

from server.services.web_image.ownership_mark import _is_ownership_mark, inspect_ownership_mark


def test_ocr_ownership_text_classification():
    middle = [[350, 250], [650, 250], [650, 280], [350, 280]]
    corner = [[800, 860], [980, 860], [980, 890], [800, 890]]
    assert _is_ownership_mark("Copyrightjm/pytphotography", middle, 1000, 1000)
    assert _is_ownership_mark("touring.com", corner, 1000, 1000)
    assert not _is_ownership_mark("museum.com", middle, 1000, 1000)


def test_plain_publisher_footer_is_not_confused_with_subject_inscriptions():
    footer = [[400, 970], [950, 970], [950, 990], [400, 990]]
    assert _is_ownership_mark("WHERE ARE THOSE MORGANS", footer, 1000, 1000)
    assert _is_ownership_mark("WHERE ARETHOSE MORGANS", [[340,950],[660,950],[660,970],[340,970]], 1000,1000)
    assert not _is_ownership_mark("FUSHIMI INARI SHRINE", [[200,400],[600,400],[600,440],[200,440]], 1000,1000)


def test_ocr_ownership_probe_rejects_marked_image(monkeypatch, tmp_path):
    import server.services.scene_understanding.frame_overlay_blur as blur

    path = tmp_path / "marked.png"
    Image.new("RGB", (1000, 1000), (80, 80, 80)).save(path)

    class FakeOCR:
        def __call__(self, _pixels):
            return [
                ([[800, 860], [980, 860], [980, 890], [800, 890]], "touring.com", "0.91")
            ], [0.1]

    monkeypatch.setattr(blur, "_get_rapidocr", lambda: FakeOCR())
    result = inspect_ownership_mark(str(path))
    assert result["checked"] is True
    assert result["watermark"] is True
    assert result["reason"] == "ownership_text_ocr"
