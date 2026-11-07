from pathlib import Path

import create_product_folder as packaging
from server.services.media_asset_policy import emergency_media_allowed


def test_distribution_keeps_runtime_defaults_and_editorial_sound_effects():
    assert ".env.defaults" in packaging.ROOT_FILES
    sample = Path("public") / "sound-effects" / "edit" / "edit_hit_dry.wav"
    assert packaging.is_excluded_file(sample) is False
    assert packaging._matches_forbidden_package_path(Path("resources/.env.defaults")) is False


def test_windows_distribution_contains_the_complete_remotion_runtime():
    import json

    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    resources = package["build"]["extraResources"]
    resource_paths = {(item.get("from"), item.get("to")) for item in resources}

    assert ("src/", "src/") in resource_paths
    assert ("remotion/", "remotion/") in resource_paths
    assert ("scripts/render-remotion-manifest.js", "scripts/render-remotion-manifest.js") in resource_paths
    assert ("scripts/capture-source-evidence.js", "scripts/capture-source-evidence.js") in resource_paths
    assert ("tools/node/", "tools/node/") in resource_paths
    assert ("node_modules/", "node_modules/") in resource_paths
    assert ("node_modules/.remotion/", "node_modules/.remotion/") in resource_paths
    assert "VIDZEO_NODE_PATH" in (root / "main.js").read_text(encoding="utf-8")
    assert "VIDZEO_NODE_PATH" in (root / "START.bat").read_text(encoding="utf-8")
    wrapper = (root / 'scripts' / 'create-clean-working-folder.ps1').read_text(encoding='utf-8')
    delivery = (root / 'scripts' / 'create-original-working-folder.ps1').read_text(encoding='utf-8')
    assert 'create-original-working-folder.ps1' in wrapper
    assert "'src'" in delivery
    assert 'Copy-SourceTree $Relative' in delivery
    assert ('tools/models/', 'tools/models/') in resource_paths


def test_first_run_probe_installs_the_local_portrait_cutout_runtime():
    root = Path(__file__).resolve().parents[1]
    main_source = (root / "main.js").read_text(encoding="utf-8")
    bootstrap_source = (root / "scripts" / "bootstrap-runtime.ps1").read_text(encoding="utf-8")
    requirements = (root / "server" / "requirements.txt").read_text(encoding="utf-8")

    assert "'rembg'" in main_source
    assert "'rembg'," in bootstrap_source
    assert 'rembg[cpu]==2.0.69; python_version < "3.11"' in requirements
    assert 'rembg[cpu]>=2.0.83,<3; python_version >= "3.11"' in requirements
    assert "['-I', '-c', probe]" in main_source
    assert '& $PythonExe -I $checkPath' in bootstrap_source
    assert '@("-I", "-m", "pip", "install"' in bootstrap_source


def test_distribution_keeps_opencv_runtime_and_wheel_metadata():
    assert packaging.is_selected_site_package(Path("cv2")) is True
    assert packaging.is_selected_site_package(Path("opencv_python_headless-4.11.0.86.dist-info")) is True
    assert packaging.is_selected_site_package(Path("rapidocr_onnxruntime")) is True
    assert packaging.is_selected_site_package(Path("rapidocr_onnxruntime-1.4.4.dist-info")) is True
    assert packaging.is_selected_site_package(Path("pyclipper")) is True
    assert packaging.is_selected_site_package(Path("shapely")) is True
    assert packaging.is_selected_site_package(Path("shapely.libs")) is True


def test_emergency_synthetic_visuals_are_disabled_in_production_defaults():
    defaults = Path(".env.defaults").read_text(encoding="utf-8")
    assert "VIDZEO_ALLOW_EMERGENCY_MEDIA=0" in defaults
    assert emergency_media_allowed({"allow_emergency_media": False}, default=True) is False
