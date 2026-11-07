from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path

from PIL import Image

from server.services import person_cutout_service as cutouts


def _manifest(source: Path):
    return {
        "scenes": [{
            "scene_index": 0,
            "primary_visual": {
                "type": "PersonProfile",
                "props": {"name": "A named person"},
                "asset": {"src": str(source)},
            },
            "media": {"src": str(source), "type": "image"},
        }]
    }


def test_worker_adds_a_real_transparent_png_and_reuses_one_session(tmp_path, monkeypatch):
    source = tmp_path / "portrait.png"
    Image.effect_noise((512, 512), 48).convert("RGB").save(source)
    target = tmp_path / "cutout.png"
    calls = {"session": 0, "remove": 0}

    def new_session(model):
        calls["session"] += 1
        assert model == "u2net_human_seg"
        return object()

    def remove(image, session, post_process_mask):
        calls["remove"] += 1
        assert image.mode == "RGB"
        assert post_process_mask is True
        output = image.convert("RGBA")
        alpha = Image.new("L", output.size, 0)
        alpha.paste(255, (80, 0, 432, 512))
        output.putalpha(alpha)
        return output

    monkeypatch.setitem(sys.modules, "rembg", types.SimpleNamespace(new_session=new_session, remove=remove))
    result = cutouts._worker_remove([{"source": str(source), "target": str(target)}], "u2net_human_seg")

    assert result[str(source)] == str(target)
    assert calls == {"session": 1, "remove": 1}
    with Image.open(target) as generated:
        assert generated.mode == "RGBA"
        assert generated.getchannel("A").getextrema() == (0, 255)


def test_existing_portrait_cutout_is_attached_without_loading_the_model(tmp_path, monkeypatch):
    output = tmp_path / "package"
    media = output / "media"
    media.mkdir(parents=True)
    source = media / "portrait.png"
    Image.new("RGB", (64, 80), (20, 50, 90)).save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:24]
    target = media / "person-cutouts" / f"{digest}-u2net_human_seg.png"
    target.parent.mkdir(parents=True)
    Image.new("RGBA", (64, 80), (20, 50, 90, 200)).save(target)

    def unexpected(*args, **kwargs):
        raise AssertionError("cached cutout should avoid the model worker")

    monkeypatch.setattr(cutouts.subprocess, "run", unexpected)
    result = cutouts.hydrate_person_cutouts(_manifest(source), output)
    scene = result["scenes"][0]
    assert scene["primary_visual"]["props"]["cutout_src"] == str(target)
    assert scene["primary_visual"]["asset"]["cutout_src"] == str(target)


def test_verified_group_profile_skips_single_person_matting(tmp_path, monkeypatch):
    source = tmp_path / 'group.png'
    Image.new('RGB', (128, 80), (20, 50, 90)).save(source)
    manifest = _manifest(source)
    props = manifest['scenes'][0]['primary_visual']['props']
    props.update(people=[{'label': 'Person One'}, {'label': 'Person Two'}],
                 people_copy_contract={'approved': True})
    def unexpected(*args, **kwargs):
        raise AssertionError('group photos must retain all people without a matting worker')
    monkeypatch.setattr(cutouts.subprocess, 'run', unexpected)
    result = cutouts.hydrate_person_cutouts(manifest, tmp_path)
    assert result['scenes'][0]['primary_visual']['asset']['src'] == str(source)
    assert not result['scenes'][0]['primary_visual']['props'].get('cutout_src')


def test_model_timeout_keeps_the_original_portrait_and_does_not_poison_manifest(tmp_path, monkeypatch):
    output = tmp_path / "package"
    media = output / "media"
    media.mkdir(parents=True)
    source = media / "portrait.png"
    Image.new("RGB", (64, 80), (20, 50, 90)).save(source)

    def timed_out(*args, **kwargs):
        raise cutouts.subprocess.TimeoutExpired(args[0], kwargs.get("timeout") or 1)

    monkeypatch.setattr(cutouts.subprocess, "run", timed_out)
    manifest = _manifest(source)
    result = cutouts.hydrate_person_cutouts(manifest, output, timeout_seconds=3)
    assert "cutout_src" not in result["scenes"][0]["primary_visual"]["props"]
    assert "cutout_src" not in manifest["scenes"][0]["primary_visual"]["props"]


def test_worker_uses_file_entrypoint_for_isolated_bundled_python(tmp_path, monkeypatch):
    source = tmp_path / "portrait.png"
    Image.new("RGB", (64, 80), (20, 50, 90)).save(source)
    output = tmp_path / "package"
    captured = {}

    def completed(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        return cutouts.subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(cutouts.subprocess, "run", completed)
    cutouts.hydrate_person_cutouts(_manifest(source), output)

    command = captured["command"]
    assert command[0] == sys.executable
    assert Path(command[1]) == Path(cutouts.__file__).resolve()
    assert command[2] == "--worker"
    assert Path(captured["cwd"]).name == "server"
    assert captured["timeout"] == 12


def test_non_portrait_scenes_never_start_segmentation(tmp_path, monkeypatch):
    manifest = {"scenes": [{"primary_visual": {"type": "Chart", "props": {}}}]}

    def unexpected(*args, **kwargs):
        raise AssertionError("non-portrait scene should not start segmentation")

    monkeypatch.setattr(cutouts.subprocess, "run", unexpected)
    assert cutouts.hydrate_person_cutouts(manifest, tmp_path) == manifest
