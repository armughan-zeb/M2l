"""Build complete, production-grade server/app.py.

Integrates:
- Base production Flask server from dist (17201 lines)
- Director functions (_is_custom_media_source, _run_director_plan, _hydrate_director_scene_media)
- Director API endpoints (/api/director/tweak, /api/director/plan, /api/director/review, /api/director/media-first, /api/director/validate)
- Editor API endpoints (/api/video/<path:filename>, _resolve_editor_media_path, _editor_resolution, _editor_float, /api/editor/media-info, /api/editor/render)
- Semantic documentary pipeline quality gate (Reviewer Council + sequence continuity audit + Manifest v4)
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DIST_APP = ROOT / "dist" / "Vidnets-Studio-Product-App" / "resources" / "server" / "app.py"
TARGET_APP = ROOT / "server" / "app.py"

def build():
    source = DIST_APP.read_text(encoding="utf-8")

    # 1. Add _is_custom_media_source if missing
    custom_media_func = '''
def _is_custom_media_source(config):
    if not isinstance(config, dict):
        return False
    return _canonical_image_source(config.get('image_source')) == 'Custom Images'
'''
    if "_is_custom_media_source" not in source:
        # Insert after _canonical_image_source
        idx = source.find("def _canonical_image_source")
        end_idx = source.find("\n\n", idx)
        source = source[:end_idx] + "\n" + custom_media_func + source[end_idx:]

    # 2. Add Director API endpoints before @app.route('/api/projects')
    director_endpoints = '''
# =============================================================================
# API - EDITORIAL DIRECTOR
# =============================================================================

@app.route('/api/director/tweak', methods=['POST'])
@require_active_access
def api_director_tweak():
    from services.editorial_director import validate_edit_plan, tweak_edit_plan
    data = request.json or {}
    raw_plan = data.get('edit_plan') or data.get('manifest')
    instruction = str(data.get('instruction') or '').strip()

    if not raw_plan or not instruction:
        return jsonify({"error": "Both 'edit_plan' and 'instruction' are required"}), 400

    try:
        plan = validate_edit_plan(raw_plan)
        tweaked = tweak_edit_plan(plan, instruction)
        return jsonify({"success": True, "edit_plan": tweaked.to_dict()})
    except Exception as exc:
        return jsonify({"error": f"Failed to apply directorial tweak: {exc}"}), 422


@app.route('/api/director/plan', methods=['POST'])
@require_active_access
def api_director_plan():
    from services.editorial_director import EditingDirector
    data = request.json or {}
    script = str(data.get('script') or '').strip()
    scene_texts = list(data.get('scene_texts') or [])
    if not scene_texts and script:
        scene_texts = [s.strip() for s in re.split(r'(?<=[.!?])\\s+', script) if s.strip()]

    if not scene_texts:
        return jsonify({"error": "Either 'script' or 'scene_texts' is required"}), 400

    scene_durations = list(data.get('scene_durations') or [])
    if not scene_durations:
        scene_durations = [max(3.0, round(len(text.split()) * 0.45, 2)) for text in scene_texts]

    media_items = list(data.get('media_items') or [])
    word_timings = list(data.get('word_timings') or [])
    topic = str(data.get('topic') or data.get('title') or '').strip()
    category = str(data.get('category') or '').strip()
    channel_brand = data.get('channel_brand')
    variation_seed = data.get('variation_seed')

    try:
        director = EditingDirector()
        plan = director.plan_documentary(
            script_text=script or " ".join(scene_texts),
            scene_texts=scene_texts,
            scene_durations=scene_durations,
            media_items=media_items,
            word_timings=word_timings,
            topic=topic,
            category=category,
            channel_brand=channel_brand,
            variation_seed=int(variation_seed) if variation_seed is not None else None,
            fps=int(data.get('fps', 30)),
            width=int(data.get('width', 1920)),
            height=int(data.get('height', 1080)),
        )
        plan_dict = plan.to_dict()
        from services.editorial_director import (
            apply_media_first_direction,
            plan_media_composition,
            audit_sequence_continuity,
            execute_reviewer_council,
        )
        plan_dict = apply_media_first_direction(plan_dict, plan_media_composition)
        plan_dict["version"] = 4
        plan_dict["sequence_audit"] = audit_sequence_continuity(plan_dict)
        plan_dict["reviewer_council"] = execute_reviewer_council(plan_dict)
        total_sfx = sum(len(s.get('sound_cues', [])) for s in plan_dict.get('scenes', []))
        return jsonify({
            "success": True,
            "edit_plan": plan_dict,
            "sound_cue_count": total_sfx,
            "reviewer_council": plan_dict["reviewer_council"],
            "sequence_audit": plan_dict["sequence_audit"],
        })
    except Exception as exc:
        return jsonify({"error": f"Failed to generate directorial edit plan: {exc}"}), 422


@app.route('/api/director/review', methods=['POST'])
@require_active_access
def api_director_review():
    from services.editorial_director import (
        apply_media_first_direction,
        plan_media_composition,
        audit_sequence_continuity,
        execute_reviewer_council,
    )
    data = request.json or {}
    raw_manifest = data.get('manifest') or data.get('edit_plan') or data
    frame_paths = data.get('frame_paths')
    try:
        manifest = apply_media_first_direction(raw_manifest, plan_media_composition)
        seq_audit = audit_sequence_continuity(manifest)
        council = execute_reviewer_council(manifest, frame_paths=frame_paths)
        return jsonify({
            "success": True,
            "status": council.get("status"),
            "ready_for_long_render": council.get("ready_for_long_render"),
            "council": council.get("council"),
            "sequence_audit": seq_audit,
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 422


@app.route('/api/director/media-first', methods=['POST'])
@require_active_access
def api_director_media_first():
    from services.editorial_director import (
        apply_media_first_direction,
        plan_media_composition,
        audit_sequence_continuity,
        execute_reviewer_council,
    )
    data = request.json or {}
    raw_manifest = data.get('manifest') or data.get('edit_plan') or data
    try:
        manifest = apply_media_first_direction(raw_manifest, plan_media_composition)
        manifest["version"] = 4
        manifest["sequence_audit"] = audit_sequence_continuity(manifest)
        manifest["reviewer_council"] = execute_reviewer_council(manifest)
        return jsonify({
            "success": True,
            "manifest": manifest,
            "sequence_audit": manifest["sequence_audit"],
            "reviewer_council": manifest["reviewer_council"],
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 422


@app.route('/api/director/validate', methods=['POST'])
@require_active_access
def api_director_validate():
    from services.editorial_director import validate_edit_plan
    data = request.json or {}
    raw_plan = data.get('edit_plan') or data.get('manifest') or data
    try:
        plan = validate_edit_plan(raw_plan)
        plan_dict = plan.to_dict()
        total_sfx = sum(len(s.get('sound_cues', [])) for s in plan_dict.get('scenes', []))
        return jsonify({
            "valid": True,
            "title": plan.title,
            "genre": plan.visual_dna.genre,
            "scene_count": len(plan.scenes),
            "sound_cue_count": total_sfx,
        })
    except Exception as exc:
        return jsonify({"valid": False, "error": str(exc)}), 422
'''

    if "@app.route('/api/director/plan')" not in source:
        proj_idx = source.find("@app.route('/api/projects')")
        source = source[:proj_idx] + director_endpoints + "\n\n" + source[proj_idx:]

    # 3. Add _run_director_plan and _hydrate_director_scene_media before _render_semantic_documentary_pipeline
    director_helpers = '''
def _run_director_plan(script, scene_texts, scene_durations, title, config):
    try:
        from services.editorial_director import EditingDirector
    except Exception as exc:
        print(f"   [director] parallel plan unavailable: {type(exc).__name__}: {exc}")
        return None
    try:
        config = config if isinstance(config, dict) else {}
        var_seed = config.get("variation_seed", config.get("seed"))
        if var_seed is not None:
            try:
                variation_seed = int(var_seed)
            except (TypeError, ValueError):
                variation_seed = None
        else:
            from services.semantic_edit_engine import stable_seed
            variation_seed = stable_seed(str(title or "Video").strip(), len(scene_texts), "semantic-render")
        director = EditingDirector()
        plan = director.plan_documentary(
            script_text=str(script or " ".join(scene_texts)).strip(),
            scene_texts=list(scene_texts or []),
            scene_durations=list(scene_durations or []),
            media_items=None,
            word_timings=None,
            topic=str(title or "Video").strip(),
            category=str(config.get("category") or config.get("niche") or ""),
            channel_brand=config.get("channel_brand"),
            variation_seed=variation_seed,
            fps=30,
            editing_profile=config.get("editing_profile"),
        )
        print(f"   [director] media-independent plan ready: {len(plan.scenes)} scenes (retrieval continues in parallel)", flush=True)
        return plan
    except Exception as exc:
        print(f"   [director] parallel plan unavailable: {type(exc).__name__}: {exc}")
        return None


def _hydrate_director_scene_media(director_scenes, media_items):
    hydrated = []
    items = list(media_items or [])
    for index, scene in enumerate(director_scenes or []):
        scene = dict(scene) if isinstance(scene, Mapping) else {}
        primary = dict(scene.get("primary_visual")) if isinstance(scene.get("primary_visual"), Mapping) else {}
        asset = dict(primary.get("asset")) if isinstance(primary.get("asset"), Mapping) else {}
        if index < len(items):
            fallback = items[index]
            if isinstance(fallback, Mapping) and (fallback.get("src") or fallback.get("path") or fallback.get("url")):
                asset = dict(fallback)
                primary["asset"] = dict(asset)
                scene["primary_visual"] = primary
                scene["media"] = dict(asset)
        if asset.get("src") or asset.get("path") or asset.get("url"):
            scene_media = dict(scene.get("media")) if isinstance(scene.get("media"), Mapping) else {}
            if not (scene_media.get("src") or scene_media.get("path") or scene_media.get("url")):
                scene["media"] = dict(asset)
        hydrated.append(scene)
    return hydrated
'''
    if "def _run_director_plan" not in source:
        # Insert before _render_semantic_documentary_pipeline or _render_video_with_semantic_editing
        render_idx = source.find("def _render_semantic_documentary_pipeline")
        if render_idx == -1:
            render_idx = source.find("def _render_video_with_semantic_editing")
        source = source[:render_idx] + director_helpers + "\n\n" + source[render_idx:]

    # 4. Add Editor endpoints before if __name__ == '__main__':
    editor_endpoints = '''
# =============================================================================
# API - TIMELINE EDITOR AND PLAYER
# =============================================================================

@app.route('/api/video/<path:filename>')
def serve_api_video(filename):
    resolved = _resolve_output_file(filename) or _resolve_temp_file(filename)
    if resolved and resolved.is_file():
        from flask import send_file
        return send_file(str(resolved))
    return jsonify({"error": "Video not found"}), 404


def _resolve_editor_media_path(media_info, root=BASE_DIR):
    if not isinstance(media_info, Mapping) and not isinstance(root, Mapping):
        return None
    sources = []
    if isinstance(media_info, Mapping):
        sources.append(media_info)
    if isinstance(root, Mapping):
        sources.append(root)
    base_root = root if isinstance(root, (str, Path)) and not isinstance(root, Mapping) else BASE_DIR

    candidates = []
    for info in sources:
        for key in ('path', 'src', 'file', 'filepath', 'selected_path'):
            val = info.get(key)
            if val:
                candidates.append(str(val))
        for raw_url in (info.get('url'), info.get('src')):
            if not raw_url:
                continue
            url = str(raw_url)
            if url.startswith('/output/'):
                resolved = _resolve_output_file(url[len('/output/'):])
                if resolved:
                    candidates.append(str(resolved))
            elif url.startswith('/temp/'):
                resolved = _resolve_temp_file(url[len('/temp/'):])
                if resolved:
                    candidates.append(str(resolved))
            elif url.startswith('local-media://'):
                candidates.append(urllib.parse.unquote(url[len('local-media://'):]))
    for raw_path in candidates:
        if not raw_path:
            continue
        try:
            candidate = Path(str(raw_path))
            if not candidate.is_absolute():
                rooted = base_root / candidate
                if rooted.exists():
                    candidate = rooted
            candidate = candidate.resolve()
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _editor_resolution(value, default=(1920, 1080)):
    if isinstance(value, Mapping):
        width = value.get('w', 1920)
        height = value.get('h', 1080)
    else:
        raw = str(value or '1920x1080').strip().lower()
        presets = {
            '480p': (854, 480),
            '720p': (1280, 720),
            '1080p': (1920, 1080),
            '1440p': (2560, 1440),
            '2160p': (3840, 2160),
        }
        if raw in presets:
            return presets[raw]
        match = re.match(r'^(\\d+)\\s*[x×]\\s*(\\d+)$', raw)
        if match:
            width, height = match.groups()
        else:
            width, height = (1920, 1080)
    try:
        width = int(width)
        height = int(height)
        if not (320 <= width <= 7680 and 180 <= height <= 4320):
            raise ValueError("Editor resolution must be between 320x180 and 7680x4320")
        return (width, height)
    except (TypeError, ValueError):
        return (1920, 1080)


def _editor_float(value, default=0.0):
    try:
        number = float(value)
        if math.isfinite(number):
            return number
        return float(default)
    except (TypeError, ValueError):
        return float(default)


@app.route('/api/editor/media-info', methods=['POST'])
@require_active_access
def api_editor_media_info():
    data = request.json or {}
    source = _resolve_editor_media_path(data)
    if not source:
        return jsonify({"error": "Media path does not exist"}), 404
    media_type = "image" if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} else "video"
    if media_type == "image":
        try:
            from PIL import Image
            with Image.open(source) as img:
                w, h = img.size
            return jsonify({
                "basename": source.name,
                "path": str(source),
                "type": "image",
                "duration": 0.0,
                "width": int(w),
                "height": int(h),
            })
        except Exception as exc:
            return jsonify({"error": f"Unable to probe image: {exc}"}), 400
    try:
        import subprocess, json
        ffprobe_bin = str(os.environ.get("VIDZEO_FFPROBE_PATH") or "ffprobe")
        cmd = [
            ffprobe_bin, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json", str(source)
        ]
        probe = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        payload = json.loads(probe.stdout or "{}") if probe.returncode == 0 else {}
        streams = payload.get("streams") or []
        video_stream = next((row for row in streams if row.get("width") and row.get("height")), {})
        duration = _editor_float((payload.get("format") or {}).get("duration"), 0.0)
        return jsonify({
            "basename": source.name,
            "path": str(source),
            "type": media_type,
            "duration": duration,
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
        })
    except Exception as exc:
        return jsonify({"error": f"Unable to probe media: {exc}"}), 400


@app.route('/api/editor/render', methods=['POST'])
@require_active_access
def api_editor_render():
    try:
        data = request.json or {}
        project_name = str(data.get('name') or data.get('title') or 'Edited_Video').strip()
        safe_name = re.sub(r'[^a-zA-Z0-9_\\-]', '_', project_name).strip('_') or 'video'
        timestamp = int(time.time())
        out_filename = f"{safe_name}_edit_{timestamp}.mp4"
        out_path = OUTPUT_DIR / out_filename
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        manifest = data.get('manifest')
        if not (isinstance(manifest, dict) and manifest) and (isinstance(data, dict) and data.get('scenes')):
            manifest = data

        if manifest and isinstance(manifest, dict) and (manifest.get('scenes') or manifest.get('video_clips')):
            from services.native_render_policy import render_semantic_documentary
            cfg = dict(manifest.get('config') or {})
            cfg.setdefault('render_backend', 'ffmpeg' if str(cfg.get('quality') or manifest.get('quality') or 'standard').lower() in {'medium', 'fast'} else 'remotion')
            render_semantic_documentary(manifest=manifest, output_path=out_path, config=cfg)
            return jsonify({
                "success": True,
                "output_path": str(out_path),
                "url": f"/output/{out_filename}",
                "filename": out_filename,
            })

        tracks = [t for t in (data.get('tracks') or []) if isinstance(t, Mapping)]
        media_pool = {
            str(m.get('id') or ''): m
            for m in (data.get('mediaPool') or data.get('media_pool') or [])
            if isinstance(m, Mapping) and m.get('id')
        }
        fps = max(12, min(60, int(data.get('fps') or 30)))
        width, height = _editor_resolution(data.get('resolution') or data.get('aspect_ratio') or '1080p')

        video_clips = []
        audio_clips = []
        text_clips = []
        for track_index, track in enumerate(tracks):
            track_type = str(track.get('type') or 'video').lower()
            for clip_index, clip in enumerate(track.get('clips') or []):
                if not isinstance(clip, Mapping):
                    continue
                if track_type == 'video':
                    video_clips.append((track_index, clip_index, clip))
                elif track_type == 'audio':
                    audio_clips.append((track_index, clip_index, clip))
                elif track_type == 'text':
                    text_clips.append((track_index, clip_index, clip))

        image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
        prepared_video = []
        prepared_audio = []
        timeline_duration = _editor_float(data.get('duration'), 0.0)

        def prepare_clip(entry, media_kind):
            track_index, clip_index, clip = entry
            media_info = media_pool.get(str(clip.get('mediaId') or '')) or {}
            source = _resolve_editor_media_path(clip, media_info)
            if not source:
                label = clip.get('name') or clip.get('mediaId') or f"clip {clip_index + 1}"
                raise ValueError(f"Missing {media_kind} media for {label}")
            source_start = max(0.0, _editor_float(clip.get('start'), 0.0))
            source_end = _editor_float(clip.get('end'), source_start)
            clip_duration = source_end - source_start
            if clip_duration <= 0:
                clip_duration = max(0.2, _editor_float(media_info.get('duration'), 3.0))
            timeline_start = max(0.0, _editor_float(
                clip.get('trackOffset', clip.get('track_offset', 0.0)), 0.0
            ))
            timeline_end = timeline_start + clip_duration
            return {
                'source': source,
                'source_start': source_start,
                'duration': clip_duration,
                'timeline_start': timeline_start,
                'timeline_end': timeline_end,
                'is_image': source.suffix.lower() in image_exts,
                'clip': clip,
            }

        for entry in video_clips:
            prepared = prepare_clip(entry, 'video')
            prepared_video.append(prepared)
            timeline_duration = max(timeline_duration, prepared['timeline_end'])
        for entry in audio_clips:
            prepared = prepare_clip(entry, 'audio')
            prepared_audio.append(prepared)
            timeline_duration = max(timeline_duration, prepared['timeline_end'])

        import uuid
        render_tmp_dir = TEMP_DIR / f"ed_render_{uuid.uuid4().hex[:8]}"
        render_tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            duration = max(0.2, timeline_duration)
            ffmpeg_inputs = []
            filter_parts = [
                f"color=c=black:s={width}x{height}:r={fps}:d={duration:.3f},format=rgba[base0]"
            ]
            input_index = 0
            current_label = 'base0'
            for index, item in enumerate(prepared_video):
                if item['is_image']:
                    ffmpeg_inputs.extend(['-loop', '1', '-framerate', str(fps), '-i', str(item['source'])])
                else:
                    ffmpeg_inputs.extend([
                        '-ss', f"{item['source_start']:.3f}",
                        '-t', f"{item['duration']:.3f}",
                        '-i', str(item['source']),
                    ])
                clip_label = f'clip{index}'
                next_label = f'base{index + 1}'
                filter_parts.append(
                    f"[{input_index}:v]fps={fps},trim=duration={item['duration']:.3f},"
                    f"setpts=PTS-STARTPTS,scale={width}:{height}:"
                    f"force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                    f"format=rgba[{clip_label}]"
                )
                filter_parts.append(
                    f"[{current_label}][{clip_label}]overlay=shortest=0:"
                    f"eof_action=pass:x=0:y=0:enable='between(t,{item['timeline_start']:.3f},{item['timeline_end']:.3f})'"
                    f"[{next_label}]"
                )
                current_label = next_label
                input_index += 1

            text_label = current_label
            for text_index, (_track_index, _clip_index, clip) in enumerate(text_clips):
                text = str(clip.get('text') or clip.get('content') or clip.get('name') or '').strip()
                if not text:
                    continue
                start = max(0.0, _editor_float(clip.get('trackOffset', clip.get('track_offset', 0.0)), 0.0))
                clip_start = max(0.0, _editor_float(clip.get('start'), 0.0))
                clip_end = _editor_float(clip.get('end'), clip_start + 2.0)
                end = start + max(0.2, clip_end - clip_start)
                escaped = (text.replace('\\\\', r'\\\\').replace(':', r'\\:')
                           .replace("'", r"\\'").replace(',', r'\\,')
                           .replace('[', r'\\[').replace(']', r'\\]'))
                next_label = f'text{ text_index }'.replace(' ', '')
                filter_parts.append(
                    f"[{text_label}]drawtext=text='{escaped}':x=(w-text_w)/2:y=h-text_h-64:"
                    f"fontsize=42:fontcolor=white:borderw=2:bordercolor=black@0.75:"
                    f"enable='between(t,{start:.3f},{end:.3f})'[{next_label}]"
                )
                text_label = next_label

            audio_labels = []
            for index, item in enumerate(prepared_audio):
                ffmpeg_inputs.extend([
                    '-ss', f"{item['source_start']:.3f}",
                    '-t', f"{item['duration']:.3f}",
                    '-i', str(item['source']),
                ])
                audio_label = f'audio{index}'
                delay_ms = max(0, int(round(item['timeline_start'] * 1000)))
                filter_parts.append(
                    f"[{input_index}:a]atrim=duration={item['duration']:.3f},"
                    f"asetpts=PTS-STARTPTS,adelay={delay_ms}:all=1[{audio_label}]"
                )
                audio_labels.append(audio_label)
                input_index += 1

            final_label = text_label
            filter_parts.append(f"[{final_label}]format=yuv420p[vout]")
            if audio_labels:
                joined = ''.join(f'[{label}]' for label in audio_labels)
                filter_parts.append(
                    f"{joined}amix=inputs={len(audio_labels)}:duration=longest:"
                    f"dropout_transition=0,atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[aout]"
                )

            from services.gpu_accelerator import get_ffmpeg_video_encoder_args
            enc_args = get_ffmpeg_video_encoder_args(preset="veryfast", crf=22)
            cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'warning', '-nostdin',
                *ffmpeg_inputs, '-filter_complex', ';'.join(filter_parts),
                '-map', '[vout]',
            ]
            if audio_labels:
                cmd.extend(['-map', '[aout]', '-c:a', 'aac', '-b:a', '192k'])
            else:
                cmd.extend(['-an'])
            cmd.extend([*enc_args, '-r', str(fps), '-t', f'{duration:.3f}', str(out_path)])
            subprocess.run(cmd, check=True, timeout=300)
            if not out_path.is_file() or out_path.stat().st_size < 4096:
                raise RuntimeError('FFmpeg completed without producing a usable editor export')
        finally:
            try:
                import shutil
                shutil.rmtree(render_tmp_dir, ignore_errors=True)
            except Exception:
                pass

        return jsonify({
            "success": True,
            "output_path": str(out_path),
            "url": f"/output/{out_filename}",
            "filename": out_filename,
        })

    except Exception as exc:
        print(f"   [editor-render] Error rendering timeline: {exc}")
        return jsonify({"error": str(exc)}), 500
'''

    if "@app.route('/api/editor/render')" not in source:
        main_idx = source.find("if __name__ == '__main__':")
        source = source[:main_idx] + editor_endpoints + "\n\n" + source[main_idx:]

    # 5. Integrate execute_reviewer_council and audit_sequence_continuity into _render_semantic_documentary_pipeline
    swarm_wiring_old = '''        try:
            from services.editorial_director import (
                annotate_media_compositions,
                audit_media_compositions,
                manager_review,
                run_documentary_swarm,
            )
            director_manifest = annotate_media_compositions(director_manifest)
            director_manifest = run_documentary_swarm(director_manifest)
            director_manifest.setdefault("metrics", {})["media_first_audit"] = audit_media_compositions(director_manifest)
            director_manifest["quality_control"] = manager_review(director_manifest)
            director_manifest.setdefault("metrics", {})["manager_ready_for_long_render"] = bool(
                director_manifest["quality_control"].get("ready_for_long_render")
            )'''

    swarm_wiring_new = '''        try:
            from services.editorial_director import (
                annotate_media_compositions,
                audit_media_compositions,
                manager_review,
                run_documentary_swarm,
                execute_reviewer_council,
                audit_sequence_continuity,
            )
            director_manifest = annotate_media_compositions(director_manifest)
            director_manifest = run_documentary_swarm(director_manifest)
            director_manifest.setdefault("metrics", {})["media_first_audit"] = audit_media_compositions(director_manifest)
            director_manifest["sequence_audit"] = audit_sequence_continuity(director_manifest)
            director_manifest["reviewer_council"] = execute_reviewer_council(director_manifest)
            director_manifest["quality_control"] = manager_review(director_manifest)
            director_manifest["version"] = 4
            director_manifest.setdefault("metrics", {})["manager_ready_for_long_render"] = bool(
                director_manifest["reviewer_council"].get("ready_for_long_render")
                and director_manifest["quality_control"].get("ready_for_long_render")
            )'''

    if swarm_wiring_old in source:
        source = source.replace(swarm_wiring_old, swarm_wiring_new, 1)

    TARGET_APP.write_text(source, encoding="utf-8")
    print(f"Successfully reconstructed {TARGET_APP} ({len(source.splitlines())} lines)")

if __name__ == "__main__":
    build()
