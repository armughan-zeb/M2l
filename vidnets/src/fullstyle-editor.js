/**
 * Full Style Video Editor — Template Browser
 * Renders the canonical documentary template catalog with live previews.
 * Wires selection to the backend rendering pipeline.
 */
const FullStyleEditor = {
  isInitialized: false,
  currentCategory: 'all',
  selectedTemplate: null,
  searchQuery: '',
  viewMode: 'grid', // 'grid' or 'list'

  STORAGE_KEY: 'vidzeo_fullstyle_selection',
  PREFS_KEY: 'vidzeo_editing_prefs',

  // ══════════════════════════════════════════════════════════════════
  // Canonical native catalog. Keep this list aligned with editing-assets.js.
  // ══════════════════════════════════════════════════════════════════

  TEMPLATES: [
    // ── Typography ──────────────────────────────────────
    { id: 'cold_open', name: 'Cold Open', category: 'Typography',
      desc: 'Centered headline fading in with accent subtitle. Classic documentary opener.', color: '#f2c14e' },
    { id: 'kinetic_statement', name: 'Kinetic Statement', category: 'Typography',
      desc: 'Left-aligned massive text with accent bar and sliding detail.', color: '#f2c14e' },
    { id: 'word_cascade', name: 'Word Cascade', category: 'Typography',
      desc: 'Words stack vertically, each fading up in sequence.', color: '#f2c14e' },
    { id: 'keyword_reveal', name: 'Keyword Reveal', category: 'Typography',
      desc: 'Single keyword pops in center-frame. Maximum impact.', color: '#f2c14e' },
    { id: 'typewriter_reveal', name: 'Typewriter Reveal', category: 'Typography',
      desc: 'Characters reveal left-to-right with blinking cursor.', color: '#49a6d8' },
    { id: 'split_statement', name: 'Split Statement', category: 'Typography',
      desc: 'Vertical divider separates two text blocks.', color: '#f2c14e' },

    // ── Data & Stats ────────────────────────────────────
    { id: 'stat_counter', name: 'Stat Counter', category: 'Data & Stats',
      desc: 'Large number with label. Data visualization at its simplest.', color: '#f2c14e' },
    { id: 'money_counter', name: 'Money Counter', category: 'Data & Stats',
      desc: 'Dollar amount with rolling animation and currency label.', color: '#4ade80' },
    { id: 'percentage_gauge', name: 'Percentage Gauge', category: 'Data & Stats',
      desc: 'Big percentage with descriptive label.', color: '#f2c14e' },
    { id: 'metric_grid', name: 'Metric Grid', category: 'Data & Stats',
      desc: 'Two side-by-side metrics for comparison.', color: '#49a6d8' },
    { id: 'bar_chart', name: 'Bar Chart', category: 'Data & Stats',
      desc: 'Horizontal bars building in with labels and values.', color: '#f2c14e' },
    { id: 'ranked_list', name: 'Ranked List', category: 'Data & Stats',
      desc: 'Numbered items with proportional bars.', color: '#49a6d8' },

    // ── Evidence & Investigation ────────────────────────
    { id: 'evidence_callout', name: 'Evidence Callout', category: 'Evidence',
      desc: 'Red accent bar with stamp label and boxed text.', color: '#ef6f6c' },
    { id: 'redaction_reveal', name: 'Redaction Reveal', category: 'Evidence',
      desc: 'Black bars that animate to reveal hidden text.', color: '#ef6f6c' },
    { id: 'case_file', name: 'Case File', category: 'Evidence',
      desc: 'Document-style layout with stamp and ruled lines.', color: '#ef6f6c' },
    { id: 'source_citation', name: 'Source Citation', category: 'Evidence',
      desc: 'Small attribution text sliding in from left.', color: '#f2c14e' },
    { id: 'stamp_mark', name: 'Stamp Mark', category: 'Evidence',
      desc: 'Rotated stamp-style text mark.', color: '#ef6f6c' },

    // ── Timeline & Dates ────────────────────────────────
    { id: 'date_reveal', name: 'Date Reveal', category: 'Timeline',
      desc: 'Large date with accent color. Temporal anchor.', color: '#49a6d8' },
    { id: 'calendar_rip', name: 'Calendar Rip', category: 'Timeline',
      desc: 'Calendar-style date with torn-edge effect.', color: '#49a6d8' },
    { id: 'timeline_trace', name: 'Timeline Trace', category: 'Timeline',
      desc: 'Connected dots along a horizontal timeline.', color: '#f2c14e' },

    // ── Quotes ──────────────────────────────────────────
    { id: 'quote_card', name: 'Quote Card', category: 'Quotes',
      desc: 'Large quotation mark with italic text.', color: '#f2c14e' },
    { id: 'witness_quote', name: 'Witness Quote', category: 'Quotes',
      desc: 'Quote with attribution line below.', color: '#f2c14e' },

    // ── Comparison ──────────────────────────────────────
    { id: 'cause_effect', name: 'Cause & Effect', category: 'Comparison',
      desc: 'Two sides with arrow divider. Cause → Effect.', color: '#ef6f6c' },
    { id: 'before_after', name: 'Before & After', category: 'Comparison',
      desc: 'Split screen with vertical divider line.', color: '#49a6d8' },
    { id: 'split_compare', name: 'Split Compare', category: 'Comparison',
      desc: 'Two values facing off. Side-by-side contrast.', color: '#f2c14e' },

    // ── Location ────────────────────────────────────────
    { id: 'location_label', name: 'Location Label', category: 'Location',
      desc: 'Bottom-third location with accent bar.', color: '#f2c14e' },
    { id: 'map_route', name: 'Map Route', category: 'Location',
      desc: 'Two dots connected by a route line.', color: '#49a6d8' },

    // ── Closing ─────────────────────────────────────────
    { id: 'end_card', name: 'End Card', category: 'Closing',
      desc: 'Centered text with top accent line. Video closer.', color: '#f2c14e' },
    { id: 'cta_card', name: 'CTA Card', category: 'Closing',
      desc: 'Call-to-action with button prompt.', color: '#6366f1' },

    // ── Document ────────────────────────────────────────
    { id: 'field_caption', name: 'Field Caption', category: 'Document',
      desc: 'Lower-third caption with accent bar background.', color: '#f2c14e' },
    { id: 'exhibit_label', name: 'Exhibit Label', category: 'Document',
      desc: 'Tag-style label with underline. Formal reference.', color: '#f2c14e' },
    { id: 'newsprint_reveal', name: 'Newsprint Reveal', category: 'Document',
      desc: 'Text blocks appearing like newspaper columns.', color: '#49a6d8' },
    { id: 'countdown_clock', name: 'Countdown Clock', category: 'Document',
      desc: 'Large number with urgency label.', color: '#ef6f6c' },
    { id: 'signal_scan', name: 'Signal Scan', category: 'Document',
      desc: 'Horizontal scan line sweeping the frame.', color: '#49a6d8' },
    { id: 'index_card', name: 'Index Card', category: 'Document',
      desc: 'Bordered card container for any content.', color: '#f2c14e' },
    { id: 'profile_dossier', name: 'Profile Dossier', category: 'Document',
      desc: 'Avatar circle with info lines. Personnel file style.', color: '#6366f1' },

    // ── Cinematic / Noah Morris Style ───────────────────
    { id: 'watercolor_reveal', name: 'Watercolor Reveal', category: 'Cinematic',
      desc: 'Painterly white borders framing the image with serif text. Scrapbook aesthetic.', color: '#c9a96e' },
    { id: 'torn_paper_reveal', name: 'Torn Paper Reveal', category: 'Cinematic',
      desc: 'Irregular grunge edges with documentary text. VidRush-style transition.', color: '#ef6f6c' },
    { id: 'bold_name_banner', name: 'Bold Name Banner', category: 'Cinematic',
      desc: 'Name on bold colored banner. Classic documentary name reveal.', color: '#ef6f6c' },
    { id: 'location_title_card', name: 'Location Title Card', category: 'Cinematic',
      desc: 'Address with decorative border frame. Noah Morris location style.', color: '#c9a96e' },
    { id: 'retro_tech_overlay', name: 'Retro Tech Overlay', category: 'Cinematic',
      desc: 'Grid overlay with date badge. 90s computer documentary aesthetic.', color: '#49a6d8' },
    { id: 'vintage_film_card', name: 'Vintage Film Card', category: 'Cinematic',
      desc: 'Ornamental corner accents with sepia tone. Classic film title card.', color: '#c9a96e' },
    { id: 'dramatic_portrait', name: 'Dramatic Portrait', category: 'Cinematic',
      desc: 'Heavy vignette with large name and subtitle. Person-introduction card.', color: '#f2c14e' },
    { id: 'chapter_title', name: 'Chapter Title', category: 'Cinematic',
      desc: 'Large chapter number with divider and title. Documentary chapter break.', color: '#f2c14e' },
    { id: 'diagonal_split', name: 'Diagonal Split', category: 'Cinematic',
      desc: 'Diagonal gradient overlay with text. CapCut-style transition.', color: '#a78bfa' },
    { id: 'paper_fold_reveal', name: 'Paper Fold Reveal', category: 'Cinematic',
      desc: 'Text unfolds from center fold line. Elegant reveal effect.', color: '#c9a96e' },
    { id: 'bold_red_text', name: 'Bold Red Text', category: 'Cinematic',
      desc: 'Massive red text on dark overlay. News-breaking urgency.', color: '#ef6f6c' },
    { id: 'cinematic_zoom_text', name: 'Cinematic Zoom Text', category: 'Cinematic',
      desc: 'Text scales up dramatically with accent bars. Maximum impact.', color: '#f2c14e' },
    { id: 'passport_stamp', name: 'Passport Stamp', category: 'Cinematic',
      desc: 'Stamp-like border with location and date. Travel documentary style.', color: '#c94c3c' },
    { id: 'split_screen_doc', name: 'Split Screen Doc', category: 'Cinematic',
      desc: 'Left image, right text. Classic documentary interview split.', color: '#f2c14e' },
    { id: 'vintage_headline', name: 'Vintage Headline', category: 'Cinematic',
      desc: 'Old newspaper masthead with serif headline. Press aesthetic.', color: '#c9a96e' },
    { id: 'archival_caption', name: 'Archival Caption', category: 'Editorial Studio',
      desc: 'Location and date caption for historical footage.', color: '#d8a84e' },
    { id: 'date_numeral', name: 'Date Numeral', category: 'Editorial Studio',
      desc: 'Centered date treatment for a major historical beat.', color: '#d8a84e' },
    { id: 'evidence_highlight', name: 'Evidence Highlight', category: 'Editorial Studio',
      desc: 'Source label and restrained investigative annotation.', color: '#d45850' },
    { id: 'profile_card', name: 'Profile Card', category: 'Editorial Studio',
      desc: 'Clean subject identification with role and date.', color: '#d8a84e' },
    { id: 'subject_grid', name: 'Subject Grid', category: 'Editorial Studio',
      desc: 'Sparse presenter grid with a right-side information block.', color: '#d8a84e' },
    { id: 'listicle_steps', name: 'Listicle Steps', category: 'Editorial Studio',
      desc: 'Three numbered points with clear editorial spacing.', color: '#d8a84e' },
    { id: 'timeline_strip', name: 'Timeline Strip', category: 'Editorial Studio',
      desc: 'Three-moment timeline with minimal markers.', color: '#d8a84e' },
    { id: 'headline_center', name: 'Centered Headline', category: 'Editorial Studio',
      desc: 'Rare, high-importance two-line headline composition.', color: '#d8a84e' },
    { id: 'polaroid_card', name: 'Polaroid Card', category: 'Editorial Studio',
      desc: 'White polaroid frame with caption band and tape corner.', color: '#d8a84e' },
    { id: 'photo_frame', name: 'Photo Frame', category: 'Editorial Studio',
      desc: 'Full-frame photo mat with accent lines and archive label.', color: '#d8a84e' },
    { id: 'presenter_card', name: 'Presenter Card', category: 'Editorial Studio',
      desc: 'Bottom-left presenter identification with slide-up entry.', color: '#d8a84e' },
    { id: 'card_overlay', name: 'Editorial Card', category: 'Editorial Studio',
      desc: 'Right-side information card with tag, title and detail.', color: '#d8a84e' },
    { id: 'archive_canvas', name: 'Archive Canvas', category: 'Editorial Studio',
      desc: 'Cream paper sheet with binding edge and ruled detail lines.', color: '#d8a84e' },
    { id: 'step_progress', name: 'Step Progress', category: 'Editorial Studio',
      desc: 'Numbered steps with an animated progress sweep bar.', color: '#d8a84e' },
    { id: 'map_card', name: 'Map Card', category: 'Editorial Studio',
      desc: 'Bottom-right location card with pin dot and place name.', color: '#d8a84e' },
  ],
  REJECTED_FOR_AUTO: new Set(['evidence_callout', 'evidence-callout', 'case_file', 'case-file', 'index_card', 'index-card', 'profile_dossier', 'profile-dossier', 'terminal_log', 'terminal-log']),

  CATEGORIES: [
    { id: 'all', label: 'All Templates', icon: '◈', color: '#818cf8' },
    { id: 'Typography', label: 'Typography', icon: 'T', color: '#f2c14e' },
    { id: 'Data & Stats', label: 'Data & Stats', icon: '#', color: '#4ade80' },
    { id: 'Evidence', label: 'Evidence', icon: '!', color: '#ef6f6c' },
    { id: 'Timeline', label: 'Timeline', icon: '—', color: '#49a6d8' },
    { id: 'Quotes', label: 'Quotes', icon: '"', color: '#f2c14e' },
    { id: 'Comparison', label: 'Comparison', icon: '⇄', color: '#a78bfa' },
    { id: 'Location', label: 'Location', icon: '◉', color: '#f472b6' },
    { id: 'Closing', label: 'Closing', icon: '■', color: '#6366f1' },
    { id: 'Document', label: 'Document', icon: '□', color: '#6b7a90' },
    { id: 'Cinematic', label: 'Cinematic', icon: '◎', color: '#c9a96e' },
    { id: 'Editorial Studio', label: 'Editorial Studio', icon: '▣', color: '#d8a84e' },
  ],

  // ══════════════════════════════════════════════════════════════════
  // INIT
  // ══════════════════════════════════════════════════════════════════

  init() {
    if (this.isInitialized) return;
    this.loadSelection();
    this.render();
    this.bindEvents();
    this.isInitialized = true;
    this.loadSharedCatalog();
    console.log('[FullStyleEditor] Initialized —', this.TEMPLATES.length, 'templates');
  },

  async loadSharedCatalog() {
    try {
      const response = await fetch('/api/editor/templates');
      if (!response.ok) return;
      const payload = await response.json();
      const templates = (payload.templates || []).filter((item) => item.status === 'approved');
      if (!templates.length) return;
      this.TEMPLATES = templates.map((item) => ({
        id: item.id,
        name: item.label || item.id,
        category: ({ typography: 'Typography', data: 'Data & Maps', map: 'Data & Maps', timeline: 'Timeline', evidence: 'Evidence', comparison: 'Comparison', diagram: 'Comparison', recipe: 'Custom' })[String(item.family || '').toLowerCase()] || 'Documentary',
        desc: item.purpose || (item.roles || []).join(' · '),
        color: item.family === 'map' ? '#58c4dc' : '#f2c14e',
        version: item.version,
        preview: item.preview || '',
        requiredAssets: item.required_assets || [],
        reviewStatus: item.review_status || item.status,
      }));
      if (!this.TEMPLATES.some((item) => item.id === this.selectedTemplate)) this.selectedTemplate = this.TEMPLATES[0]?.id || null;
      this.render();
    } catch (error) {
      // Keep the bundled browser available when Flask is still starting.
    }
  },

  // ══════════════════════════════════════════════════════════════════
  // RENDER
  // ══════════════════════════════════════════════════════════════════

  render() {
    const section = document.getElementById('section-fullstyle');
    if (!section) return;

    const filtered = this.getFilteredTemplates();
    const catCounts = {};
    this.TEMPLATES.filter((item) => !this.REJECTED_FOR_AUTO.has(String(item.id || '').toLowerCase())).forEach(t => {
      catCounts[t.category] = (catCounts[t.category] || 0) + 1;
    });

    section.innerHTML = `
      <div class="fs-editor">
        <!-- Sidebar -->
        <div class="fs-sidebar">
          <div class="fs-sidebar-header">
            <h2>Full Style Editor</h2>
            <p>${this.TEMPLATES.length} Vox documentary templates</p>
          </div>
          <div class="fs-search">
            <input type="text" id="fs-search-input" placeholder="Search templates..." value="${this.searchQuery}" />
          </div>
          <div class="fs-categories" id="fs-categories">
            ${this.CATEGORIES.map(c => `
              <button class="fs-cat-btn ${this.currentCategory === c.id ? 'active' : ''}" data-cat="${c.id}">
                <span class="fs-cat-icon" style="background:${c.color}22;color:${c.color}">${c.icon}</span>
                <span>${c.label}</span>
                <span class="fs-cat-count">${c.id === 'all' ? this.TEMPLATES.length : (catCounts[c.id] || 0)}</span>
              </button>
            `).join('')}
          </div>
        </div>

        <!-- Main Content -->
        <div class="fs-main">
          <div class="fs-toolbar">
            <span class="fs-toolbar-title">${this.getCategoryLabel()}</span>
            <span class="fs-toolbar-count">${filtered.length} template${filtered.length !== 1 ? 's' : ''}</span>
            <span class="fs-toolbar-spacer"></span>
            <div class="fs-view-toggle">
              <button class="fs-view-btn ${this.viewMode === 'grid' ? 'active' : ''}" data-view="grid">▦</button>
              <button class="fs-view-btn ${this.viewMode === 'list' ? 'active' : ''}" data-view="list">☰</button>
            </div>
            <button class="fs-apply-btn" id="fs-apply-btn" ${!this.selectedTemplate ? 'disabled' : ''}>
              ✓ Apply Style
            </button>
          </div>
          <div class="fs-grid-wrapper">
            <div class="fs-grid ${this.viewMode === 'list' ? 'list-view' : ''}" id="fs-grid">
              ${filtered.map(t => this.renderCard(t)).join('')}
            </div>
          </div>
        </div>
      </div>`;
  },

  renderCard(tmpl) {
    const sel = this.selectedTemplate === tmpl.id ? 'selected' : '';
    return `
      <div class="fs-card ${sel}" data-id="${tmpl.id}">
        <div class="fs-card-check">✓</div>
        <div class="fs-card-preview tpl-preview-${tmpl.id}">
          <div class="fs-preview-inner">
            ${this.getPreviewContent(tmpl)}
          </div>
        </div>
        <div class="fs-card-info">
          <h4 class="fs-card-name">${tmpl.name}</h4>
          <div class="fs-card-meta">
            <span class="fs-card-category">${tmpl.category}</span>
            <span class="fs-card-badge" style="background:${tmpl.color}22;color:${tmpl.color}">VOX</span>
          </div>
        </div>
      </div>`;
  },

  // ══════════════════════════════════════════════════════════════════
  // PREVIEW CONTENT — CSS-animated previews for each template
  // ══════════════════════════════════════════════════════════════════

  getPreviewContent(tmpl) {
    const previews = {
      cold_open: '<div class="fs-t1">HEADLINE</div><div class="fs-t2">Supporting detail</div>',
      kinetic_statement: '<div class="fs-bar"></div><div class="fs-t1">STATEMENT</div>',
      word_cascade: '<div class="fs-w1">WORD ONE</div><div class="fs-w2">WORD TWO</div>',
      keyword_reveal: '<div class="fs-kw">KEYWORD</div>',
      typewriter_reveal: '<div class="fs-text">TYPEWRITER TEXT</div>',
      split_statement: '<div class="fs-line"></div><div class="fs-t1">SPLIT</div>',
      stat_counter: '<div class="fs-big">87%</div><div class="fs-label">Approval Rating</div>',
      money_counter: '<div class="fs-dollar">U.S. DOLLARS</div><div class="fs-big">$1,234</div>',
      percentage_gauge: '<div class="fs-pct">73%</div><div class="fs-label">Completion</div>',
      metric_grid: '<div class="fs-m1"><div class="fs-num">72</div><div style="font-size:9px;color:#6b7a90">Metric A</div></div><div class="fs-m2"><div class="fs-num">31</div><div style="font-size:9px;color:#6b7a90">Metric B</div></div>',
      bar_chart: '<div class="fs-bar1"></div><div class="fs-bar2"></div><div class="fs-bar3"></div>',
      ranked_list: '<div class="fs-rank">01</div><div class="fs-bar"></div><div class="fs-rank2">02</div><div class="fs-bar2"></div>',
      evidence_callout: '<div class="fs-bar"></div><div class="fs-stamp">EVIDENCE</div><div class="fs-t1">The records did not match</div>',
      redaction_reveal: '<div class="fs-redact"></div><div class="fs-redact2"></div>',
      case_file: '<div class="fs-stamp">CLASSIFIED</div><div class="fs-line"></div><div class="fs-line2"></div>',
      source_citation: '<div class="fs-src">— Source Name, 2024</div>',
      stamp_mark: '<div class="fs-stamp">APPROVED</div>',
      date_reveal: '<div class="fs-date">2024</div><div class="fs-label">Key Date</div>',
      calendar_rip: '<div class="fs-date">JAN 15</div><div class="fs-label">Calendar Date</div>',
      timeline_trace: '<div class="fs-dot"></div><div class="fs-line"></div><div class="fs-dot2"></div>',
      quote_card: '<div class="fs-q">"</div><div class="fs-t1">Important quote goes here</div>',
      witness_quote: '<div class="fs-q">"</div><div class="fs-t1">Testimony text</div><div class="fs-attr">— Witness Name</div>',
      cause_effect: '<div class="fs-cause">CAUSE</div><div class="fs-arrow">→</div><div class="fs-effect">EFFECT</div>',
      before_after: '<div class="fs-before">BEFORE</div><div class="fs-after">AFTER</div><div class="fs-vline"></div>',
      split_compare: '<div class="fs-left">12</div><div class="fs-right">48</div>',
      location_label: '<div class="fs-bar"></div><div class="fs-loc">NORTH ATLANTIC</div>',
      map_route: '<div class="fs-dot1"></div><div class="fs-line"></div><div class="fs-dot2"></div>',
      end_card: '<div class="fs-line"></div><div class="fs-t1">THANK YOU</div>',
      cta_card: '<div class="fs-t1">SUBSCRIBE</div><div class="fs-btn">Click Here</div>',
      field_caption: '<div class="fs-bar"></div><div class="fs-t1">Location Text</div>',
      exhibit_label: '<div class="fs-tag">EXHIBIT A</div><div class="fs-line"></div>',
      newsprint_reveal: '<div class="fs-block"></div><div class="fs-block2"></div>',
      countdown_clock: '<div class="fs-num">3</div><div class="fs-label">Seconds Remaining</div>',
      signal_scan: '<div class="fs-scan"></div>',
      index_card: '<div class="fs-card-inner"></div>',
      profile_dossier: '<div class="fs-avatar"></div><div class="fs-line"></div><div class="fs-line2"></div>',
      // Cinematic / Noah Morris Style
      watercolor_reveal: '<div class="fs-border"></div><div class="fs-title-serif">Addison Avenue</div><div class="fs-sub">California</div>',
      torn_paper_reveal: '<div class="fs-torn-top"></div><div class="fs-torn-bot"></div><div class="fs-t1">Stanford University</div>',
      bold_name_banner: '<div class="fs-banner"><div class="fs-name">Frederick Terman</div></div>',
      location_title_card: '<div class="fs-frame"></div><div class="fs-title-serif">367 Addison Ave</div><div class="fs-sub">Palo Alto</div>',
       retro_tech_overlay: '<div class="fs-grid"></div><div class="fs-badge">1998</div><div class="fs-t1">MOST VISITED</div>',
      vintage_film_card: '<div class="fs-corners"></div><div class="fs-title-serif">GOLDEN AGE</div><div class="fs-diamond">◆</div>',
      dramatic_portrait: '<div class="fs-vignette"></div><div class="fs-name-lg">JAMES DONOVAN</div><div class="fs-role">Negotiator</div>',
      chapter_title: '<div class="fs-ch-num">01</div><div class="fs-ch-line"></div><div class="fs-ch-title">THE BEGINNING</div>',
      diagonal_split: '<div class="fs-diag"></div><div class="fs-t1">TURNING POINT</div>',
      paper_fold_reveal: '<div class="fs-fold"></div><div class="fs-t1">HIDDEN IN PLAIN SIGHT</div>',
      bold_red_text: '<div class="fs-red-big">BREAKING</div><div class="fs-sub">Detail text</div>',
      cinematic_zoom_text: '<div class="fs-zoom">EVERYTHING</div><div class="fs-zoom-line"></div>',
      passport_stamp: '<div class="fs-stamp-border"></div><div class="fs-stamp-text">SILICON VALLEY</div><div class="fs-stamp-date">1930</div>',
      split_screen_doc: '<div class="fs-split-left"></div><div class="fs-split-right"><div class="fs-t1">THE FINDING</div></div>',
       vintage_headline: '<div class="fs-masthead">CHRONICLE</div><div class="fs-hl">THE WARNING WAS THERE</div><div class="fs-mast-line"></div>',
       archival_caption: '<div class="fs-bar"></div><div class="fs-t1">NORTH ATLANTIC</div><div class="fs-label">APRIL 1912</div>',
       date_numeral: '<div class="fs-label">A KEY MOMENT</div><div class="fs-date">APRIL 15, 1912</div>',
       evidence_highlight: '<div class="fs-label">SOURCE</div><div class="fs-bar"></div><div class="fs-t1">KEY EVIDENCE</div>',
       profile_card: '<div class="fs-bar"></div><div class="fs-name">EDWARD SMITH</div><div class="fs-label">CAPTAIN</div>',
       subject_grid: '<div class="fs-grid"></div><div class="fs-name">THE SUBJECT</div><div class="fs-label">DOCUMENTARY PROFILE</div>',
       listicle_steps: '<div class="fs-t1">THREE REASONS</div><div class="fs-rank">01</div><div class="fs-t1">THE WARNING</div><div class="fs-rank2">02</div>',
       timeline_strip: '<div class="fs-t1">THE TIMELINE</div><div class="fs-line"></div><div class="fs-dot"></div><div class="fs-dot2"></div>',
       headline_center: '<div class="fs-label">KEY MOMENT</div><div class="fs-t1">THE TURNING POINT</div><div class="fs-line"></div>',
       polaroid_card: '<div class="fs-border"></div><div class="fs-title-serif">ARCHIVE PHOTO</div><div class="fs-sub">APRIL 1912</div>',
       photo_frame: '<div class="fs-frame"></div><div class="fs-label">ARCHIVE FRAME</div>',
       presenter_card: '<div class="fs-bar"></div><div class="fs-name">THE PRESENTER</div><div class="fs-label">FIELD REPORT</div>',
       card_overlay: '<div class="fs-card-inner"></div><div class="fs-label">EDITORIAL NOTE</div><div class="fs-t1">THE DETAIL</div>',
       archive_canvas: '<div class="fs-card-inner"></div><div class="fs-label">RECORD</div><div class="fs-t1">THE ARCHIVE</div>',
       step_progress: '<div class="fs-t1">THE PLAN</div><div class="fs-rank">01</div><div class="fs-line"></div><div class="fs-rank2">02</div>',
       map_card: '<div class="fs-card-inner"></div><div class="fs-dot"></div><div class="fs-t1">NORTH ATLANTIC</div>',
    };
    return previews[tmpl.id] || '<div class="tpl-preview-generic"><div class="fs-icon">◈</div><div class="fs-text">Preview</div></div>';
  },

  // ══════════════════════════════════════════════════════════════════
  // FILTERING
  // ══════════════════════════════════════════════════════════════════

  getFilteredTemplates() {
    let list = this.TEMPLATES.filter((item) => !this.REJECTED_FOR_AUTO.has(String(item.id || '').toLowerCase()));
    if (this.currentCategory !== 'all') {
      list = list.filter(t => t.category === this.currentCategory);
    }
    if (this.searchQuery) {
      const q = this.searchQuery.toLowerCase();
      list = list.filter(t =>
        t.name.toLowerCase().includes(q) ||
        t.desc.toLowerCase().includes(q) ||
        t.category.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q)
      );
    }
    return list;
  },

  getCategoryLabel() {
    if (this.currentCategory === 'all') return 'All Templates';
    const cat = this.CATEGORIES.find(c => c.id === this.currentCategory);
    return cat ? cat.label : 'Templates';
  },

  // ══════════════════════════════════════════════════════════════════
  // EVENTS
  // ══════════════════════════════════════════════════════════════════

  bindEvents() {
    // Category buttons
    document.querySelectorAll('.fs-cat-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        this.currentCategory = btn.dataset.cat;
        this.render();
        this.bindEvents();
      });
    });

    // Search
    const search = document.getElementById('fs-search-input');
    if (search) {
      search.addEventListener('input', e => {
        this.searchQuery = e.target.value;
        this.render();
        this.bindEvents();
        // Re-focus search
        const s2 = document.getElementById('fs-search-input');
        if (s2) { s2.focus(); s2.selectionStart = s2.selectionEnd = s2.value.length; }
      });
    }

    // View toggle
    document.querySelectorAll('.fs-view-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        this.viewMode = btn.dataset.view;
        this.render();
        this.bindEvents();
      });
    });

    // Template cards
    document.querySelectorAll('.fs-card').forEach(card => {
      card.addEventListener('click', () => {
        this.selectTemplate(card.dataset.id);
      });
    });

    // Apply button
    const applyBtn = document.getElementById('fs-apply-btn');
    if (applyBtn) {
      applyBtn.addEventListener('click', () => this.applySelection());
    }
  },

  // ══════════════════════════════════════════════════════════════════
  // SELECTION
  // ══════════════════════════════════════════════════════════════════

  selectTemplate(id) {
    this.selectedTemplate = id;
    this.saveSelection();

    // Update UI
    document.querySelectorAll('.fs-card').forEach(card => {
      card.classList.toggle('selected', card.dataset.id === id);
    });

    const applyBtn = document.getElementById('fs-apply-btn');
    if (applyBtn) applyBtn.disabled = false;

    console.log('[FullStyleEditor] Selected:', id);
  },

  applySelection() {
    if (!this.selectedTemplate) return;

    // Save to editing prefs (bridge to backend pipeline)
    try {
      const prefs = JSON.parse(localStorage.getItem(this.PREFS_KEY) || '{}');
      prefs.template = this.selectedTemplate;
      prefs.templateSource = 'vox';
      localStorage.setItem(this.PREFS_KEY, JSON.stringify(prefs));
    } catch(e) {}

    // Sync to hidden input for renderer.js
    const hidden = document.getElementById('settingEditingStudioPrefs');
    if (hidden) {
      try {
        const prefs = JSON.parse(hidden.value || '{}');
        prefs.template = this.selectedTemplate;
        prefs.templateSource = 'vox';
        hidden.value = JSON.stringify(prefs);
        hidden.dispatchEvent(new Event('change'));
      } catch(e) {
        hidden.value = JSON.stringify({ template: this.selectedTemplate, templateSource: 'vox' });
        hidden.dispatchEvent(new Event('change'));
      }
    }

    // Also sync to the old-style select if it exists
    const oldSelect = document.querySelector('[data-setting="template"]');
    if (oldSelect) {
      oldSelect.value = this.selectedTemplate;
      oldSelect.dispatchEvent(new Event('change'));
    }

    // Visual feedback
    const btn = document.getElementById('fs-apply-btn');
    if (btn) {
      const tmpl = this.TEMPLATES.find(t => t.id === this.selectedTemplate);
      btn.innerHTML = `✓ Applied: ${tmpl ? tmpl.name : this.selectedTemplate}`;
      btn.style.background = 'linear-gradient(135deg, #22c55e, #16a34a)';
      setTimeout(() => {
        btn.innerHTML = '✓ Apply Style';
        btn.style.background = '';
      }, 2000);
    }

    console.log('[FullStyleEditor] Applied:', this.selectedTemplate);
  },

  // ══════════════════════════════════════════════════════════════════
  // PERSISTENCE
  // ══════════════════════════════════════════════════════════════════

  saveSelection() {
    try {
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify({
        template: this.selectedTemplate,
        category: this.currentCategory,
        viewMode: this.viewMode,
      }));
    } catch(e) {}
  },

  loadSelection() {
    try {
      const data = JSON.parse(localStorage.getItem(this.STORAGE_KEY));
      if (data) {
        this.selectedTemplate = data.template || null;
        this.viewMode = data.viewMode || 'grid';
      }
    } catch(e) {}
  },

  // ══════════════════════════════════════════════════════════════════
  // PUBLIC API
  // ══════════════════════════════════════════════════════════════════

  getSelected() { return this.selectedTemplate; },
  getTemplate(id) { return this.TEMPLATES.find(t => t.id === id); },
  getAllTemplates() { return [...this.TEMPLATES]; },
  getTemplatesByCategory(cat) { return this.TEMPLATES.filter(t => t.category === cat); },
};

// ══════════════════════════════════════════════════════════════════
// Auto-init
// ══════════════════════════════════════════════════════════════════

window.FullStyleEditor = FullStyleEditor;

window.initFullStyleEditor = function() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => FullStyleEditor.init());
  } else {
    FullStyleEditor.init();
  }
};
