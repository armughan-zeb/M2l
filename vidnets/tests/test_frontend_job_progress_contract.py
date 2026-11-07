from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_script_composer_exposes_live_word_count():
    html = (ROOT / "src" / "index.html").read_text(encoding="utf-8")
    assert 'id="quickCreateWordLimitHelper"' in html
    assert 'aria-live="polite"' in html


def test_new_queue_job_resets_previous_completion_progress_and_logs():
    source = (ROOT / "src" / "renderer.js").read_text(encoding="utf-8")
    assert "beginQueueItem(itemOrId" in source
    assert "this.lastProgressPercent = 0;" in source
    assert "this.completionHandledFor = null;" in source
    assert "if (steps) steps.replaceChildren();" in source
    assert "ProgressController.beginQueueItem(queuedItem, request.title);" in source
    assert "ProgressController.activeQueueId = queueResult?.item?.id" not in source


def test_projects_library_renders_all_selectable_exports_and_preview():
    source = (ROOT / "src" / "renderer.js").read_text(encoding="utf-8")
    html = (ROOT / "src" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "src" / "index.css").read_text(encoding="utf-8")

    assert "const rawProjects = await api('/api/projects');" in source
    assert "projects.forEach(project =>" in source
    assert "item.dataset.projectId = projectIdentity(project);" in source
    assert "selectProject(project" in source
    assert 'id="projectPreviewPlayer"' in html
    assert 'id="projectPreviewOpenButton"' in html
    assert ".project-preview-panel" in css
    # Do not defer cards below the fold in the packaged Chromium window.
    assert "content-visibility: visible" in css


def test_queue_polling_is_single_flight_and_generic_queue_add_starts_it():
    source = (ROOT / "src" / "renderer.js").read_text(encoding="utf-8")
    assert "queuePollInFlight" in source
    assert "if (this.queuePollInFlight) return;" in source
    assert "this.queuePollInFlight = false;" in source
    assert "this.startPolling({ notifyOnComplete: true });" in source


def test_queue_retry_action_stays_available_across_paused_and_resumed_states():
    source = (ROOT / "src" / "renderer.js").read_text(encoding="utf-8")
    active_renderer = source.split("QueueController.renderQueue = function(type) {", 1)[1]

    assert 'class="queue-actions"' in active_renderer
    assert "resumeQueueItem(" in active_renderer
    assert "Retry</button>" in active_renderer
    assert "api('/api/queue/resume'" in source
    # Refresh action controls when an incremental card changes status, so a
    # retried job does not retain a stale Retry button after it is re-queued.
    assert "if (nextActions) actions.innerHTML = nextActions.innerHTML;" in active_renderer


def test_progress_overlay_keeps_wait_copy_minimal_and_wrap_safe():
    source = (ROOT / "src" / "renderer.js").read_text(encoding="utf-8")
    html = (ROOT / "src" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "src" / "index.css").read_text(encoding="utf-8")

    assert 'id="progressMeta">Please wait</div>' in html
    assert "return 'Please wait';" in source
    assert "return 'Action needed';" in source
    assert "still working${elapsed}; please wait" not in source
    assert "overflow-wrap: anywhere;" in css
    assert "max-height: min(720px, calc(100vh - 32px));" in css


def test_documentary_direction_is_automatic_and_remotion_editor_remains_available():
    html = (ROOT / "src" / "index.html").read_text(encoding="utf-8")
    nav = html.split('<nav class="sidebar-nav">', 1)[1].split("</nav>", 1)[0]
    source = (ROOT / "src" / "renderer.js").read_text(encoding="utf-8")
    editor = (ROOT / "src" / "editor.js").read_text(encoding="utf-8")

    assert "Timeline Editor" not in nav
    assert "Style &amp; Templates" not in nav
    assert "settingEditorialTemplate" not in source
    assert 'id="editorRemotionPlayer"' in html
    assert '<script src="remotion-player.js"></script>' not in html
    assert "ensureRemotionPlayerBundle" in editor
    assert "new URL('./remotion-player.js', document.baseURI)" in editor
    assert 'id="quickCreateInput"' in html


def test_progress_wait_heartbeat_keeps_real_percent_and_animates_current_bar():
    source = (ROOT / "src" / "renderer.js").read_text(encoding="utf-8")
    queue = (ROOT / "server" / "services" / "queue_manager.py").read_text(encoding="utf-8")
    css = (ROOT / "src" / "index.css").read_text(encoding="utf-8")

    assert "progressFill.classList.toggle('is-waiting'" in source
    assert "heartbeat_progress = current" in queue
    assert "min(ceiling, current + 1)" not in queue
    assert ".progress-fill.is-waiting" in css


def test_flask_static_brand_and_remotion_preview_assets_exist():
    for relative in (
        "src/assets/vidnets-icon-glow.png",
        "src/remotion-player.js",
        "public/icon.ico",
        "public/icon.png",
    ):
        assert (ROOT / relative).is_file(), relative
