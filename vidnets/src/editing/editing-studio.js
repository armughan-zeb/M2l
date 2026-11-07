/**
 * Vidzeo Editing Studio Controller
 * Full sidebar settings integration with all asset browsers, previews & sync
 */
const EditingStudio = {
  currentTab: 'general',
  isInitialized: false,
  PREFS_KEY: 'vidzeo_editing_prefs',

  prefs: {
    template: 'tpl-docu', font: 'font-montserrat',
    transition: 'tr-fade', effect: 'ef-cg-cinematic',
    animation: 'anim-fadein', overlay: 'ovl-lt-min',
    soundEffect: null, videoFormat: 'fmt-16x9',
    colorPalette: null, sfxVolume: 50, musicVolume: 50,
    voiceSpeed: 1.0, brandColorPrimary: '#6366F1',
    brandColorAccent: '#8B5CF6', backgroundColor: '#0A0D18'
  },

  init() {
    if (this.isInitialized) return;
    this.loadPrefs();
    this.injectKeyframes();
    this.loadGoogleFonts();
    if (window.EditingAssets) this.renderAllPages();
    this.bindTabEvents();
    this.bindGlobalInputs();
    this.switchTab('general', true);
    this.syncHiddenInput();
    this.isInitialized = true;
    console.log('[EditingStudio] Initialized');
  },

  renderAllPages() {
    this.renderTemplatesPage();
    this.renderFontsPage();
    this.renderTransitionsPage();
    this.renderEffectsPage();
    this.renderAnimationsPage();
    this.renderOverlaysPage();
    this.renderAudioPage();
    this.renderCanvasPage();
    this.renderAssetsPage();
  },

  switchTab(tabName, force) {
    if (this.currentTab === tabName && !force) return;
    const oldBtn = document.querySelector(`.vsm-tab-btn[data-target="${this.currentTab}"]`);
    const oldPage = document.getElementById(`vsm-page-${this.currentTab}`);
    if (oldBtn) oldBtn.classList.remove('active');
    if (oldPage) { oldPage.classList.remove('active'); oldPage.style.display = 'none'; }
    this.currentTab = tabName;
    const newBtn = document.querySelector(`.vsm-tab-btn[data-target="${tabName}"]`);
    const newPage = document.getElementById(`vsm-page-${tabName}`);
    if (newBtn) newBtn.classList.add('active');
    if (newPage) { newPage.style.display = 'block'; setTimeout(() => newPage.classList.add('active'), 10); }
  },

  bindTabEvents() {
    document.querySelectorAll('.vsm-tab-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        const t = e.currentTarget.dataset.target;
        if (t) this.switchTab(t);
      });
    });
  },

  bindGlobalInputs() {
    const mVol = document.getElementById('settingMusicVolume');
    if (mVol) {
      mVol.value = this.prefs.musicVolume;
      mVol.addEventListener('input', e => {
        this.prefs.musicVolume = parseInt(e.target.value);
        this.savePrefs();
      });
    }
  },

  // ── PAGE RENDERERS ──────────────────────────────────────────────────

  renderTemplatesPage() {
    const c = document.getElementById('vsm-page-templates');
    if (!c) return;
    const items = window.EditingAssets?.TEMPLATES || [];
    let html = this.pageHeader('Video Templates', 'Editorial styles & themes for your video', true, 'templates');
    html += `<div class="vsm-grid" id="vsm-grid-templates">`;
    items.forEach(t => {
      const sel = this.prefs.template === t.id ? 'vsm-selected' : '';
      html += `<div class="vsm-card ${sel}" data-id="${t.id}">
        <div class="vsm-card-preview vsm-preview-template">
          <div class="vsm-template-swatch"><div class="vsm-template-swatch-inner" style="background:linear-gradient(135deg,${t.colorAccent},${t.colorPrimary})"></div></div>
        </div>
        <div class="vsm-card-body">
          <h4 class="vsm-card-title">${t.label}</h4>
          <p class="vsm-card-desc">${t.description}</p>
          <div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap;">
            ${(t.tags||[]).map(tag => `<span class="vsm-badge">${tag}</span>`).join('')}
          </div>
        </div>
        <div class="vsm-check-icon">✓</div>
      </div>`;
    });
    html += `</div>`;
    c.innerHTML = html;
    this.bindGridSelection('vsm-grid-templates', id => this.selectTemplate(id));
  },

  renderFontsPage() {
    const c = document.getElementById('vsm-page-fonts');
    if (!c) return;
    const items = window.EditingAssets?.FONTS || [];
    let html = this.pageHeader('Typography & Fonts', 'Choose your display and caption fonts', true, 'fonts');
    html += `<div class="vsm-grid vsm-grid-small" id="vsm-grid-fonts">`;
    const previewText = 'ABCabc 123';
    items.forEach(f => {
      const sel = this.prefs.font === f.id ? 'vsm-selected' : '';
      html += `<div class="vsm-card vsm-card-font ${sel}" data-id="${f.id}">
        <div class="vsm-card-preview" style="font-family:${f.family};"><span class="vsm-font-preview-text">Aa</span></div>
        <div class="vsm-card-body">
          <h4 class="vsm-card-title">${f.label}</h4>
          <p class="vsm-card-desc" style="font-family:${f.family};font-size:11px;">${previewText}</p>
          <span class="vsm-badge" style="margin-top:4px;display:inline-block;">${f.category||'Sans'}</span>
        </div>
        <div class="vsm-check-icon">✓</div>
      </div>`;
    });
    html += `</div>`;
    c.innerHTML = html;
    this.bindGridSelection('vsm-grid-fonts', id => this.selectFont(id));
  },

  renderTransitionsPage() {
    const c = document.getElementById('vsm-page-transitions');
    if (!c) return;
    const items = window.EditingAssets?.TRANSITIONS || [];
    let html = this.pageHeader('Video Transitions', 'How clips blend together');
    html += `<div class="vsm-grid" id="vsm-grid-transitions">`;
    items.forEach(t => {
      const sel = this.prefs.transition === t.id ? 'vsm-selected' : '';
      html += `<div class="vsm-card ${sel}" data-id="${t.id}">
        <div class="vsm-card-preview vsm-preview-transition">
          <div class="vsm-transition-icon"></div>
        </div>
        <div class="vsm-card-body">
          <h4 class="vsm-card-title">${t.label}</h4>
          <p class="vsm-card-desc">${t.description}</p>
          <span class="vsm-badge">${(t.duration||0)+'ms'}</span>
        </div>
        <div class="vsm-check-icon">✓</div>
      </div>`;
    });
    html += `</div>`;
    c.innerHTML = html;
    this.bindGridSelection('vsm-grid-transitions', id => this.selectTransition(id));
  },

  renderEffectsPage() {
    const c = document.getElementById('vsm-page-effects');
    if (!c) return;
    const items = window.EditingAssets?.EFFECTS || [];
    let html = this.pageHeader('Visual Effects & Filters', 'Color grading, film looks & atmospheric effects');
    html += `<div class="vsm-grid" id="vsm-grid-effects">`;
    const effectColors = {
      'ef-cg-warm': ['#ff7e5f','#feb47b'], 'ef-cg-cool': ['#2193b0','#6dd5ed'],
      'ef-cg-noir': ['#2b2b2b','#757575'], 'ef-cg-vintage': ['#c94b4b','#f4d03f'],
      'ef-cg-cinematic': ['#0d3b66','#f4a261'], 'ef-bloom': ['#e8cbc0','#636fa4'],
      'ef-chromatic': ['#ff006e','#00f5d4'], 'ef-scanlines': ['#121212','#2a2a2a'],
      'ef-nightvision': ['#00ff41','#003b00'], 'ef-dreamglow': ['#c471ed','#f64f59'],
      'ef-frosted': ['#b8c6db','#f5f7fa'], 'ef-oil': ['#2c3e50','#e74c3c'],
      'ef-grain': ['#333','#666'], 'ef-vignette-light': ['#1a1a2e','#16213e'],
      'ef-vignette-dark': ['#0a0a0a','#1a1a2e']
    };
    items.forEach(t => {
      const sel = this.prefs.effect === t.id ? 'vsm-selected' : '';
      const cols = effectColors[t.id] || ['#6366f1','#8b5cf6'];
      html += `<div class="vsm-card ${sel}" data-id="${t.id}">
        <div class="vsm-card-preview vsm-preview-effect" style="--preview-color1:${cols[0]};--preview-color2:${cols[1]};">
          <div class="vsm-effect-swatch"></div>
        </div>
        <div class="vsm-card-body">
          <h4 class="vsm-card-title">${t.label}</h4>
          <p class="vsm-card-desc">${t.description}</p>
          <span class="vsm-badge">${t.category||'General'}</span>
        </div>
        <div class="vsm-check-icon">✓</div>
      </div>`;
    });
    html += `</div>`;
    c.innerHTML = html;
    this.bindGridSelection('vsm-grid-effects', id => this.selectEffect(id));
  },

  renderAnimationsPage() {
    const c = document.getElementById('vsm-page-animations');
    if (!c) return;
    const items = window.EditingAssets?.ANIMATIONS || [];
    let html = this.pageHeader('Caption Animations', 'How text appears on screen');
    html += `<div class="vsm-grid" id="vsm-grid-animations">`;
    const animStyles = {
      'anim-typewriter': { anim: 'anim-typewriter-kf', text: '|' },
      'anim-fadein': { anim: 'anim-fadein-kf', text: 'Fade' },
      'anim-fadeinup': { anim: 'anim-fadeinup-kf', text: 'Rise' },
      'anim-fadeindown': { anim: 'anim-fadeindown-kf', text: 'Drop' },
      'anim-popin': { anim: 'anim-popin-kf', text: 'POP' },
      'anim-slideleft': { anim: 'anim-slideleft-kf', text: 'Slide' },
      'anim-slideright': { anim: 'anim-slideright-kf', text: 'Slide' },
      'anim-bounce': { anim: 'anim-bounce-kf', text: 'BOUNCE' },
      'anim-flash': { anim: 'anim-flash-kf', text: 'FLASH' },
      'anim-wave': { anim: 'anim-wave-kf', text: 'Wave' },
      'anim-shake': { anim: 'anim-shake-kf', text: 'SHAKE' },
      'anim-flip': { anim: 'anim-flip-kf', text: 'FLIP' },
      'anim-rotatein': { anim: 'anim-rotatein-kf', text: 'SPIN' },
      'anim-zoomin': { anim: 'anim-zoomin-kf', text: 'ZOOM' },
      'anim-blurin': { anim: 'anim-blurin-kf', text: 'Blur' },
      'anim-glitchin': { anim: 'anim-glitchin-kf', text: 'GL1TCH' },
      'anim-neonflicker': { anim: 'anim-neonflicker-kf', text: 'NEON' },
      'anim-rainbow': { anim: 'anim-rainbow-kf', text: '🌈' },
      'anim-spotlight': { anim: 'anim-spotlight-kf', text: 'FOCUS' },
      'anim-none': { anim: '', text: 'Static' }
    };
    items.forEach(t => {
      const sel = this.prefs.animation === t.id ? 'vsm-selected' : '';
      const st = animStyles[t.id] || { anim: 'anim-fadein-kf', text: t.label };
      html += `<div class="vsm-card ${sel}" data-id="${t.id}">
        <div class="vsm-card-preview vsm-preview-animation-container">
          <div class="vsm-anim-text" style="${st.anim ? 'animation:' + st.anim + ' 1.8s infinite ease-in-out;' : ''}">${st.text}</div>
        </div>
        <div class="vsm-card-body">
          <h4 class="vsm-card-title">${t.label}</h4>
          <p class="vsm-card-desc">${t.description}</p>
        </div>
        <div class="vsm-check-icon">✓</div>
      </div>`;
    });
    html += `</div>`;
    c.innerHTML = html;
    this.bindGridSelection('vsm-grid-animations', id => this.selectAnimation(id));
  },

  renderOverlaysPage() {
    const c = document.getElementById('vsm-page-overlays');
    if (!c) return;
    const items = window.EditingAssets?.OVERLAYS || [];
    let html = this.pageHeader('Video Overlays', 'Lower thirds, bugs, markers & watermarks');
    html += `<div class="vsm-grid" id="vsm-grid-overlays">`;
    const ovlStyles = {
      'ovl-lt-min': { pos: 'flex-end', align: 'flex-start' },
      'ovl-lt-bold': { pos: 'flex-end', align: 'flex-start' },
      'ovl-lt-news': { pos: 'flex-end', align: 'flex-start' },
      'ovl-tc-cin': { pos: 'center', align: 'center' },
      'ovl-tc-mod': { pos: 'center', align: 'center' },
      'ovl-tc-inv': { pos: 'center', align: 'center' },
      'ovl-bug-sub': { pos: 'flex-end', align: 'flex-end' },
      'ovl-bug-like': { pos: 'flex-start', align: 'flex-end' },
      'ovl-mark-chap': { pos: 'flex-start', align: 'flex-start' },
      'ovl-mark-date': { pos: 'flex-end', align: 'flex-end' },
      'ovl-count-mon': { pos: 'center', align: 'center' },
      'ovl-count-stat': { pos: 'center', align: 'center' },
      'ovl-wm-logo': { pos: 'flex-start', align: 'flex-end' },
      'ovl-wm-copy': { pos: 'flex-end', align: 'flex-end' },
      'ovl-bug-news': { pos: 'flex-start', align: 'flex-start' },
      'ovl-cite': { pos: 'flex-end', align: 'flex-start' }
    };
    items.forEach(t => {
      const sel = this.prefs.overlay === t.id ? 'vsm-selected' : '';
      const st = ovlStyles[t.id] || { pos: 'flex-end', align: 'flex-start' };
      html += `<div class="vsm-card ${sel}" data-id="${t.id}">
        <div class="vsm-card-preview" style="display:flex;align-items:${st.pos};justify-content:${st.align};padding:8px;">
          <span style="background:var(--primary);color:white;font-size:9px;padding:3px 8px;border-radius:4px;font-weight:600;">${t.label}</span>
        </div>
        <div class="vsm-card-body">
          <h4 class="vsm-card-title">${t.label}</h4>
          <p class="vsm-card-desc">${t.description}</p>
          <span class="vsm-badge">${t.category||'Overlay'}</span>
        </div>
        <div class="vsm-check-icon">✓</div>
      </div>`;
    });
    html += `</div>`;
    c.innerHTML = html;
    this.bindGridSelection('vsm-grid-overlays', id => this.selectOverlay(id));
  },

  renderAudioPage() {
    const c = document.getElementById('vsm-page-audio');
    if (!c) return;
    let html = this.pageHeader('Audio & Sound Design');
    html += `<div class="vsm-audio-controls">
      <div class="vsm-control-group"><label>Background Music Volume</label>
        <div class="vsm-slider-row">
          <input type="range" id="vsm-studio-music-vol" min="0" max="100" value="${this.prefs.musicVolume}" class="vsm-slider" />
          <span class="vsm-slider-val" id="vsm-studio-music-vol-val">${this.prefs.musicVolume}%</span>
        </div>
      </div>
      <div class="vsm-control-group"><label>Sound Effects (SFX) Volume</label>
        <div class="vsm-slider-row">
          <input type="range" id="vsm-studio-sfx-vol" min="0" max="100" value="${this.prefs.sfxVolume}" class="vsm-slider" />
          <span class="vsm-slider-val" id="vsm-studio-sfx-vol-val">${this.prefs.sfxVolume}%</span>
        </div>
      </div>
      <div class="vsm-control-group"><label>AI Voice Speed</label>
        <select id="vsm-studio-voice-speed" class="vsm-select">
          ${[0.75,1.0,1.25,1.5].map(v => `<option value="${v}" ${this.prefs.voiceSpeed==v?'selected':''}>${v}x ${v===1?'(Normal)':v<1?'(Slow)':'(Fast)'}</option>`).join('')}
        </select>
      </div>
    </div>
    <h3 class="vsm-section-title">Sound Effects Library</h3>
    <div class="vsm-grid vsm-grid-small" id="vsm-grid-sfx">`;
    const sfx = window.EditingAssets?.SOUND_EFFECTS || [];
    sfx.forEach(t => {
      const sel = this.prefs.soundEffect === t.id ? 'vsm-selected' : '';
      html += `<div class="vsm-card vsm-card-sfx ${sel}" data-id="${t.id}">
        <div class="vsm-card-body">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <h4 class="vsm-card-title" style="margin:0;">${t.label}</h4>
            <button class="vsm-btn-icon vsm-play-sfx" data-sfx-id="${t.id}" title="Preview">▶</button>
          </div>
          <p class="vsm-card-desc" style="font-size:11px;margin-top:6px;">${t.description}</p>
          <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap;">
            ${(t.tags||[]).slice(0,3).map(tag => `<span class="vsm-badge">${tag}</span>`).join('')}
            <span class="vsm-badge" style="background:rgba(255,255,255,0.05);">${t.category||'SFX'}</span>
          </div>
        </div>
        <div class="vsm-check-icon">✓</div>
      </div>`;
    });
    html += `</div>`;
    c.innerHTML = html;
    this.bindGridSelection('vsm-grid-sfx', id => this.selectSfx(id));
    document.querySelectorAll('.vsm-play-sfx').forEach(b => {
      b.addEventListener('click', e => { e.stopPropagation(); this.playSfxPreview(b.dataset.sfxId); });
    });
    const mVol = document.getElementById('vsm-studio-music-vol');
    const mVal = document.getElementById('vsm-studio-music-vol-val');
    if (mVol) mVol.addEventListener('input', e => { this.prefs.musicVolume = parseInt(e.target.value); if(mVal)mVal.innerText=this.prefs.musicVolume+'%'; this.savePrefs(); });
    const sVol = document.getElementById('vsm-studio-sfx-vol');
    const sVal = document.getElementById('vsm-studio-sfx-vol-val');
    if (sVol) sVol.addEventListener('input', e => { this.prefs.sfxVolume = parseInt(e.target.value); if(sVal)sVal.innerText=this.prefs.sfxVolume+'%'; this.savePrefs(); });
    const spd = document.getElementById('vsm-studio-voice-speed');
    if (spd) spd.addEventListener('change', e => { this.prefs.voiceSpeed = parseFloat(e.target.value); this.savePrefs(); });
  },

  renderCanvasPage() {
    const c = document.getElementById('vsm-page-canvas');
    if (!c) return;
    const formats = window.EditingAssets?.VIDEO_FORMATS || [];
    let html = this.pageHeader('Canvas & Branding');
    html += `<h3 class="vsm-section-title">Video Format / Aspect Ratio</h3>
    <div class="vsm-grid vsm-grid-small" id="vsm-grid-formats">`;
    const ratioMap = { 'fmt-16x9':'w16\\:h9', 'fmt-9x16':'w9\\:h16', 'fmt-1x1':'w1\\:h1', 'fmt-4x5':'w4\\:h5', 'fmt-21x9':'w21\\:h9', 'fmt-2x35':'w2\\:h35' };
    formats.forEach(f => {
      const sel = this.prefs.videoFormat === f.id ? 'vsm-selected' : '';
      const r = ratioMap[f.id] || 'w16\\:h9';
      html += `<div class="vsm-card vsm-card-format ${sel}" data-id="${f.id}">
        <div class="vsm-card-preview vsm-format-icon"><div class="vsm-ratio-box ${r}"></div></div>
        <div class="vsm-card-body" style="text-align:center;">
          <h4 class="vsm-card-title">${f.label}</h4>
          <p class="vsm-card-desc">${f.ratio} • ${f.description||''}</p>
        </div>
        <div class="vsm-check-icon">✓</div>
      </div>`;
    });
    html += `</div><div class="vsm-divider"></div>
    <h3 class="vsm-section-title">Brand Colors</h3>
    <div class="vsm-color-controls">
      ${['primary','accent','bg'].map(k => {
        const labels = { primary:'Primary Brand', accent:'Accent Color', bg:'Canvas Background' };
        const vals = { primary:this.prefs.brandColorPrimary, accent:this.prefs.brandColorAccent, bg:this.prefs.backgroundColor };
        return `<div class="vsm-control-group"><label>${labels[k]}</label>
          <div class="vsm-color-picker-wrapper">
            <input type="color" id="vsm-color-${k}" value="${vals[k]}" class="vsm-color-input" />
            <input type="text" id="vsm-color-${k}-text" value="${vals[k]}" class="vsm-input vsm-input-small" />
          </div></div>`;
      }).join('')}
    </div>`;
    c.innerHTML = html;
    this.bindGridSelection('vsm-grid-formats', id => this.selectVideoFormat(id));
    ['primary','accent','bg'].forEach(k => {
      const pkr = document.getElementById(`vsm-color-${k}`);
      const txt = document.getElementById(`vsm-color-${k}-text`);
      const key = { primary:'brandColorPrimary', accent:'brandColorAccent', bg:'backgroundColor' }[k];
      if (pkr && txt) {
        pkr.addEventListener('input', e => { txt.value = e.target.value.toUpperCase(); this.prefs[key] = e.target.value; this.savePrefs(); });
        txt.addEventListener('change', e => { let v = e.target.value; if (!v.startsWith('#')) v = '#' + v; pkr.value = v; this.prefs[key] = v; this.savePrefs(); });
      }
    });
  },

  renderAssetsPage() {
    const c = document.getElementById('vsm-page-assets');
    if (!c) return;
    const pals = window.EditingAssets?.COLOR_PALETTES || [];
    c.innerHTML = `<div class="vsm-page-header"><h2>Media Assets & Color Palettes</h2></div>
    <h3 class="vsm-section-title">Color Palettes</h3>
    <div class="vsm-grid vsm-grid-small" id="vsm-grid-palettes">
      ${pals.map(p => {
        const sel = this.prefs.colorPalette === p.id ? 'vsm-selected' : '';
        return `<div class="vsm-card ${sel}" data-id="${p.id}">
          <div class="vsm-card-preview" style="display:flex;padding:4px;gap:3px;">
            ${p.colors.map(c => `<div style="flex:1;background:${c};border-radius:3px;"></div>`).join('')}
          </div>
          <div class="vsm-card-body">
            <h4 class="vsm-card-title">${p.label}</h4>
            <span class="vsm-badge">${p.mood}</span>
          </div>
          <div class="vsm-check-icon">✓</div>
        </div>`;
      }).join('')}
    </div>
    <div class="vsm-divider"></div>
    <h3 class="vsm-section-title">Extra Assets</h3>
    <div class="vsm-asset-toggles">
      <div class="vsm-toggle-row">
        <div class="vsm-toggle-info"><span class="vsm-toggle-icon">🎥</span><div><strong>Documentary B-Roll</strong><p class="vsm-text-muted vsm-small">Auto-interleave stock footage</p></div></div>
        <label class="vsm-switch"><input type="checkbox" id="vsm-toggle-doc-mode" /><span class="vsm-slider-switch round"></span></label>
      </div>
    </div>
    <div class="vsm-divider"></div>
    <div class="vsm-upload-area" id="vsm-upload-dropzone">
      <div class="vsm-upload-icon">📁</div>
      <h3>Upload Custom Asset</h3>
      <p class="vsm-text-muted">Drop video/image files here</p>
      <button class="vsm-btn vsm-btn-primary vsm-mt" style="background:var(--primary);color:white;border:none;padding:8px 20px;border-radius:8px;cursor:pointer;font-family:inherit;">Browse</button>
      <input type="file" id="vsm-upload-input" style="display:none;" multiple accept="video/*,image/*" />
    </div>`;
    this.bindGridSelection('vsm-grid-palettes', id => this.selectColorPalette(id));
    const dz = document.getElementById('vsm-upload-dropzone');
    const fi = document.getElementById('vsm-upload-input');
    if (dz && fi) {
      dz.addEventListener('click', () => fi.click());
      fi.addEventListener('change', e => { if (e.target.files.length) console.log('[EditingStudio] Assets:', e.target.files.length, 'file(s)'); });
      dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('vsm-dragover'); });
      dz.addEventListener('dragleave', () => dz.classList.remove('vsm-dragover'));
      dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('vsm-dragover'); });
    }
  },

  // ── HELPERS ─────────────────────────────────────────────────────────

  pageHeader(title, desc, searchable, searchId) {
    const s = searchable ? `<div class="vsm-search-bar"><input type="text" id="vsm-search-${searchId}" placeholder="Search..." class="vsm-input" /></div>` : '';
    return `<div class="vsm-page-header"><h2>${title}</h2>${s}</div>${desc ? `<p class="vsm-text-muted">${desc}</p>` : ''}`;
  },

  bindGridSelection(gridId, cb) {
    const grid = document.getElementById(gridId);
    if (!grid) return;
    grid.querySelectorAll('.vsm-card').forEach(card => {
      card.addEventListener('click', () => cb(card.dataset.id));
    });
    const search = document.getElementById('vsm-search-' + gridId.replace('vsm-grid-', ''));
    if (search) {
      search.addEventListener('input', e => {
        const q = e.target.value.toLowerCase();
        grid.querySelectorAll('.vsm-card').forEach(card => {
          card.style.display = card.textContent.toLowerCase().includes(q) ? 'flex' : 'none';
        });
      });
    }
  },

  updateGridSelection(gridId, selectedId) {
    const grid = document.getElementById(gridId);
    if (!grid) return;
    grid.querySelectorAll('.vsm-card').forEach(card => {
      card.classList.toggle('vsm-selected', card.dataset.id === selectedId);
    });
  },

  // ── SELECTION HANDLERS ──────────────────────────────────────────────

  selectTemplate(id) { this.prefs.template = id; this.updateGridSelection('vsm-grid-templates', id); this.savePrefs(); },
  selectFont(id) {
    this.prefs.font = id; this.updateGridSelection('vsm-grid-fonts', id);
    const f = window.EditingAssets?.FONTS?.find(x => x.id === id);
    const ext = document.getElementById('settingFontSelect');
    if (ext && f) { ext.value = f.family; ext.dispatchEvent(new Event('change')); }
    this.savePrefs();
  },
  selectTransition(id) { this.prefs.transition = id; this.updateGridSelection('vsm-grid-transitions', id); this.savePrefs(); },
  selectEffect(id) {
    this.prefs.effect = id; this.updateGridSelection('vsm-grid-effects', id);
    const ext = document.getElementById('settingEffectSelect');
    if (ext) { ext.value = id; ext.dispatchEvent(new Event('change')); }
    this.savePrefs();
  },
  selectAnimation(id) {
    this.prefs.animation = id; this.updateGridSelection('vsm-grid-animations', id);
    const ext = document.getElementById('settingCaptionAnimation');
    if (ext) { ext.value = id; ext.dispatchEvent(new Event('change')); }
    this.savePrefs();
  },
  selectOverlay(id) { this.prefs.overlay = id; this.updateGridSelection('vsm-grid-overlays', id); this.savePrefs(); },
  selectSfx(id) { this.prefs.soundEffect = id; this.updateGridSelection('vsm-grid-sfx', id); this.savePrefs(); },
  selectVideoFormat(id) { this.prefs.videoFormat = id; this.updateGridSelection('vsm-grid-formats', id); this.savePrefs(); },
  selectColorPalette(id) { this.prefs.colorPalette = id; this.updateGridSelection('vsm-grid-palettes', id); this.savePrefs(); },
  playSfxPreview(id) { console.log('[EditingStudio] Play SFX:', id); },

  // ── PERSISTENCE ─────────────────────────────────────────────────────

  savePrefs() {
    try { localStorage.setItem(this.PREFS_KEY, JSON.stringify(this.prefs)); } catch(e) {}
    // Sync to modal hidden input so it propagates to the pipeline via syncToPipeline
    const hidden = document.getElementById('settingEditingStudioPrefs');
    if (hidden) hidden.value = JSON.stringify(this.prefs);
  },
  loadPrefs() {
    try { const d = JSON.parse(localStorage.getItem(this.PREFS_KEY)); if (d) this.prefs = {...this.prefs, ...d}; } catch(e) {}
    this.syncHiddenInput();
  },
  getSelectedForGeneration() { return {...this.prefs}; },
  syncHiddenInput() {
    const hidden = document.getElementById('settingEditingStudioPrefs');
    if (hidden) hidden.value = JSON.stringify(this.prefs);
  },

  // ── RESOURCE INJECTION ──────────────────────────────────────────────

  loadGoogleFonts() {
    if (document.getElementById('vsm-google-fonts') || !window.EditingAssets?.FONTS) return;
    const families = window.EditingAssets.FONTS.map(f => {
      const name = f.googleFont || f.family.replace(/["']/g, '').replace(/\s+/g, '+');
      return `family=${name}:wght@400;600;700;800;900`;
    }).join('&');
    const link = document.createElement('link');
    link.id = 'vsm-google-fonts'; link.rel = 'stylesheet';
    link.href = `https://fonts.googleapis.com/css2?${families}&display=swap`;
    document.head.appendChild(link);
  },

  injectKeyframes() {
    if (document.getElementById('vsm-anim-keyframes')) return;
    const style = document.createElement('style');
    style.id = 'vsm-anim-keyframes';
    style.textContent = `
      .vsm-preview-animation-container { display:flex; align-items:center; justify-content:center; background:#0d1020; height:100px; }
      .vsm-anim-text { font-size:20px; font-weight:700; color:white; display:inline-block; }
      .vsm-ratio-box { border:2px solid var(--text-muted); border-radius:3px; background:rgba(255,255,255,0.05); }
      .vsm-ratio-box.w16\\:h9 { width:56px; height:32px; }
      .vsm-ratio-box.w9\\:h16 { width:24px; height:44px; }
      .vsm-ratio-box.w1\\:h1 { width:38px; height:38px; }
      .vsm-ratio-box.w4\\:h5 { width:28px; height:36px; }
      .vsm-ratio-box.w21\\:h9 { width:56px; height:24px; }
      .vsm-ratio-box.w2\\:h35 { width:56px; height:24px; }
    `;
    document.head.appendChild(style);
  }
};

window.EditingStudio = EditingStudio;

window.initEditingStudio = function() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => EditingStudio.init());
  } else {
    EditingStudio.init();
  }
};
