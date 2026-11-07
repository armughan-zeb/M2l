const fs = require('fs');
const path = require('path');

// Mock browser globals for EditorController
global.window = { addEventListener: () => {}, VidzeoTimeline: require('../src/production-timeline') };
global.document = {
    addEventListener: () => {},
    removeEventListener: () => {},
    getElementById: (id) => ({
        id,
        classList: { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} },
        addEventListener: () => {},
        appendChild: () => {},
        querySelectorAll: () => [],
        querySelector: () => null,
        getBoundingClientRect: () => ({ width: 1200, height: 600, x: 0, y: 0 }),
        getContext: () => ({ fillRect: () => {}, strokeText: () => {}, beginPath: () => {}, moveTo: () => {}, lineTo: () => {}, stroke: () => {}, scale: () => {}, clearRect: () => {} }),
        value: '',
        dataset: {},
        style: { setProperty: () => {} },
        insertAdjacentHTML: () => {},
    }),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({ className: '', innerHTML: '', dataset: {}, appendChild: () => {}, style: {}, addEventListener: () => {} }),
    body: { classList: { add: () => {}, remove: () => {} }, appendChild: () => {} }
};
global.AppState = { currentSection: 'editor' };
global.setTimeout = () => 0;
// This contract exercises plan import/export, without rendering editor chrome.
global.document.getElementById = id => id === 'editorRemotionPlayer'
    ? { isConnected: true, classList: { add() {}, remove() {} } } : null;
global.requestAnimationFrame = callback => { callback(); return 0; };

// Read and eval editor.js
const editorCode = fs.readFileSync(path.join(__dirname, '..', 'src', 'editor.js'), 'utf-8');
const EditorController = eval(editorCode + '; EditorController;');

// Keep the editor contract repeatable when users clear generated outputs.
const manifest = {
    name: 'Editor contract fixture', duration: 8, fps: 30,
    video_clips: Array.from({length: 4}, (_, i) => ({
        start: 0, end: 2, track_offset: i * 2, path: `fixture-${i}.jpg`,
    })),
};
manifest.remotion_manifest = {
    version: 2,
    edit_plan_schema_version: 3,
    fps: 30,
    width: 1920,
    height: 1080,
    duration: 8,
    duration_in_frames: 240,
    scenes: [{
        id: 'shared-plan-scene',
        scene_index: 0,
        start: 0,
        duration: 8,
        duration_in_frames: 240,
        narration: 'A test scene from the edit plan.',
        primary_visual: { type: 'ChapterTitle', variant: 'cinematic_minimal_act', props: { title: 'THE SHARED PLAN' } },
        camera: { motion: 'slow_push' },
        transition_out: 'hard_cut',
    }],
};

let previewManifest = null;
let exportedPayload = null;
global.window.VidzeoRemotion = {
    mountPlayer: (_host, incomingManifest) => {
        previewManifest = incomingManifest;
        return { seekTo: () => {} };
    },
};
global.fetch = async (_url, options) => {
    exportedPayload = JSON.parse(options.body);
    return { ok: true, json: async () => ({ success: true, filename: 'smoke.mp4' }) };
};

async function verify() {
    EditorController.init();
    EditorController.loadManifest(manifest);
    await EditorController.exportProject();

    const project = EditorController.project;
    console.log('=== EDITOR CONTROLLER VERIFICATION ===');
    console.log('Project Name:', project.name);
    console.log('Duration:', project.duration);
    console.log('Tracks Count:', project.tracks.length);

    const v1 = project.tracks.find(t => t.id === 'media');
    const a1 = project.tracks.find(t => t.id === 'narration');
    const t1 = project.tracks.find(t => t.id === 'copy');

    console.log('V1 Video Clips Count:', v1 ? v1.clips.length : 0);
    console.log('A1 Audio Clips Count:', a1 ? a1.clips.length : 0);
    console.log('T1 Text Clips Count:', t1 ? t1.clips.length : 0);
    console.log('Media Pool Size:', project.mediaPool.length);

    if (!v1 || v1.clips.length !== 1) throw new Error('Expected the versioned scene plan to own the media track');
    if (typeof EditorController.exportProject !== 'function') throw new Error('exportProject is not exposed');
    if (!previewManifest || previewManifest.scenes[0].id !== 'shared-plan-scene') throw new Error('Player did not receive the versioned scene plan');
    if (!exportedPayload?.manifest || exportedPayload.manifest.scenes[0].id !== 'shared-plan-scene') throw new Error('Export did not carry the previewed scene plan');
    if (exportedPayload.manifest.config.render_backend !== 'remotion' || exportedPayload.manifest.config.semantic_render_required !== true) throw new Error('Export did not require Remotion');

    console.log('PASS: Remotion preview and export use the same versioned plan, and export requires Remotion.');
}

verify().catch(error => {
    console.error('FAIL:', error.message || error);
    process.exitCode = 1;
});
