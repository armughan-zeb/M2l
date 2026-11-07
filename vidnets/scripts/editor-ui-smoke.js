const { app, BrowserWindow } = require('electron');
const path = require('path');

app.whenReady().then(async () => {
    const win = new BrowserWindow({
        width: 1440,
        height: 960,
        show: true,
        webPreferences: { contextIsolation: true, nodeIntegration: false }
    });
    await win.loadFile(path.join(__dirname, '..', 'src', 'index.html'));
    await new Promise(resolve => setTimeout(resolve, 1800));
    await win.webContents.executeJavaScript(`
        AppState.account.accessState = 'active';
        navigateTo('editor');
        EditorController.init();
        EditorController.loadManifest({
            name: 'Documentary Editor Smoke Test', fps: 30, duration: 18,
            video_clips: [
                {path:'scene-a.jpg',url:'',start:0,end:6,track_offset:0,source:'Scene A'},
                {path:'scene-b.jpg',url:'',start:0,end:6,track_offset:6,source:'Scene B'},
                {path:'scene-c.jpg',url:'',start:0,end:6,track_offset:12,source:'Scene C'}
            ],
            source_scene_texts: ['On March 14, 2024 the case began.', 'The deal reached $3 billion.', 'The final discovery changed everything.'],
            documentary_edit_plan: {
                enabled:true, duration:18,
                scenes:[
                    {start:0,duration:6,date_card:'MARCH 14, 2024',camera_motion:'zoom_in',color_grade:'investigative'},
                    {start:6,duration:6,money_counter:{label:'$3 BILLION'},camera_motion:'pan_left',color_grade:'investigative'},
                    {start:12,duration:6,key_highlight:'THE FINAL DISCOVERY',camera_motion:'zoom_out',color_grade:'investigative'}
                ],
                tracks:{
                    overlays:[{start:0,duration:4,label:'MARCH 14, 2024',type:'date'},{start:6,duration:4,label:'$3 BILLION',type:'money'}],
                    highlights:[{start:12,duration:3,label:'THE FINAL DISCOVERY'}],
                    transitions:[{start:6,duration:.25,type:'dip_to_black'}],
                    grade:[{start:0,duration:18,type:'investigative'}]
                }
            }
        });
        true;
    `);
    await new Promise(resolve => setTimeout(resolve, 800));
    await win.webContents.executeJavaScript(`
        document.querySelectorAll('.section').forEach(section => section.classList.toggle('active', section.id === 'section-editor'));
        document.body.classList.add('editor-mode');
        document.querySelector('[data-tab="templates"]').click();
        document.querySelector('[data-style="archival"]').click();
        document.querySelector('[data-tab="history"]').click();
        window.__editorSmokeHistoryItems = document.querySelectorAll('.editor-history-item').length;
        document.querySelector('[data-tab="overlays"]').click();
        document.querySelector('.editor-clip.overlay-clip')?.click();
    `);
    const state = await win.webContents.executeJavaScript(`({
        active: document.getElementById('section-editor').classList.contains('active'),
        tracks: document.querySelectorAll('.editor-track').length,
        clips: document.querySelectorAll('.editor-clip').length,
        width: document.querySelector('.editor-root').getBoundingClientRect().width,
        height: document.querySelector('.editor-root').getBoundingClientRect().height,
        rootDisplay: getComputedStyle(document.querySelector('.editor-root')).display,
        rootChildren: document.querySelector('.editor-root').children.length,
        tabs: Array.from(document.querySelectorAll('.editor-media-tab')).map(tab => tab.dataset.tab),
        checklist: document.querySelectorAll('#edRenderChecklist .editor-check').length,
        controls: ['edDocStyle','edDocDensity','edDocTexture','edDocFont','edCaptionFont','edCaptionSize','edCaptionColor','edTextTemplate','edTextAnimation','edTextBackground','edTransitionStyle','edGradeStrength','edVoiceVolume','edCaptionsToggle','edClipText','edClipFont','edClipAnimation','edClipX','edClipY'].every(id => Boolean(document.getElementById(id))),
        templateApplied: EditorController.project.settings.style === 'archival',
        historyItems: window.__editorSmokeHistoryItems,
        visualCards: document.querySelectorAll('.editor-visual-card').length,
        selectionInspector: !document.getElementById('editorSelectionInspector').classList.contains('hidden'),
        timelineTracks: Array.from(document.querySelectorAll('.editor-track-header')).map(track => track.textContent.trim()),
        toolbar: (() => { const r=document.querySelector('.editor-toolbar').getBoundingClientRect(); return [r.x,r.y,r.width,r.height]; })(),
        left: (() => { const r=document.querySelector('.editor-left').getBoundingClientRect(); return [r.x,r.y,r.width,r.height]; })(),
        preview: (() => { const r=document.querySelector('.editor-preview').getBoundingClientRect(); return [r.x,r.y,r.width,r.height]; })()
    })`);
    if (!state.active || state.tracks < 8 || state.clips < 12 || !state.controls || !state.templateApplied
        || state.historyItems < 2 || state.visualCards < 5 || !state.selectionInspector || state.checklist !== 5
        || !state.tabs.includes('overlays') || !state.tabs.includes('animations') || !state.tabs.includes('history')) {
        throw new Error(`Editor UI contract failed: ${JSON.stringify(state)}`);
    }
    const image = await win.webContents.capturePage();
    const output = path.join(__dirname, '..', 'temp', 'editor-ui-smoke.png');
    require('fs').mkdirSync(path.dirname(output), { recursive: true });
    require('fs').writeFileSync(output, image.toPNG());
    process.stdout.write(JSON.stringify({ ...state, output }));
    await win.close();
    app.quit();
}).catch(error => {
    process.stderr.write(error.stack || error.message);
    app.exit(1);
});
