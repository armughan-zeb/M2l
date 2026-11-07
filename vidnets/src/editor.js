// =============================================================================
// VIDEO EDITOR — Full Interactive Controller
// CapCut-Inspired Multi-Track Timeline Editor
// =============================================================================

const EditorController = (() => {
    // ── Project State ──────────────────────────────────────────────────
    const project = {
        name: 'Untitled Project',
        fps: 30,
        resolution: { w: 1920, h: 1080 },
        duration: 0,
        playhead: 0,
        tracks: window.VidzeoTimeline.definitions.map(([id,type,name]) => ({id,type,name,clips:[],locked:false,visible:true,muted:false})),
        mediaPool: [],      // imported media files
        history: [],         // undo stack
        redoStack: [],
        selectedClipIds: [],
        zoom: 100,           // pixels per second
        snapEnabled: true,
        isPlaying: false
    };

    let historyBaseline = JSON.parse(JSON.stringify(project.tracks));
    let _initialized = false;
    let _animFrame = null;
    let _clipIdCounter = 0;
    let remotionManifest = null;
    let remotionPreviewManifest = null;
    let remotionPlayer = null;
    let remotionPlayerBundlePromise = null;
    let remotionMountScheduled = false;
    let selectedRemotionSceneIndex = 0;
    let remotionVoiceoverUrl = '';
    let remotionMusicUrl = '';
    const SCENE_VARIANTS = {
        SmartImage: ['full_bleed_cinematic','editorial_crop','framed_print','spotlight_reveal','parallax_float','archival_polaroid'],
        SmartVideo: ['full_bleed','pip_inset','split_duo','film_gate','browser_frame','evidence_clip'],
        KineticText: ['word_by_word_punch','staggered_lines','mask_slide_up','bold_minimal_center','tracking_expand','split_counter_accent'],
        StatCard: ['hero_counter_large','percentage_ring','kpi_metric_card','delta_growth_badge','stat_with_context'],
        QuoteCard: ['editorial_large_quote','quote_fragment_punch'],
        Headline: ['newspaper_front_page','digital_article_crop','floating_press_clipping','magnified_press_quote'],
        DocumentHighlight: ['declassified_redaction','yellow_marker_highlight','magnifier_zoom_lens','report_page_pan'],
        AnimatedMap: ['route_flight_arc','territory_zoom_highlight','city_to_city_transit','country_spotlight','global_network'],
        Timeline: ['vertical_chronology','horizontal_milestone_trail','date_flip_calendar','era_bracket_card'],
        Chart: ['editorial_bar','trend_line_with_markers','area_growth_fill','donut_distribution','ranked_horizontal_bars','before_after_delta'],
        Comparison: ['split_slider_before_after','side_by_side_cards','versus_contrast_clash','dual_metric_comparison'],
        Collage: ['evidence_pinboard','three_panel_asymmetric','contact_sheet_grid','scattered_photo_desk'],
        PersonProfile: ['cinematic_lower_third_card','minimal_profile_split','portrait_full_bleed'],
        Diagram: ['cause_and_effect_flow','step_by_step_process','network_node_connect','hierarchical_system_tree'],
        ChapterTitle: ['cinematic_minimal_act','bold_editorial_kicker','archival_slate_divider','technical_timecode_chapter']
    };

    // ── Utility ────────────────────────────────────────────────────────
    const el = (id) => document.getElementById(id);
    const uid = () => `clip_${++_clipIdCounter}_${Date.now()}`;

    function formatTC(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        const f = Math.floor((seconds % 1) * project.fps);
        return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}:${String(f).padStart(2,'0')}`;
    }

    function remotionScenes() { return Array.isArray(remotionManifest?.scenes) ? remotionManifest.scenes : []; }
    function selectedRemotionScene() { return remotionScenes()[selectedRemotionSceneIndex] || null; }
    function remotionProps(scene = selectedRemotionScene()) {
        if (!scene) return {};
        if (!scene.primary_visual || typeof scene.primary_visual !== 'object') scene.primary_visual = { type: 'SmartImage', variant: 'full_bleed_cinematic', props: {} };
        if (!scene.primary_visual.props || typeof scene.primary_visual.props !== 'object') scene.primary_visual.props = {};
        return scene.primary_visual.props;
    }
    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    }
    function rebuildRemotionTiming() {
        if (!remotionManifest) return;
        const fps = Math.max(1, Number(remotionManifest.fps || project.fps || 30));
        let frame = 0;
        const timingBefore = remotionScenes().map(scene => ({start:Number(scene.start || 0),duration:Number(scene.duration_in_frames || 0)/fps || Number(scene.duration || 0)}));
        for (const scene of remotionScenes()) {
            const duration = Math.max(0.5, Number(scene.duration || Number(scene.duration_in_frames || fps) / fps));
            const frames = Math.max(1, Math.round(duration * fps));
            scene.start = Number((frame / fps).toFixed(3));
            scene.from = frame;
            scene.duration = Number((frames / fps).toFixed(3));
            scene.duration_in_frames = frames;
            frame += frames;
        }
        project.tracks.forEach(track => track.clips.forEach(clip => {
            if (!Number.isInteger(clip.sceneIndex)) return;
            const previous = timingBefore[clip.sceneIndex], current = remotionScenes()[clip.sceneIndex];
            if (!previous || !current) return;
            const local = Math.max(0, clip.trackOffset - previous.start);
            clip.trackOffset = Number(current.start) + Math.min(local, Math.max(0, Number(current.duration) - 1/fps));
            clip.end = Math.min(clip.end, clip.start + Number(current.duration) - (clip.trackOffset - Number(current.start)));
            if (track.id !== 'copy' && Math.abs((clip.end-clip.start) - previous.duration) < 1/fps && local < 1/fps) clip.end = clip.start + Number(current.duration);
        }));
        remotionManifest.duration_in_frames = frame;
        remotionManifest.duration = Number((frame / fps).toFixed(3));
        project.duration = remotionManifest.duration;
    }
    function previewManifestForPlayer() {
        if (!remotionManifest) return null;
        const preview = window.VidzeoTimeline.exportTracks(remotionManifest, project.tracks);
        const clips = window._editorSourceClips || [];
        (preview.scenes || []).forEach((scene, index) => {
            const clip = clips[index];
            const media = scene.media || scene.background || {};
            if (clip?.url && (media.path || media.src) && !/^https?:/i.test(String(media.src || media.path))) {
                scene.media = { ...media, src: clip.url, type: clip.media_type === 'video' ? 'video' : (media.type || 'image') };
            }
        });
        if (remotionVoiceoverUrl && preview.audio?.voiceover) {
            preview.audio.voiceover = { ...preview.audio.voiceover, src: remotionVoiceoverUrl, path: remotionVoiceoverUrl };
        }
        if (remotionMusicUrl && preview.audio?.music) {
            preview.audio.music = { ...preview.audio.music, src: remotionMusicUrl, path: remotionMusicUrl };
        }
        function previewAssets(value) {
            if (Array.isArray(value)) return value.map(previewAssets);
            if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key,item]) => [key,previewAssets(item)]));
            if (typeof value === 'string' && /^[a-z]:[\\/]/i.test(value)) return `local-media://${value.replace(/\\/g,'/')}`;
            return value;
        }
        return previewAssets(preview);
    }
    function ensureRemotionPlayerBundle() {
        if (window.VidzeoRemotion?.mountPlayer) return Promise.resolve();
        if (remotionPlayerBundlePromise) return remotionPlayerBundlePromise;

        remotionPlayerBundlePromise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = new URL('./remotion-player.js', document.baseURI).href;
            script.async = true;
            script.dataset.vidzeoRemotionPlayer = '1';
            script.onload = () => {
                if (window.VidzeoRemotion?.mountPlayer) resolve();
                else reject(new Error('The Remotion Player bundle loaded without its mount API.'));
            };
            script.onerror = () => {
                script.remove();
                reject(new Error('The Remotion Player bundle could not be loaded.'));
            };
            (document.head || document.body).appendChild(script);
        }).catch((error) => {
            remotionPlayerBundlePromise = null;
            throw error;
        });

        return remotionPlayerBundlePromise;
    }

    function mountRemotionPreview() {
        const host = el('editorRemotionPlayer');
        if (!host || !remotionManifest) return;
        ensureRemotionPlayerBundle().then(() => {
            if (remotionMountScheduled) return;
            remotionMountScheduled = true;
            requestAnimationFrame(() => {
                remotionMountScheduled = false;
                if (!host.isConnected || !remotionManifest) return;
                remotionPreviewManifest = previewManifestForPlayer();
                try {
                    remotionPlayer = window.VidzeoRemotion.mountPlayer(host, remotionPreviewManifest, { autoPlay: false });
                    host.classList.add('is-active');
                    el('editorCanvas')?.classList.add('remotion-active');
                    el('editorPlaceholder')?.style.setProperty('display', 'none');
                } catch (error) {
                    console.error('Remotion preview failed:', error);
                    showEditorToast('Remotion preview failed', error.message || 'The scene preview could not be mounted.', 7000);
                }
            });
        }).catch((error) => {
            host.classList.remove('is-active');
            console.error('Remotion preview bundle failed:', error);
            showEditorToast('Remotion preview unavailable', error.message || 'Rebuild the app assets and reopen the editor.', 7000);
        });
    }
    function renderRemotionSceneTimeline() {
        const strip = el('remotionSceneTimeline');
        if (!strip) return;
        strip.innerHTML = remotionScenes().map((scene, index) => {
            const type = scene.primary_visual?.type || scene.primary?.type || 'SmartImage';
            const variant = scene.primary_visual?.variant || scene.primary?.variant || '';
            const locked = scene.locked || scene.design?.locked || scene.director_decision?.locked;
            return `<button type="button" class="remotion-scene-chip ${index === selectedRemotionSceneIndex ? 'active' : ''}" data-remotion-scene="${index}"><strong>${index + 1}. ${escapeHtml(type)}${locked ? ' 🔒' : ''}</strong><span>${escapeHtml(variant)} · ${Number(scene.duration || 0).toFixed(1)}s</span></button>`;
        }).join('');
        strip.querySelectorAll('[data-remotion-scene]').forEach(button => button.addEventListener('click', () => selectRemotionScene(Number(button.dataset.remotionScene))));
    }
    function selectRemotionScene(index) {
        if (!remotionScenes().length) return;
        selectedRemotionSceneIndex = Math.max(0, Math.min(remotionScenes().length - 1, Number(index) || 0));
        renderRemotionSceneTimeline();
        populateRemotionSceneControls();
        const scene = selectedRemotionScene();
        remotionPlayer?.seekTo?.(Number(scene?.from || 0));
    }
    function populateRemotionSceneControls() {
        const scene = selectedRemotionScene();
        const controls = el('remotionSceneControls');
        if (!scene || !controls) return;
        const primary = scene.primary_visual || scene.primary || {};
        const props = remotionProps(scene);
        const type = SCENE_VARIANTS[primary.type] ? primary.type : 'SmartImage';
        if (el('edRemotionType')) el('edRemotionType').value = type;
        const variants = SCENE_VARIANTS[type] || [];
        const variantEl = el('edRemotionVariant');
        if (variantEl) {
            variantEl.innerHTML = variants.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value.replaceAll('_',' '))}</option>`).join('');
            if (primary.variant && !variants.includes(primary.variant)) variantEl.insertAdjacentHTML('afterbegin', `<option value="${escapeHtml(primary.variant)}">${escapeHtml(primary.variant)}</option>`);
            variantEl.value = primary.variant || variants[0] || '';
        }
        const fps = Number(remotionManifest.fps || 30);
        if (el('edRemotionDuration')) el('edRemotionDuration').value = Number(scene.duration || Number(scene.duration_in_frames || fps) / fps).toFixed(1);
        if (el('edRemotionCamera')) el('edRemotionCamera').value = scene.camera?.motion || scene.motion?.camera || 'slow_push';
        if (el('edRemotionTransition')) el('edRemotionTransition').value = scene.transition?.template_id || scene.transition_out || 'hard_cut';
        if (el('edRemotionFocus')) el('edRemotionFocus').value = scene.text?.focus || props.text_focus || props.highlight || props.emphasis || '';
        const locked = Boolean(scene.locked || scene.design?.locked || scene.director_decision?.locked);
        if (el('edRemotionLocked')) el('edRemotionLocked').checked = locked;
        if (el('edRemotionMapFrom')) el('edRemotionMapFrom').value = props.from || props.start || '';
        if (el('edRemotionMapTo')) el('edRemotionMapTo').value = props.to || props.end || '';
        if (el('edRemotionMapCountry')) el('edRemotionMapCountry').value = props.country || props.target_country || '';
        const capture = props.source_capture || {};
        if (el('edRemotionEvidenceUrl')) el('edRemotionEvidenceUrl').value = capture.url || props.source_url || '';
        if (el('edRemotionEvidenceQuote')) el('edRemotionEvidenceQuote').value = capture.exact_excerpt || props.submitted_excerpt || props.exact_excerpt || '';
        if (el('edRemotionEvidenceStatus')) el('edRemotionEvidenceStatus').textContent = capture.status === 'verified_capture' ? `Verified · ${capture.publication || capture.domain || 'source'}` : 'Attributed card until verified';
        if (el('edRemotionSceneLabel')) el('edRemotionSceneLabel').textContent = `${selectedRemotionSceneIndex + 1} of ${remotionScenes().length}`;
        controls.querySelectorAll('input,select,textarea,button').forEach(control => { control.disabled = locked && control.id !== 'edRemotionLocked'; });
    }
    function updateRemotionSceneFromControls(event) {
        const scene = selectedRemotionScene();
        if (!scene) return;
        if (event.target.id === 'edRemotionLocked') {
            scene.locked = Boolean(event.target.checked);
            scene.director_decision = { ...(scene.director_decision || {}), locked: scene.locked };
            scene.design = { ...(scene.design || {}), locked: scene.locked };
            populateRemotionSceneControls(); renderRemotionSceneTimeline(); return;
        }
        if (scene.locked || scene.design?.locked || scene.director_decision?.locked) return;
        const props = remotionProps(scene);
        const primary = scene.primary_visual || scene.primary;
        switch (event.target.id) {
            case 'edRemotionType': primary.type = event.target.value; primary.variant = SCENE_VARIANTS[primary.type]?.[0] || 'full_bleed_cinematic'; props.variant = primary.variant; populateRemotionSceneControls(); break;
            case 'edRemotionVariant': primary.variant = event.target.value; props.variant = event.target.value; break;
            case 'edRemotionDuration': scene.duration = Math.max(0.5, Math.min(120, Number(event.target.value) || 0.5)); rebuildRemotionTiming(); break;
            case 'edRemotionCamera': scene.camera = { ...(scene.camera || {}), motion: event.target.value }; scene.motion = { ...(scene.motion || {}), camera: event.target.value }; break;
            case 'edRemotionTransition': scene.transition_out = event.target.value; scene.transition = { ...(scene.transition || {}), template_id: event.target.value }; break;
            case 'edRemotionFocus': {
                const phrase = event.target.value.trim();
                scene.text = { ...(scene.text || {}), focus: phrase }; props.text_focus = phrase;
                if (primary.type === 'DocumentHighlight') props.highlight = phrase;
                else if (primary.type === 'KineticText') { props.text = phrase || scene.narration || ''; props.emphasis = phrase; }
                else props.emphasis = phrase;
                break;
            }
            case 'edRemotionMapFrom': if (event.target.value.trim()) props.from = event.target.value.trim(); else delete props.from; break;
            case 'edRemotionMapTo': if (event.target.value.trim()) props.to = event.target.value.trim(); else delete props.to; break;
            case 'edRemotionMapCountry': if (event.target.value.trim()) props.country = event.target.value.trim(); else delete props.country; break;
            case 'edRemotionEvidenceUrl': props.source_url = event.target.value.trim(); props.source_capture = { url: event.target.value.trim(), exact_excerpt: '', submitted_excerpt: el('edRemotionEvidenceQuote')?.value || '', status: 'attributed_card', verification_status: 'source_unverified' }; delete props.highlight; break;
            case 'edRemotionEvidenceQuote': props.submitted_excerpt = event.target.value.trim(); props.source_capture = { url: el('edRemotionEvidenceUrl')?.value || '', exact_excerpt: '', submitted_excerpt: event.target.value.trim(), status: 'attributed_card', verification_status: 'source_unverified' }; delete props.highlight; break;
            default: return;
        }
        rebuildRemotionTiming(); renderTimeline(); renderRemotionSceneTimeline(); mountRemotionPreview();
        remotionPlayer?.seekTo?.(Number(scene.from || 0));
    }
    function setupRemotionEditingControls() {
        const controls = el('remotionSceneControls');
        if (!controls || controls.dataset.bound) return;
        controls.dataset.bound = '1';
        controls.addEventListener('input', updateRemotionSceneFromControls);
        controls.addEventListener('change', updateRemotionSceneFromControls);
        el('edRemotionCaptureEvidence')?.addEventListener('click', captureRemotionEvidence);
    }
    async function captureRemotionEvidence() {
        const scene = selectedRemotionScene();
        if (!scene) return;
        const props = remotionProps(scene);
        const url = String(el('edRemotionEvidenceUrl')?.value || '').trim();
        const excerpt = String(el('edRemotionEvidenceQuote')?.value || '').trim();
        if (!url || !excerpt) { showEditorToast('Source details needed', 'Add a source page and its exact supporting passage.', 5000); return; }
        const button = el('edRemotionCaptureEvidence');
        if (button) { button.disabled = true; button.textContent = 'Capturing…'; }
        try {
            const response = await fetch('/api/editor/source-capture', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url, excerpt, scene_index: selectedRemotionSceneIndex }) });
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.error || 'Source capture failed');
            props.source_capture = { ...result.source_capture, submitted_excerpt: excerpt };
            props.source_url = url;
            if (result.source_capture.verification_status === 'exact_passage_verified') props.highlight = result.source_capture.exact_excerpt;
            else delete props.highlight;
            scene.primary_visual.type = 'DocumentHighlight';
            scene.primary_visual.variant = 'yellow_marker_highlight';
            scene.primary_visual.props = props;
            scene.director_treatment_enabled = true;
            populateRemotionSceneControls(); renderRemotionSceneTimeline(); mountRemotionPreview();
            const verified = result.source_capture.status === 'verified_capture';
            showEditorToast(verified ? 'Passage captured' : 'Evidence card prepared', verified ? 'Exact passage verified; the source screenshot is attached.' : 'The page could not be verified, so this scene will use an attributed card.', 5500);
        } catch (error) {
            showEditorToast('Evidence capture failed', error.message || 'Could not capture this source.', 6000);
        } finally { if (button) { button.disabled = false; button.textContent = 'Capture passage'; } }
    }

    function showEditorToast(title, msg, duration = 4000) {
        const existing = document.querySelector('.editor-toast');
        if (existing) existing.remove();
        const toast = document.createElement('div');
        toast.className = 'editor-toast';
        toast.innerHTML = `
            <button class="editor-toast-close" onclick="this.parentElement.remove()">✕</button>
            <div class="editor-toast-title">${title}</div>
            <div class="editor-toast-msg">${msg}</div>`;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), duration);
    }

    // ── Initialization ─────────────────────────────────────────────────
    function init() {
        if (_initialized) {
            drawRuler();
            return;
        }
        _initialized = true;

        setupMediaTabs();
        setupDropzone();
        setupPlayerControls();
        setupRemotionEditingControls();
        setupTimelineToolbar();
        setupTimelineInteraction();
        setupKeyboardShortcuts();
        setupToggles();
        setupContextMenu();
        setupResizablePanels();
        drawRuler();
        renderTimeline();
        updateTimecodeDisplay();

        console.log('✂️ Video Editor initialized');
    }

    // ── Media Tabs ─────────────────────────────────────────────────────
    function setupMediaTabs() {
        const tabContainer = el('editorMediaTabs');
        if (!tabContainer) return;
        tabContainer.addEventListener('click', (e) => {
            const tab = e.target.closest('.editor-media-tab');
            if (!tab) return;
            tabContainer.querySelectorAll('.editor-media-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const tabName = tab.dataset.tab;
            updateMediaPanel(tabName);
        });
    }

    function updateMediaPanel(tabName) {
        const content = el('editorMediaContent');
        if (!content) return;
        // For non-media tabs show placeholder
        if (tabName !== 'media') {
            const labels = {
                audio: '🎵 Audio Library', text: 'T Text Templates',
                stickers: '✨ Stickers', effects: '🎭 Effects',
                transitions: '🔀 Transitions', filters: '🌈 Filters', ai: '🤖 AI Tools'
            };
            // Keep dropzone for media, show placeholder for others
            content.innerHTML = `
                <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:200px;gap:12px;">
                    <div style="font-size:32px;opacity:0.3;">${labels[tabName]?.charAt(0) || '📁'}</div>
                    <div style="font-size:13px;color:var(--ed-text-sec);">${labels[tabName] || tabName}</div>
                    <div style="font-size:11px;color:var(--ed-text-muted);">Coming soon</div>
                </div>`;
            return;
        }
        // Rebuild media tab content
        rebuildMediaPanel();
    }

    function rebuildMediaPanel() {
        const content = el('editorMediaContent');
        if (!content) return;
        let html = `
            <div class="editor-dropzone" id="editorDropzone">
                <div class="editor-dropzone-icon">+</div>
                <div class="editor-dropzone-label">Import</div>
                <div class="editor-dropzone-hint">Drag and drop videos, photos, and audio files here</div>
            </div>`;

        if (project.mediaPool.length === 0) {
            html += `
                <div class="editor-divider-text">No media? Create with these tools</div>
                <div class="editor-create-cards">
                    <button class="editor-create-card"><span class="card-icon">🤖</span><span class="card-label">AI media</span></button>
                    <button class="editor-create-card"><span class="card-icon">👤</span><span class="card-label">AI avatars</span></button>
                    <button class="editor-create-card"><span class="card-icon">🎥</span><span class="card-label">Record</span></button>
                </div>`;
        }

        html += '<div class="editor-media-grid" id="editorMediaGrid">';
        project.mediaPool.forEach((media, i) => {
            const dur = media.duration ? formatTC(media.duration).substring(3) : '';
            html += `
                <div class="editor-media-thumb" draggable="true" data-media-idx="${i}" title="${media.basename}">
                    ${media.type === 'video'
                        ? `<video src="${media.url}" preload="metadata" muted></video>`
                        : `<div style="width:100%;height:100%;background:var(--ed-bg-elevated);display:flex;align-items:center;justify-content:center;font-size:20px;">${media.type === 'audio' ? '🎵' : '🖼️'}</div>`
                    }
                    <div class="media-overlay"></div>
                    ${dur ? `<div class="media-duration">${dur}</div>` : ''}
                    <div class="media-filename">${media.basename}</div>
                </div>`;
        });
        html += '</div>';
        content.innerHTML = html;

        // Re-attach dropzone listeners
        setupDropzone();

        // Attach drag listeners to media thumbnails
        content.querySelectorAll('.editor-media-thumb[draggable]').forEach(thumb => {
            thumb.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('text/plain', thumb.dataset.mediaIdx);
                e.dataTransfer.effectAllowed = 'copy';
            });
        });
    }

    // ── Dropzone (Import) ──────────────────────────────────────────────
    function setupDropzone() {
        const dropzone = el('editorDropzone');
        if (!dropzone) return;

        dropzone.addEventListener('click', () => importMedia());
        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault(); e.stopPropagation();
            dropzone.classList.add('drag-over');
        });
        dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault(); e.stopPropagation();
            dropzone.classList.remove('drag-over');
            if (e.dataTransfer.files.length > 0) {
                handleDroppedFiles(e.dataTransfer.files);
            }
        });
    }

    async function importMedia() {
        // Try Electron's file dialog first
        if (window.electronAPI && window.electronAPI.openFileDialog) {
            try {
                const paths = await window.electronAPI.openFileDialog();
                if (paths && paths.length > 0) {
                    for (const p of paths) {
                        await addMediaFromPath(p);
                    }
                    return;
                }
            } catch(e) { console.warn('Electron dialog not available:', e); }
        }

        // Fallback: HTML file input
        const input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.accept = 'video/*,audio/*,image/*,.vidzeo';
        input.onchange = () => handleDroppedFiles(input.files);
        input.click();
    }

    function handleDroppedFiles(files) {
        Array.from(files).forEach(file => {
            if (/\.vidzeo$/i.test(file.name)) { readProjectFile(file); return; }
            const url = URL.createObjectURL(file);
            const type = file.type.startsWith('video') ? 'video'
                : file.type.startsWith('audio') ? 'audio' : 'image';

            const media = {
                id: uid(),
                path: file.path || file.name,
                basename: file.name,
                url,
                type,
                duration: 0,
                width: 0, height: 0,
                file
            };

            // Get duration for video/audio
            if (type === 'video' || type === 'audio') {
                const probe = document.createElement(type);
                probe.preload = 'metadata';
                probe.onloadedmetadata = () => {
                    media.duration = probe.duration;
                    if (type === 'video') {
                        media.width = probe.videoWidth;
                        media.height = probe.videoHeight;
                    }
                    URL.revokeObjectURL(probe.src);
                    rebuildMediaPanel();
                };
                probe.src = url;
            }

            project.mediaPool.push(media);
        });

        rebuildMediaPanel();
        showEditorToast('Media Imported', `${files.length} file(s) added to library`);
    }

    async function addMediaFromPath(filePath) {
        try {
            const info = await fetch('/api/editor/media-info', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:filePath})}).then(r => r.json());
            if (info.error) { showEditorToast('Import Error', info.error); return; }

            const ext = filePath.split('.').pop().toLowerCase();
            const type = ['mp4','mov','avi','mkv','webm','wmv','flv'].includes(ext) ? 'video'
                : ['mp3','wav','ogg','aac','flac','m4a'].includes(ext) ? 'audio' : 'image';

            // Convert Windows backslashes to forward slashes for URL
            const normalizedPath = filePath.replace(/\\/g, '/');
            const mediaUrl = `local-media://${normalizedPath}`;

            project.mediaPool.push({
                id: uid(),
                path: filePath,
                basename: info.basename || filePath.split(/[/\\]/).pop(),
                url: mediaUrl,
                type,
                duration: info.duration || 0,
                width: info.width || 0,
                height: info.height || 0
            });

            rebuildMediaPanel();
        } catch(e) {
            console.error('Failed to add media:', e);
        }
    }

    // ── Player Controls ────────────────────────────────────────────────
    function setupPlayerControls() {
        el('editorPlayBtn')?.addEventListener('click', togglePlayback);
        el('editorFirst')?.addEventListener('click', () => seekTo(0));
        el('editorLast')?.addEventListener('click', () => seekTo(project.duration));
        el('editorBack5')?.addEventListener('click', () => seekRelative(-5));
        el('editorFwd5')?.addEventListener('click', () => seekRelative(5));
        el('editorPrevFrame')?.addEventListener('click', () => seekRelative(-1 / project.fps));
        el('editorNextFrame')?.addEventListener('click', () => seekRelative(1 / project.fps));

        // Scrub bar
        const scrubBar = el('editorScrubBar');
        if (scrubBar) {
            let scrubbing = false;
            const doScrub = (e) => {
                const rect = scrubBar.getBoundingClientRect();
                const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                seekTo(pct * project.duration);
            };
            scrubBar.addEventListener('mousedown', (e) => { scrubbing = true; doScrub(e); });
            document.addEventListener('mousemove', (e) => { if (scrubbing) doScrub(e); });
            document.addEventListener('mouseup', () => { scrubbing = false; });
        }

        // Fullscreen
        el('editorFullscreenBtn')?.addEventListener('click', () => {
            const canvas = el('editorCanvas');
            if (canvas) {
                if (document.fullscreenElement) document.exitFullscreen();
                else canvas.requestFullscreen().catch(() => {});
            }
        });
    }

    function togglePlayback() {
        project.isPlaying = !project.isPlaying;
        const btn = el('editorPlayBtn');
        const video = el('editorVideo');

        if (project.isPlaying) {
            btn.textContent = '⏸';
            if (remotionManifest) remotionPlayer?.play?.();
            if (video && video.src) video.play();
            _animFrame = requestAnimationFrame(playbackLoop);
        } else {
            btn.textContent = '▶';
            if (remotionManifest) remotionPlayer?.pause?.();
            if (video) video.pause();
            if (_animFrame) cancelAnimationFrame(_animFrame);
        }
    }

    let _lastPlayTime = 0;
    function playbackLoop(timestamp) {
        if (!project.isPlaying) return;
        if (_lastPlayTime === 0) _lastPlayTime = timestamp;
        const delta = (timestamp - _lastPlayTime) / 1000;
        _lastPlayTime = timestamp;

        project.playhead = Math.min(project.duration, project.playhead + delta);
        if (project.playhead >= project.duration) {
            project.playhead = project.duration;
            project.isPlaying = false;
            el('editorPlayBtn').textContent = '▶';
            el('editorVideo')?.pause();
            remotionPlayer?.pause?.();
            _lastPlayTime = 0;
            updateTimecodeDisplay();
            updatePlayhead();
            return;
        }

        updateTimecodeDisplay();
        updatePlayhead();
        updateScrubBar();
        syncVideoToPlayhead();

        _animFrame = requestAnimationFrame(playbackLoop);
    }

    function seekTo(time) {
        project.playhead = Math.max(0, Math.min(project.duration, time));
        _lastPlayTime = 0;
        updateTimecodeDisplay();
        updatePlayhead();
        updateScrubBar();
        syncVideoToPlayhead();
        if (remotionManifest) remotionPlayer?.seekTo?.(Math.round(project.playhead * Number(remotionManifest.fps || project.fps || 30)));
    }

    function seekRelative(delta) {
        seekTo(project.playhead + delta);
    }

    function syncVideoToPlayhead() {
        if (remotionManifest) {
            remotionPlayer?.seekTo?.(Math.round(project.playhead * Number(remotionManifest.fps || project.fps || 30)));
            return;
        }
        const video = el('editorVideo');
        if (!video || !video.src) return;
        // Find the clip under the playhead on the first video track
        const vTrack = project.tracks.find(t => t.type === 'video');
        if (!vTrack) return;
        const clip = vTrack.clips.find(c =>
            project.playhead >= c.trackOffset &&
            project.playhead < c.trackOffset + (c.end - c.start)
        );
        if (clip) {
            const media = project.mediaPool.find(m => m.id === clip.mediaId);
            if (media && video.src !== media.url) {
                video.src = media.url;
                video.style.display = 'block';
                el('editorPlaceholder').style.display = 'none';
            }
            const clipTime = clip.start + (project.playhead - clip.trackOffset);
            if (Math.abs(video.currentTime - clipTime) > 0.1) {
                video.currentTime = clipTime;
            }
        }
    }

    function updateTimecodeDisplay() {
        const tc = el('editorTcCurrent');
        const ptc = el('editorProjectTc');
        if (tc) tc.textContent = `${formatTC(project.playhead)} / ${formatTC(project.duration)}`;
        if (ptc) ptc.textContent = formatTC(project.playhead);
    }

    function updateScrubBar() {
        const fill = el('editorScrubFill');
        if (fill && project.duration > 0) {
            fill.style.width = `${(project.playhead / project.duration) * 100}%`;
        }
    }

    function updatePlayhead() {
        const ph = el('editorPlayhead');
        const tracksArea = el('editorTimelineTracks');
        if (!ph || !tracksArea) return;
        const pxPerSec = project.zoom;
        ph.style.left = `${project.playhead * pxPerSec}px`;
        ph.style.height = `${tracksArea.scrollHeight}px`;
    }

    // ── Timeline Toolbar ───────────────────────────────────────────────
    function setupTimelineToolbar() {
        el('edUndo')?.addEventListener('click', undo);
        el('edRedo')?.addEventListener('click', redo);
        el('edSplit')?.addEventListener('click', splitAtPlayhead);
        el('edDelete')?.addEventListener('click', deleteSelected);

        el('edSnap')?.addEventListener('click', function() {
            project.snapEnabled = !project.snapEnabled;
            this.classList.toggle('active', project.snapEnabled);
        });

        // Zoom
        const zoomSlider = el('edZoomSlider');
        if (zoomSlider) {
            zoomSlider.addEventListener('input', () => {
                project.zoom = parseInt(zoomSlider.value);
                renderTimeline();
                drawRuler();
            });
        }
        el('edZoomIn')?.addEventListener('click', () => {
            project.zoom = Math.min(500, project.zoom + 20);
            if (zoomSlider) zoomSlider.value = project.zoom;
            renderTimeline(); drawRuler();
        });
        el('edZoomOut')?.addEventListener('click', () => {
            project.zoom = Math.max(10, project.zoom - 20);
            if (zoomSlider) zoomSlider.value = project.zoom;
            renderTimeline(); drawRuler();
        });
        el('edFitTimeline')?.addEventListener('click', fitTimeline);
        el('edAddTrack')?.addEventListener('click', addTrack);

        // Export
        el('editorExportBtn')?.addEventListener('click', exportProject);
    }

    function fitTimeline() {
        const tracksArea = el('editorTimelineTracks');
        if (!tracksArea || project.duration <= 0) return;
        const availableWidth = tracksArea.clientWidth - 20;
        project.zoom = Math.max(10, Math.floor(availableWidth / project.duration));
        const zoomSlider = el('edZoomSlider');
        if (zoomSlider) zoomSlider.value = project.zoom;
        renderTimeline();
        drawRuler();
    }

    // ── Timeline Interaction (Drop, Drag, Select) ──────────────────────
    function setupTimelineInteraction() {
        const tracksArea = el('editorTimelineTracks');
        if (!tracksArea) return;

        tracksArea.addEventListener('scroll', () => { const headers = el('editorTrackHeaders'); if (headers) headers.scrollTop = tracksArea.scrollTop; drawRuler(); });
        el('editorTrackHeaders')?.addEventListener('scroll', () => { const headers = el('editorTrackHeaders'); if (headers && tracksArea.scrollTop !== headers.scrollTop) tracksArea.scrollTop = headers.scrollTop; });

        // Drop media from library onto timeline
        tracksArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
        });

        tracksArea.addEventListener('drop', (e) => {
            e.preventDefault();
            const mediaIdx = parseInt(e.dataTransfer.getData('text/plain'));
            if (isNaN(mediaIdx)) return;

            const media = project.mediaPool[mediaIdx];
            if (!media) return;

            // Determine which track to drop onto
            const trackEl = e.target.closest('.editor-track');
            const trackIdx = trackEl ? parseInt(trackEl.dataset.track) : 0;
            const track = project.tracks[trackIdx] || project.tracks[0];

            // Calculate time position from drop X
            const rect = tracksArea.getBoundingClientRect();
            const x = e.clientX - rect.left + tracksArea.scrollLeft;
            const timeOffset = Math.max(0, x / project.zoom);

            addClipToTrack(track, media, timeOffset);
        });

        // Click on clip to select
        tracksArea.addEventListener('click', (e) => {
            const clipEl = e.target.closest('.editor-clip');
            if (clipEl) {
                const clipId = clipEl.dataset.clipId;
                if (e.ctrlKey || e.metaKey) {
                    // Multi-select
                    const idx = project.selectedClipIds.indexOf(clipId);
                    if (idx >= 0) project.selectedClipIds.splice(idx, 1);
                    else project.selectedClipIds.push(clipId);
                } else {
                    project.selectedClipIds = [clipId];
                }
                renderTimeline();
                updatePropertiesPanel();
            } else {
                // Deselect all
                project.selectedClipIds = [];
                renderTimeline();
                updatePropertiesPanel();
            }
        });

        // Playhead dragging on ruler
        const rulerTicks = el('editorRulerTicks');
        if (rulerTicks) {
            let draggingPH = false;
            rulerTicks.addEventListener('mousedown', (e) => {
                draggingPH = true;
                const rect = rulerTicks.getBoundingClientRect();
                const x = e.clientX - rect.left;
                seekTo(x / project.zoom);
            });
            document.addEventListener('mousemove', (e) => {
                if (!draggingPH) return;
                const rect = rulerTicks.getBoundingClientRect();
                const x = e.clientX - rect.left + tracksArea.scrollLeft;
                seekTo(Math.max(0, x / project.zoom));
            });
            document.addEventListener('mouseup', () => { draggingPH = false; });
        }

        // Clip dragging (move clips on timeline)
        let dragClip = null, dragStartX = 0, dragOrigOffset = 0;
        tracksArea.addEventListener('mousedown', (e) => {
            const clipEl = e.target.closest('.editor-clip');
            if (!clipEl || e.target.classList.contains('editor-clip-handle')) return;
            const clipId = clipEl.dataset.clipId;
            const clip = findClipById(clipId);
            if (!clip || findTrackForClip(clipId)?.locked) return;
            dragClip = clip;
            dragStartX = e.clientX;
            dragOrigOffset = clip.trackOffset;
            e.preventDefault();
        });
        document.addEventListener('mousemove', (e) => {
            if (!dragClip) return;
            const dx = e.clientX - dragStartX;
            const dt = dx / project.zoom;
            const bounds = clipSceneBounds(dragClip);
            dragClip.trackOffset = Math.max(bounds.start, Math.min(bounds.end - (dragClip.end - dragClip.start), dragOrigOffset + dt));
            recalcDuration();
            renderTimeline();
        });
        document.addEventListener('mouseup', () => {
            if (dragClip) {
                pushHistory('Move clip');
                dragClip = null;
            }
        });

        // Clip resize handles
        let resizeClip = null, resizeSide = null, resizeStartX = 0;
        let resizeOrigStart = 0, resizeOrigEnd = 0, resizeOrigOffset = 0;

        tracksArea.addEventListener('mousedown', (e) => {
            const handle = e.target.closest('.editor-clip-handle');
            if (!handle) return;
            const clipEl = handle.closest('.editor-clip');
            const clipId = clipEl.dataset.clipId;
            resizeClip = findClipById(clipId);
            if (!resizeClip || findTrackForClip(clipId)?.locked) { resizeClip = null; return; }
            resizeSide = handle.classList.contains('left') ? 'left' : 'right';
            resizeStartX = e.clientX;
            resizeOrigStart = resizeClip.start;
            resizeOrigEnd = resizeClip.end;
            resizeOrigOffset = resizeClip.trackOffset;
            e.preventDefault();
            e.stopPropagation();
        });
        document.addEventListener('mousemove', (e) => {
            if (!resizeClip) return;
            const dx = e.clientX - resizeStartX;
            const dt = dx / project.zoom;
            if (resizeSide === 'left') {
                const bounds = clipSceneBounds(resizeClip);
                const adjusted = Math.max(bounds.start - resizeOrigOffset, dt);
                const newStart = Math.max(0, resizeOrigStart + adjusted);
                if (newStart < resizeClip.end) {
                    resizeClip.start = newStart;
                    resizeClip.trackOffset = Math.max(bounds.start, resizeOrigOffset + adjusted);
                }
            } else {
                const newEnd = Math.max(resizeClip.start + 0.1, resizeOrigEnd + dt);
                resizeClip.end = Math.min(resizeClip.mediaDuration || Infinity, newEnd, resizeClip.start + clipSceneBounds(resizeClip).end - resizeClip.trackOffset);
            }
            recalcDuration();
            renderTimeline();
        });
        document.addEventListener('mouseup', () => {
            if (resizeClip) { pushHistory('Resize clip'); resizeClip = null; }
        });
    }

    function clipSceneBounds(clip) {
        const scene = Number.isInteger(clip.sceneIndex) && remotionScenes()[clip.sceneIndex];
        return scene ? {start:Number(scene.start || 0),end:Number(scene.start || 0)+Number(scene.duration || 0)} : {start:0,end:Infinity};
    }

    // ── Add Clip to Track ──────────────────────────────────────────────
    function addClipToTrack(track, media, timeOffset) {
        if ((track.type === 'audio' && media.type !== 'audio') || (track.id === 'media' && media.type === 'audio')) { showEditorToast('Choose a matching track', 'Place pictures and video on Media, and audio on Music or Sound effects.'); return; }
        const duration = media.duration || 5; // default 5s for images
        const clip = {
            id: uid(),
            mediaId: media.id,
            name: media.basename,
            type: media.type,
            start: 0,
            end: duration,
            trackOffset: timeOffset,
            mediaDuration: duration,
            sceneIndex: remotionScenes().findIndex(scene => timeOffset >= Number(scene.start || 0) && timeOffset < Number(scene.start || 0) + Number(scene.duration || 0)),
            productionPayload: {asset:media.path || media.url,media_type:media.type}
        };
        if (track.locked || (remotionManifest && !['media','music','sfx'].includes(track.id))) { showEditorToast('Choose a media or audio track', 'Graphics and approved text are edited with the scene controls.'); return; }
        track.clips.push(clip);
        pushHistory(`Add ${media.type}`);
        recalcDuration();
        renderTimeline();

        // Hide empty state
        const empty = el('editorTimelineEmpty');
        if (empty) empty.style.display = 'none';

        // Show video in preview if first video added
        if (media.type === 'video') {
            const video = el('editorVideo');
            if (video) {
                video.src = media.url;
                video.style.display = 'block';
                el('editorPlaceholder').style.display = 'none';
            }
        }

        showEditorToast('Clip Added', `"${media.basename}" added to ${track.name}`);
    }

    function recalcDuration() {
        let maxEnd = 0;
        project.tracks.forEach(t => {
            t.clips.forEach(c => {
                const end = c.trackOffset + (c.end - c.start);
                if (end > maxEnd) maxEnd = end;
            });
        });
        project.duration = maxEnd;
        updateTimecodeDisplay();
        updateScrubBar();
    }

    function rebuildTrackLanes() {
        const headers = el('editorTrackHeaders'), area = el('editorTimelineTracks');
        if (!headers || !area) return;
        headers.querySelectorAll('.editor-track-header').forEach(node => node.remove());
        area.querySelectorAll('.editor-track').forEach(node => node.remove());
        project.tracks.forEach((track,index) => {
            const cls = track.type === 'audio' ? 'audio-track' : track.type === 'video' ? '' : 'text-track';
            const header = document.createElement('div'); header.className = `editor-track-header ${cls}`; header.dataset.track = index;
            header.innerHTML = `<span class="editor-track-name">${escapeHtml(track.name)}</span><div class="editor-track-icons"><button class="editor-track-icon-btn" data-action="lock" aria-pressed="${track.locked}" title="Lock">${track.locked ? '🔒' : '🔓'}</button><button class="editor-track-icon-btn" data-action="enable" aria-pressed="${track.visible !== false && !track.muted}" title="${track.type === 'audio' ? 'Mute' : 'Visibility'}">${track.type === 'audio' ? (track.muted ? '🔇' : '🔊') : (track.visible === false ? '○' : '👁')}</button></div>`;
            header.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
                if (button.dataset.action === 'lock') track.locked = !track.locked;
                else if (track.type === 'audio') track.muted = !track.muted;
                else track.visible = track.visible === false;
                pushHistory('Track settings'); renderTimeline();
            }));
            headers.insertBefore(header, headers.querySelector('.editor-add-track-btn'));
            const lane = document.createElement('div'); lane.className = `editor-track ${cls}`; lane.dataset.track = index;
            area.insertBefore(lane, el('editorPlayhead'));
        });
    }

    // ── Timeline Rendering ─────────────────────────────────────────────
    function renderTimeline() {
        const tracksArea = el('editorTimelineTracks');
        if (!tracksArea) return;

        rebuildTrackLanes();
        const pxPerSec = project.zoom;
        const totalWidth = Math.max(tracksArea.clientWidth, project.duration * pxPerSec + 200);

        // Render each track's clips
        project.tracks.forEach((track, tIdx) => {
            const trackEl = tracksArea.querySelector(`.editor-track[data-track="${tIdx}"]`);
            if (!trackEl) return;
            trackEl.style.width = `${totalWidth}px`;

            // Clear existing clips
            trackEl.querySelectorAll('.editor-clip').forEach(c => c.remove());

            track.clips.forEach(clip => {
                const clipDur = clip.end - clip.start;
                const left = clip.trackOffset * pxPerSec;
                const width = clipDur * pxPerSec;

                const div = document.createElement('div');
                div.className = `editor-clip ${clip.type}-clip${project.selectedClipIds.includes(clip.id) ? ' selected' : ''}`;
                div.dataset.clipId = clip.id;
                div.style.left = `${left}px`;
                div.style.width = `${Math.max(20, width)}px`;
                div.innerHTML = `
                    <div class="editor-clip-handle left"></div>
                    <span class="editor-clip-name">${escapeHtml(clip.name)}</span>
                    <div class="editor-clip-handle right"></div>`;

                trackEl.appendChild(div);
            });
        });

        updatePlayhead();

        // Show/hide empty state
        const hasClips = project.tracks.some(t => t.clips.length > 0);
        const empty = el('editorTimelineEmpty');
        if (empty) empty.style.display = hasClips ? 'none' : 'flex';
    }

    // ── Ruler Drawing ──────────────────────────────────────────────────
    function drawRuler() {
        const canvas = el('editorRulerCanvas');
        const container = el('editorRulerTicks');
        if (!canvas || !container) return;

        const dpr = Math.min(window.devicePixelRatio || 1, 1.25);
        const w = container.clientWidth;
        const h = container.clientHeight || 28;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;


        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, w, h);

        const pxPerSec = project.zoom;
        // Adaptive tick spacing
        let majorInterval = 1;
        if (pxPerSec < 20) majorInterval = 10;
        else if (pxPerSec < 50) majorInterval = 5;
        else if (pxPerSec < 100) majorInterval = 2;

        const scrollTime = Number(el('editorTimelineTracks')?.scrollLeft || 0) / pxPerSec;
        const totalTime = scrollTime + w / pxPerSec;

        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'center';

        for (let t = Math.floor(scrollTime / majorInterval) * majorInterval; t <= totalTime; t += majorInterval / 10) {
            const x = (t-scrollTime) * pxPerSec;
            const isMajor = Math.abs(t % majorInterval) < 0.001 || Math.abs(t % majorInterval - majorInterval) < 0.001;

            if (isMajor) {
                ctx.strokeStyle = '#4A4F5A';
                ctx.beginPath(); ctx.moveTo(x, h); ctx.lineTo(x, h - 14); ctx.stroke();
                ctx.fillStyle = '#4A4F5A';
                const sec = Math.round(t);
                const mm = String(Math.floor(sec / 60)).padStart(2, '0');
                const ss = String(sec % 60).padStart(2, '0');
                ctx.fillText(`${mm}:${ss}`, x, 10);
            } else {
                ctx.strokeStyle = '#2A2C30';
                ctx.beginPath(); ctx.moveTo(x, h); ctx.lineTo(x, h - 6); ctx.stroke();
            }
        }
    }

    // ── Clip Operations ────────────────────────────────────────────────
    function findClipById(id) {
        for (const track of project.tracks) {
            const clip = track.clips.find(c => c.id === id);
            if (clip) return clip;
        }
        return null;
    }

    function findTrackForClip(id) {
        return project.tracks.find(t => t.clips.some(c => c.id === id));
    }

    function splitAtPlayhead() {
        let didSplit = false;
        project.tracks.forEach(track => {
            if (track.locked || (project.selectedClipIds.length && !track.clips.some(c => project.selectedClipIds.includes(c.id)))) return;
            const toSplit = [];
            track.clips.forEach(clip => {
                const clipStart = clip.trackOffset;
                const clipEnd = clip.trackOffset + (clip.end - clip.start);
                if (project.playhead > clipStart + 0.05 && project.playhead < clipEnd - 0.05) {
                    toSplit.push(clip);
                }
            });
            toSplit.forEach(clip => {
                const splitPoint = project.playhead - clip.trackOffset + clip.start;
                const newClip = {
                    ...clip,
                    id: uid(),
                    mediaId: clip.mediaId,
                    name: clip.name + ' (split)',
                    type: clip.type,
                    start: splitPoint,
                    end: clip.end,
                    trackOffset: project.playhead,
                    mediaDuration: clip.mediaDuration
                };
                clip.end = splitPoint;
                track.clips.push(newClip);
                didSplit = true;
            });
        });
        if (didSplit) {
            pushHistory('Split');
            renderTimeline();
            showEditorToast('Split', 'Clip split at playhead');
        }
    }

    function deleteSelected() {
        if (project.selectedClipIds.length === 0) return;
        project.tracks.forEach(track => {
            if (!track.locked) track.clips = track.clips.filter(c => !project.selectedClipIds.includes(c.id));
        });
        pushHistory('Delete');
        project.selectedClipIds = [];
        recalcDuration();
        renderTimeline();
        updatePropertiesPanel();
    }

    function duplicateSelected() {
        project.selectedClipIds.forEach(clipId => {
            const clip = findClipById(clipId);
            const track = findTrackForClip(clipId);
            if (clip && track && !track.locked) {
                const newClip = { ...clip, id: uid(), trackOffset: clip.trackOffset + (clip.end - clip.start) };
                const available = clipSceneBounds(clip).end - newClip.trackOffset;
                if (available <= .05) { showEditorToast('Scene boundary', 'Choose a shorter clip before duplicating within this scene.'); return; }
                newClip.end = Math.min(newClip.end, newClip.start + available);
                track.clips.push(newClip);
            }
        });
        pushHistory('Duplicate');
        recalcDuration();
        renderTimeline();
    }

    // ── Undo / Redo ────────────────────────────────────────────────────
    function pushHistory(action) {
        project.history.push({ action, tracks: historyBaseline });
        historyBaseline = JSON.parse(JSON.stringify(project.tracks));
        if (remotionManifest) mountRemotionPreview();
        project.redoStack = [];
        if (project.history.length > 50) project.history.shift();
    }

    function undo() {
        if (project.history.length === 0) return;
        const current = JSON.parse(JSON.stringify(project.tracks));
        project.redoStack.push({ action: 'undo', tracks: current });
        const prev = project.history.pop();
        project.tracks = prev.tracks;
        historyBaseline = JSON.parse(JSON.stringify(project.tracks));
        mountRemotionPreview();
        recalcDuration();
        renderTimeline();
        showEditorToast('Undo', prev.action);
    }

    function redo() {
        if (project.redoStack.length === 0) return;
        const current = JSON.parse(JSON.stringify(project.tracks));
        project.history.push({ action: 'redo', tracks: current });
        const next = project.redoStack.pop();
        project.tracks = next.tracks;
        historyBaseline = JSON.parse(JSON.stringify(project.tracks));
        mountRemotionPreview();
        recalcDuration();
        renderTimeline();
        showEditorToast('Redo', 'Restored');
    }

    // ── Add Track ──────────────────────────────────────────────────────
    function addTrack() {
        if (remotionManifest) { showEditorToast('Nine production tracks available', 'Use the scene controls to change graphics; add media or effects to their existing tracks.'); return; }
        const trackCount = project.tracks.length;
        const type = trackCount % 3 === 0 ? 'video' : trackCount % 3 === 1 ? 'audio' : 'text';
        const prefix = type === 'video' ? 'V' : type === 'audio' ? 'A' : 'T';
        const num = project.tracks.filter(t => t.type === type).length + 1;

        project.tracks.push({
            id: `${prefix.toLowerCase()}${num}`,
            type, name: `${prefix}${num}`,
            clips: [], locked: false, visible: true, muted: false
        });

        // Rebuild track headers and track areas
        const headers = el('editorTrackHeaders');
        const tracksArea = el('editorTimelineTracks');
        if (!headers || !tracksArea) return;

        const tIdx = project.tracks.length - 1;
        const trackClass = type === 'audio' ? 'audio-track' : type === 'text' ? 'text-track' : '';

        // Add header
        const addBtn = headers.querySelector('.editor-add-track-btn');
        const headerDiv = document.createElement('div');
        headerDiv.className = `editor-track-header ${trackClass}`;
        headerDiv.dataset.track = tIdx;
        headerDiv.innerHTML = `
            <span class="editor-track-name">${prefix}${num}</span>
            <div class="editor-track-icons">
                ${type === 'audio' ? '<button class="editor-track-icon-btn" data-tooltip="Mute">🔇</button>' : ''}
                <button class="editor-track-icon-btn" data-tooltip="Lock">🔒</button>
                <button class="editor-track-icon-btn" data-tooltip="Visibility">👁</button>
            </div>`;
        headers.insertBefore(headerDiv, addBtn);

        // Add track lane
        const playhead = el('editorPlayhead');
        const trackDiv = document.createElement('div');
        trackDiv.className = `editor-track ${trackClass}`;
        trackDiv.dataset.track = tIdx;
        tracksArea.insertBefore(trackDiv, playhead);

        showEditorToast('Track Added', `${prefix}${num} (${type})`);
    }

    // ── Properties Panel ───────────────────────────────────────────────
    function updatePropertiesPanel() {
        const propName = el('edPropName');
        if (!propName) return;

        if (project.selectedClipIds.length === 0) {
            propName.textContent = project.name;
            return;
        }

        const clip = findClipById(project.selectedClipIds[0]);
        if (clip) {
            propName.textContent = clip.name;
        }
    }

    // ── Toggles ────────────────────────────────────────────────────────
    function setupToggles() {
        document.querySelectorAll('.editor-toggle').forEach(toggle => {
            toggle.addEventListener('click', function() {
                this.classList.toggle('on');
            });
        });
    }

    // ── Context Menu ───────────────────────────────────────────────────
    function setupContextMenu() {
        const tracksArea = el('editorTimelineTracks');
        if (!tracksArea) return;

        tracksArea.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            const clipEl = e.target.closest('.editor-clip');

            // Remove existing context menu
            document.querySelector('.editor-context-menu')?.remove();

            const menu = document.createElement('div');
            menu.className = 'editor-context-menu';

            if (clipEl) {
                const clipId = clipEl.dataset.clipId;
                if (!project.selectedClipIds.includes(clipId)) {
                    project.selectedClipIds = [clipId];
                    renderTimeline();
                }
                menu.innerHTML = `
                    <div class="editor-context-item" data-action="split">Split <span class="editor-context-shortcut">Ctrl+B</span></div>
                    <div class="editor-context-item" data-action="duplicate">Duplicate <span class="editor-context-shortcut">Ctrl+D</span></div>
                    <div class="editor-context-sep"></div>
                    <div class="editor-context-item" data-action="speed">Speed & Duration</div>
                    <div class="editor-context-item" data-action="rename">Rename</div>
                    <div class="editor-context-sep"></div>
                    <div class="editor-context-item danger" data-action="delete">Delete <span class="editor-context-shortcut">Del</span></div>`;
            } else {
                menu.innerHTML = `
                    <div class="editor-context-item" data-action="paste">Paste <span class="editor-context-shortcut">Ctrl+V</span></div>
                    <div class="editor-context-item" data-action="selectAll">Select All <span class="editor-context-shortcut">Ctrl+A</span></div>`;
            }

            menu.style.left = `${e.clientX}px`;
            menu.style.top = `${e.clientY}px`;
            document.body.appendChild(menu);

            menu.addEventListener('click', (ce) => {
                const item = ce.target.closest('.editor-context-item');
                if (!item) return;
                const action = item.dataset.action;
                if (action === 'split') splitAtPlayhead();
                else if (action === 'delete') deleteSelected();
                else if (action === 'duplicate') duplicateSelected();
                else if (action === 'selectAll') {
                    project.selectedClipIds = [];
                    project.tracks.forEach(t => t.clips.forEach(c => project.selectedClipIds.push(c.id)));
                    renderTimeline();
                }
                menu.remove();
            });

            // Close on click outside
            setTimeout(() => {
                document.addEventListener('click', function closeMenu() {
                    menu.remove();
                    document.removeEventListener('click', closeMenu);
                });
            }, 10);
        });
    }

    // ── Keyboard Shortcuts ─────────────────────────────────────────────
    function setupKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            // Only handle when editor is active
            if (AppState.currentSection !== 'editor') return;
            // Don't intercept when typing in inputs
            if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;

            const key = e.key;
            const ctrl = e.ctrlKey || e.metaKey;
            const shift = e.shiftKey;

            switch (true) {
                case key === ' ':
                    e.preventDefault(); togglePlayback(); break;
                case key === 'k' || key === 'K':
                    if (project.isPlaying) togglePlayback(); break;
                case key === 'ArrowLeft' && shift:
                    e.preventDefault(); seekRelative(-5); break;
                case key === 'ArrowRight' && shift:
                    e.preventDefault(); seekRelative(5); break;
                case key === 'ArrowLeft':
                    e.preventDefault(); seekRelative(-1 / project.fps); break;
                case key === 'ArrowRight':
                    e.preventDefault(); seekRelative(1 / project.fps); break;
                case key === 'z' && ctrl && shift:
                    e.preventDefault(); redo(); break;
                case key === 'z' && ctrl:
                    e.preventDefault(); undo(); break;
                case key === 's' && ctrl:
                    e.preventDefault(); saveProject(); break;
                case key === 'd' && ctrl:
                    e.preventDefault(); duplicateSelected(); break;
                case key === 'b' && ctrl:
                    e.preventDefault(); splitAtPlayhead(); break;
                case key === 'Delete' || key === 'Backspace':
                    e.preventDefault(); deleteSelected(); break;
                case key === 'Escape':
                    project.selectedClipIds = [];
                    renderTimeline(); updatePropertiesPanel(); break;
                case key === 'f' || key === 'F':
                    fitTimeline(); break;
                case key === '=' && ctrl:
                case key === '+' && ctrl:
                    e.preventDefault();
                    project.zoom = Math.min(500, project.zoom + 20);
                    el('edZoomSlider').value = project.zoom;
                    renderTimeline(); drawRuler(); break;
                case key === '-' && ctrl:
                    e.preventDefault();
                    project.zoom = Math.max(10, project.zoom - 20);
                    el('edZoomSlider').value = project.zoom;
                    renderTimeline(); drawRuler(); break;
            }
        });
    }

    // ── Panel Resizing ─────────────────────────────────────────────────
    function setupResizablePanels() {
        // Timeline resize (drag top edge)
        const timeline = el('editorTimeline');
        if (timeline) {
            let resizing = false, startY = 0, startH = 0;
            timeline.addEventListener('mousedown', (e) => {
                if (e.offsetY > 4) return;
                resizing = true;
                startY = e.clientY;
                startH = timeline.offsetHeight;
                document.body.style.cursor = 'row-resize';
                e.preventDefault();
            });
            document.addEventListener('mousemove', (e) => {
                if (!resizing) return;
                const dy = startY - e.clientY;
                const newH = Math.max(140, Math.min(window.innerHeight * 0.6, startH + dy));
                timeline.style.minHeight = `${newH}px`;
                timeline.style.maxHeight = `${newH}px`;
            });
            document.addEventListener('mouseup', () => {
                if (resizing) { resizing = false; document.body.style.cursor = ''; }
            });
        }
    }

    // ── Save / Export ──────────────────────────────────────────────────
    async function readProjectFile(file) {
        try {
            const data = JSON.parse(await file.text());
            if (!data || !Array.isArray(data.tracks) || !data.tracks.every(track => Array.isArray(track.clips))) throw new Error('This file does not contain a valid editing project.');
            loadManifest(data);
        } catch (error) { showEditorToast('Project could not open', error.message); }
    }
    function openProject() {
        const input = document.createElement('input'); input.type = 'file'; input.accept = '.vidzeo';
        input.onchange = () => { if (input.files?.[0]) readProjectFile(input.files[0]); }; input.click();
    }

    function saveProject() {
        const data = {
            name: el('editorProjectName')?.value || project.name,
            fps: project.fps,
            resolution: project.resolution,
            duration: project.duration,
            tracks: project.tracks,
            mediaPool: project.mediaPool.map(m => ({
                id: m.id, path: m.path, basename: m.basename,
                type: m.type, duration: m.duration
            })),
            remotion_manifest: remotionManifest ? window.VidzeoTimeline.exportTracks(remotionManifest, project.tracks) : null
        };

        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${data.name.replace(/\s+/g, '_')}.vidzeo`;
        a.click();
        URL.revokeObjectURL(url);
        showEditorToast('Project Saved', `${data.name}.vidzeo`);
    }

    // ── Load Manifest from Generation Pipeline ─────────────────────────
    function loadManifest(manifest) {
        if (!manifest || typeof manifest !== 'object') return;

        remotionManifest = manifest.remotion_manifest && typeof manifest.remotion_manifest === 'object'
            ? JSON.parse(JSON.stringify(manifest.remotion_manifest))
            : (Array.isArray(manifest.scenes) ? JSON.parse(JSON.stringify(manifest)) : null);
        selectedRemotionSceneIndex = 0;
        window._editorSourceClips = Array.isArray(manifest.video_clips) ? manifest.video_clips : [];
        remotionVoiceoverUrl = String(manifest.audio_url || '');
        remotionMusicUrl = String(manifest.music_url || '');
        const editSource = remotionManifest || manifest;

        // Reset project
        project.name = manifest.name || 'Imported Project';
        project.duration = editSource.duration || manifest.duration || 0;
        project.fps = editSource.fps || manifest.fps || 30;
        if (editSource.width && editSource.height) {
            project.resolution = { w: Number(editSource.width), h: Number(editSource.height) };
        } else if (manifest.width && manifest.height) {
            project.resolution = { w: Number(manifest.width), h: Number(manifest.height) };
        } else if (manifest.resolution && typeof manifest.resolution === 'object') {
            project.resolution = {
                w: Number(manifest.resolution.w || 1920),
                h: Number(manifest.resolution.h || 1080)
            };
        }
        project.tracks.forEach(t => t.clips = []);
        project.mediaPool = [];
        project.history = [];
        project.redoStack = [];
        project.selectedClipIds = [];
        _clipIdCounter = 0;

        // Update project name in toolbar
        const nameEl = el('editorProjectName');
        if (nameEl) nameEl.value = project.name;

        // ── V1 Track: Video Clips / Scene Images ────────────────────
        const v1Track = project.tracks.find(t => t.id === 'media');
        const semanticScenes = Array.isArray(editSource.scenes) ? editSource.scenes : [];
        const videoClips = Array.isArray(manifest.video_clips) && manifest.video_clips.length
            ? manifest.video_clips
            : semanticScenes.map((scene, i) => {
                const media = scene?.media || {};
                const duration = Number(scene?.duration || 0) || 2;
                return {
                    path: media.path || media.src || '',
                    url: media.url || media.src || '',
                    start: 0,
                    end: duration,
                    track_offset: Number(scene?.start || 0) || 0,
                    fit: media.fit || 'contain',
                    position: media.position || 'center',
                    safe_framing: media.safe_framing !== false,
                    subject_bbox: media.subject_bbox || media.focal_bbox || scene?.design?.focal_bbox || '',
                    design: scene?.design || {},
                    scene_id: scene?.id || `scene_${i + 1}`
                };
            });

        videoClips.forEach((clip, i) => {
            const clipDur = (clip.end - clip.start) || 2;
            const ext = (clip.path || '').split('.').pop().toLowerCase();
            const type = ['mp4','mov','avi','mkv','webm','wmv','flv'].includes(ext) ? 'video' : 'image';
            const basename = (clip.path || '').split(/[/\\]/).pop() || `Scene ${i + 1}`;

            // Add to media pool
            const mediaItem = {
                id: uid(),
                path: clip.path || '',
                basename,
                url: clip.url || (clip.path ? `local-media://${clip.path.replace(/\\/g, '/')}` : ''),
                type,
                duration: clipDur,
                width: 1920,
                height: 1080,
                fit: clip.fit || 'contain',
                position: clip.position || 'center',
                safe_framing: clip.safe_framing !== false,
                subject_bbox: clip.subject_bbox || clip.focal_bbox || '',
                design: clip.design || {}
            };
            project.mediaPool.push(mediaItem);

            // Add clip to V1 track
            if (v1Track) {
                v1Track.clips.push({
                    id: uid(),
                    mediaId: mediaItem.id,
                    trackOffset: clip.track_offset || 0,
                    start: clip.start || 0,
                    end: clip.start + clipDur,
                    name: basename
                });
            }
        });

        // Update total project duration from clips
        if (v1Track && v1Track.clips.length > 0) {
            const lastClip = v1Track.clips[v1Track.clips.length - 1];
            project.duration = Math.max(project.duration, lastClip.trackOffset + (lastClip.end - lastClip.start));
        }

        // ── A1 Track: Voiceover Audio ────────────────────────────────
        const a1Track = project.tracks.find(t => t.id === 'narration');
        if (a1Track && manifest.audio_url) {
            const audioBasename = (manifest.audio_path || '').split(/[/\\]/).pop() || 'Voiceover.wav';
            const audioDur = manifest.duration || 0;

            const audioMedia = {
                id: uid(),
                path: manifest.audio_path || '',
                basename: audioBasename,
                url: manifest.audio_url,
                type: 'audio',
                duration: audioDur,
                width: 0,
                height: 0
            };
            project.mediaPool.push(audioMedia);

            a1Track.clips.push({
                id: uid(),
                mediaId: audioMedia.id,
                trackOffset: 0,
                start: 0,
                end: audioDur,
                name: audioBasename
            });
        }

        // ── T1 Track: Script text segments ───────────────────────────
        const t1Track = project.tracks.find(t => t.id === 'captions');
        if (t1Track && manifest.script) {
            // Split script into rough segments matching scene count
            const sentences = manifest.script.split(/(?<=[.!?])\s+/).filter(s => s.trim().length > 0);
            const totalScenes = videoClips.length || 1;
            const sentencesPerScene = Math.max(1, Math.ceil(sentences.length / totalScenes));

            videoClips.forEach((clip, i) => {
                const segStart = i * sentencesPerScene;
                const segEnd = Math.min(segStart + sentencesPerScene, sentences.length);
                const text = sentences.slice(segStart, segEnd).join(' ').substring(0, 80);
                if (!text) return;

                const clipDur = (clip.end - clip.start) || 2;
                t1Track.clips.push({
                    id: uid(),
                    mediaId: null,
                    trackOffset: clip.track_offset || 0,
                    start: 0,
                    end: clipDur,
                    name: text
                });
            });
        }

        if (remotionManifest) {
            project.tracks = window.VidzeoTimeline.importTracks(remotionManifest);
            if (Array.isArray(manifest.tracks) && manifest.tracks.every(t => Array.isArray(t.clips))) project.tracks = JSON.parse(JSON.stringify(manifest.tracks));
        }
        if (!remotionManifest && Array.isArray(manifest.tracks) && manifest.tracks.every(track => Array.isArray(track.clips))) project.tracks = JSON.parse(JSON.stringify(manifest.tracks));
        if (Array.isArray(manifest.mediaPool)) project.mediaPool = manifest.mediaPool.map(media => ({...media,url:media.url || (media.path ? `local-media://${media.path.replace(/\\/g,'/')}` : '')}));
        historyBaseline = JSON.parse(JSON.stringify(project.tracks));
        // Re-render
        rebuildMediaPanel();
        renderTimeline();
        drawRuler();
        updateTimecodeDisplay();

        if (remotionManifest) {
            rebuildRemotionTiming();
            mountRemotionPreview();
            renderRemotionSceneTimeline();
            populateRemotionSceneControls();
        } else {
            el('editorRemotionPlayer')?.classList.remove('is-active');
            el('editorCanvas')?.classList.remove('remotion-active');
            if (el('editorPlaceholder')) el('editorPlaceholder').style.display = '';
            const strip = el('remotionSceneTimeline'); if (strip) strip.innerHTML = '';
        }

        showEditorToast('Project Loaded', `${videoClips.length} scenes, ${manifest.duration?.toFixed(1)}s`);
        console.log('✂️ Manifest loaded:', manifest.name, `— ${videoClips.length} clips`);
    }

    // ── Export / Render ────────────────────────────────────────────────
    async function exportProject() {
        const btn = el('editorExportBtn');
        const origText = btn ? btn.textContent : 'Export';
        try {
            if (btn) {
                btn.disabled = true;
                btn.textContent = '⏳ Rendering...';
            }
            showEditorToast('Export Started', 'Rendering timeline with hardware acceleration...', 5000);

            const payload = {
                name: project.name || 'Edited Video',
                fps: project.fps || 30,
                resolution: project.resolution || { w: 1920, h: 1080 },
                duration: project.duration || 0,
                tracks: project.tracks || [],
                mediaPool: project.mediaPool || []
            };
            if (remotionManifest) {
                rebuildRemotionTiming();
                const exportManifest = window.VidzeoTimeline.exportTracks(remotionManifest, project.tracks);
                exportManifest.name = project.name || exportManifest.name || 'Edited Video';
                exportManifest.title = exportManifest.name;
                exportManifest.fps = Number(project.fps || exportManifest.fps || 30);
                exportManifest.width = Number(project.resolution?.w || exportManifest.width || 1920);
                exportManifest.height = Number(project.resolution?.h || exportManifest.height || 1080);
                exportManifest.duration = project.duration;
                exportManifest.duration_in_frames = Math.round(project.duration * exportManifest.fps);
                exportManifest.config = { ...(exportManifest.config || {}), render_backend: 'remotion', semantic_render_required: true, quality: 'standard' };
                payload.manifest = exportManifest;
                payload.duration = exportManifest.duration;
                payload.fps = exportManifest.fps;
                payload.resolution = { w: exportManifest.width, h: exportManifest.height };
            }

            const response = await fetch('/api/editor/render', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await response.json();
            if (!response.ok || !data.success) {
                throw new Error(data.error || 'Export failed');
            }

            const release = data.render_metadata?.release_review;
            const reviewPending = release && !release.ready_to_publish;
            showEditorToast(reviewPending ? 'Exported · Review needed' : 'Export Complete', `${data.render_metadata?.selected_backend || 'Remotion'} · ${data.filename || data.url}${reviewPending ? ' · Creative or factual review remains pending.' : ''}`, 8000);
            if (data.url) {
                if (remotionManifest) {
                    el('editorRemotionPlayer')?.classList.remove('is-active');
                    el('editorCanvas')?.classList.remove('remotion-active');
                }
                const videoEl = el('editorVideo');
                if (videoEl) {
                    videoEl.src = data.url;
                    videoEl.load();
                    videoEl.play().catch(() => {});
                }
            }
        } catch (err) {
            console.error('Editor export error:', err);
            showEditorToast('Export Failed', err.message || 'Render error occurred', 6000);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = origText;
            }
        }
    }

    // ── Window Resize Handler ──────────────────────────────────────────
    window.addEventListener('resize', () => {
        if (typeof AppState !== 'undefined' && AppState.currentSection === 'editor') {
            drawRuler();
            renderTimeline();
        }
    });

    // ── Public API ─────────────────────────────────────────────────────
    return { init, loadManifest, exportProject, saveProject, openProject, addTrack, project };
})();
