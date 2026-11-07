/*
 * Render a comprehensive visual showcase of ALL documentary templates and variants.
 * Generates an MP4 and extracts a high-res frame for each template into all_templates_gallery/.
 */
const path = require('path');
const fs = require('fs');
const http = require('http');
const { execSync } = require('child_process');
const { bundle } = require('@remotion/bundler');
const { renderMedia, selectComposition } = require('@remotion/renderer');

const root = path.resolve(__dirname, '..');
const galleryDir = 'C:\\Users\\user\\.gemini\\antigravity\\brain\\92f3db13-954a-4b44-a08e-9383e3c2c23b\\all_templates_gallery';
const showcaseMp4 = path.join(root, 'output', 'all_templates_showcase.mp4');

const SAMPLE_IMAGES = [
  path.join(root, 'temp/codex_validated_royal_20260916/temp/jobs/c3e128c5/c3e128c5-run-0001-0b49226283f5/render_media/web_v2_1789574320024_791c23/scene_0001.jpg'),
  path.join(root, 'temp/codex_validated_royal_20260916/temp/jobs/c3e128c5/c3e128c5-run-0001-0b49226283f5/render_media/web_v2_1789574320024_791c23/scene_0002.jpg'),
  path.join(root, 'temp/codex_validated_royal_20260916/temp/jobs/c3e128c5/c3e128c5-run-0001-0b49226283f5/render_media/web_v2_1789574320024_791c23/scene_0003.jpg'),
  path.join(root, 'temp/codex_validated_royal_20260916/temp/jobs/c3e128c5/c3e128c5-run-0001-0b49226283f5/render_media/web_v2_1789574320024_791c23/scene_0004.jpg'),
  path.join(root, 'temp/codex_validated_royal_20260916/temp/jobs/c3e128c5/c3e128c5-run-0001-0b49226283f5/render_media/web_v2_1789574320024_791c23/scene_0005.jpg'),
];

function getImage(idx) {
  return SAMPLE_IMAGES[idx % SAMPLE_IMAGES.length];
}

const TEMPLATE_SPECS = [
  {
    id: '01_chapter_royal_plaque',
    name: 'ChapterTitle: Heritage Framed Plaque (Royal History - Gold & Serif)',
    genre: 'royal-history',
    primary_visual: {
      type: 'ChapterTitle',
      variant: 'archival_slate_divider',
      props: { act: 'CHAPTER I', title: 'THE WINDSOR WEDDING', subtitle: 'St George Chapel // 2018', position: 'bottom_left' }
    },
    imageIdx: 0,
  },
  {
    id: '02_chapter_vox_pill',
    name: 'ChapterTitle: Vox Yellow Highlighter Pill (Vox Explainer - Canary Yellow)',
    genre: 'vox-explainer',
    primary_visual: {
      type: 'ChapterTitle',
      variant: 'bold_editorial_kicker',
      props: { act: 'ACT II', title: 'SHADOWS OF THE CROWN', subtitle: 'A New Era Begins', position: 'top_right' }
    },
    imageIdx: 1,
  },
  {
    id: '03_chapter_minimal_floating',
    name: 'ChapterTitle: Floating Minimal Underline (Nordic Editorial - Cobalt Blue)',
    genre: 'minimal-editorial',
    primary_visual: {
      type: 'ChapterTitle',
      variant: 'cinematic_minimal_act',
      props: { act: 'EPISODE III', title: 'CONSTITUTIONAL BALANCE', subtitle: 'Democracy in Transition', position: 'bottom_right' }
    },
    imageIdx: 2,
  },
  {
    id: '04_chapter_tech_hud',
    name: 'ChapterTitle: Cyber Timecode HUD (Technology - Electric Cyan & Monospace)',
    genre: 'technology',
    primary_visual: {
      type: 'ChapterTitle',
      variant: 'technical_timecode_chapter',
      props: { act: 'PHASE 04', title: 'SURVEILLANCE ARCHITECTURE', subtitle: 'Digital Intelligence Matrix', position: 'top_left' }
    },
    imageIdx: 3,
  },
  {
    id: '05_stat_vox_solid_block',
    name: 'StatCard: Solid Canary Yellow Block (Vox Explainer - High Impact)',
    genre: 'vox-explainer',
    primary_visual: {
      type: 'StatCard',
      variant: 'vox_stat_block',
      props: { value: 970, label: 'MILLION SPECTATORS', kicker: 'GLOBAL REACH', position: 'top_right' }
    },
    imageIdx: 1,
  },
  {
    id: '06_stat_minimal_floating_hero',
    name: 'StatCard: Floating Hero Number No Box (Nordic Editorial - Organic Text Shadow)',
    genre: 'minimal-editorial',
    primary_visual: {
      type: 'StatCard',
      variant: 'floating_hero_counter',
      props: { value: 1234, label: 'SOVEREIGN HOLDINGS DEPOSITED', kicker: 'AUDITED RESERVES', position: 'top_left' }
    },
    imageIdx: 0,
  },
  {
    id: '07_stat_finance_kpi',
    name: 'StatCard: Emerald Delta Pill (Finance Explainer - Bloomberg Green)',
    genre: 'finance-explainer',
    primary_visual: {
      type: 'StatCard',
      variant: 'kpi_metric_card',
      props: { value: 64, label: 'APPROVAL RATING', delta: '+24%', kicker: 'POLLING CONSENSUS', position: 'bottom_left' }
    },
    imageIdx: 2,
  },
  {
    id: '08_stat_percentage_ring',
    name: 'StatCard: Cyan Circular Progress Ring (Technology - Circular HUD)',
    genre: 'technology',
    primary_visual: {
      type: 'StatCard',
      variant: 'percentage_ring',
      props: { value: 78, label: 'CYBER READINESS', position: 'center' }
    },
    imageIdx: 4,
  },
  {
    id: '09_chart_vox_bar',
    name: 'Chart: Vox Centered Bar Chart (Vox Explainer - Blur Backdrop)',
    genre: 'vox-explainer',
    primary_visual: {
      type: 'Chart',
      variant: 'editorial_bar',
      props: {
        title: 'WESTMINSTER ABBEY // MAY 2023',
        values: [
          { label: '2004', value: 8 },
          { label: '2010', value: 65 },
          { label: '2016', value: 215 },
          { label: '2022', value: 394 }
        ],
        background_blur: true,
        position: 'center'
      }
    },
    imageIdx: 3,
  },
  {
    id: '10_chart_tech_trend',
    name: 'Chart: Glowing Cyan Trend Curve (Technology - Dark Matrix Grid)',
    genre: 'technology',
    primary_visual: {
      type: 'Chart',
      variant: 'trend_line_with_markers',
      props: {
        title: 'NETWORK PROTOCOL ADOPTION',
        emphasis: 'EXPONENTIAL SURGE',
        values: [
          { label: '2019', value: 24 },
          { label: '2021', value: 58 },
          { label: '2023', value: 92 },
          { label: '2025', value: 140 }
        ],
        background_blur: true,
        position: 'center'
      }
    },
    imageIdx: 4,
  },
  {
    id: '11_chart_finance_donut',
    name: 'Chart: Emerald Proportional Donut (Finance Explainer - Portfolio Breakdown)',
    genre: 'finance-explainer',
    primary_visual: {
      type: 'Chart',
      variant: 'donut_distribution',
      props: {
        title: 'TREASURY ALLOCATION',
        emphasis: '$450M',
        values: [
          { label: 'EQUITY', value: 220 },
          { label: 'BONDS', value: 130 },
          { label: 'CASH', value: 100 }
        ],
        background_blur: true,
        position: 'center'
      }
    },
    imageIdx: 0,
  },
  {
    id: '12_quote_minimal_pullquote',
    name: 'QuoteCard: Floating Pull-Quote Watermark (Nordic Editorial - Cobalt Watermark)',
    genre: 'minimal-editorial',
    primary_visual: {
      type: 'QuoteCard',
      variant: 'editorial_large_quote',
      props: {
        quote: 'History is not merely recorded; it is contested in the public square.',
        author: 'PROF. ARTHUR SCHLESINGER',
        role: 'HISTORICAL ARCHIVIST'
      }
    },
    imageIdx: 1,
  },
  {
    id: '13_quote_witness_dossier',
    name: 'QuoteCard: Crimson Witness Deposition (True Crime - Blood Red Caution)',
    genre: 'true-crime',
    primary_visual: {
      type: 'QuoteCard',
      variant: 'witness_dossier_quote',
      props: {
        quote: 'The security orders arrived too late to avert the crowd breach.',
        author: 'CONFIDENTIAL OFFICER #42',
        role: 'METROPOLITAN POLICE',
        date: 'MAY 6, 2023'
      }
    },
    imageIdx: 3,
  },
  {
    id: '14_headline_broadsheet',
    name: 'Headline: Broadsheet Broadsheet Paper (Broadsheet Archive - Aged Newsprint)',
    genre: 'broadsheet-paper',
    primary_visual: {
      type: 'Headline',
      variant: 'newspaper_front_page',
      props: {
        publication: 'THE DAILY TELEGRAPH',
        headline: 'A NATION PAUSES AS NEW CHAPTER OPENS AT THE ABBEY',
        subdeck: 'Thousands gather along the Mall for the coronation procession.',
        date: 'MAY 7, 2023'
      }
    },
    imageIdx: 3,
  },
  {
    id: '15_headline_breaking_ticker',
    name: 'Headline: Cable News Breaking Ticker Ribbon (Vox Explainer - Full-Width Bar)',
    genre: 'vox-explainer',
    primary_visual: {
      type: 'Headline',
      variant: 'breaking_news_ticker',
      props: {
        headline: 'PARLIAMENTARY INQUIRY SUMMONS TREASURY OFFICIALS',
        subdeck: 'Emergency select committee hearing scheduled for Monday morning.'
      }
    },
    imageIdx: 2,
  },
  {
    id: '16_profile_manila_dossier',
    name: 'PersonProfile: Authentic Manila Folder with Red Stamp (True Crime - Kraft Paper)',
    genre: 'true-crime',
    primary_visual: {
      type: 'PersonProfile',
      variant: 'classified_manila_dossier',
      props: {
        name: 'PRINCE PHILIP, DUKE OF EDINBURGH',
        role: 'CONSORT OF THE MONARCH',
        status: 'OFFICIAL RECORD',
        alias: 'PHILIP MOUNTBATTEN',
        tenure: '1947 — 2021',
        photo: getImage(0)
      }
    },
    imageIdx: 0,
  },
  {
    id: '17_profile_broadcast_lower_third',
    name: 'PersonProfile: Cinematic Lower-Third Ribbon (Royal History - Clean Broadcast)',
    genre: 'royal-history',
    primary_visual: {
      type: 'PersonProfile',
      variant: 'cinematic_lower_third_card',
      props: {
        name: 'ARCHIE HARRISON',
        role: 'EARL OF DUMBARTON // BUCKINGHAM PALACE'
      }
    },
    imageIdx: 2,
  },
  {
    id: '18_map_tactical_flight_arc',
    name: 'AnimatedMap: Geospatial Flight Arc (Technology - Satellite Vector Arc)',
    genre: 'technology',
    primary_visual: {
      type: 'AnimatedMap',
      variant: 'tactical_coordinate_grid',
      props: {
        startCity: 'LONDON',
        endCity: 'EDINBURGH',
        distance: '650 KM'
      }
    },
    imageIdx: 4,
  },
  {
    id: '19_document_classified_redaction',
    name: 'DocumentHighlight: Declassified Memo Redaction (True Crime - Marker Highlighter)',
    genre: 'true-crime',
    primary_visual: {
      type: 'DocumentHighlight',
      variant: 'declassified_redaction',
      props: {
        docTitle: 'HOUSEHOLD FINANCIAL AUDIT // CLASSIFIED',
        date: 'JUNE 14, 2018',
        paragraph: 'The private purse allocations were audited under strict confidentiality protocols. Direct disbursements towards external residences were concealed under general sovereign maintenance.',
        highlight: 'Direct disbursements towards external residences were concealed under general sovereign maintenance.'
      }
    },
    imageIdx: 1,
  },
  {
    id: '20_collage_evidence_pinboard',
    name: 'Collage: Evidence Pinboard with Red Yarn (True Crime - Polaroid Pushpins)',
    genre: 'true-crime',
    primary_visual: {
      type: 'Collage',
      variant: 'evidence_pinboard',
      props: {
        title: 'HISTORICAL EVIDENCE PINBOARD',
        images: [
          { src: getImage(0), label: 'EXHIBIT A' },
          { src: getImage(1), label: 'EXHIBIT B' },
          { src: getImage(2), label: 'EXHIBIT C' }
        ]
      }
    },
    imageIdx: 0,
  },
  {
    id: '21_comparison_slider_dual_photos',
    name: 'Comparison: Archival Photo Split Slider (Royal History - Dual Image Reveal)',
    genre: 'royal-history',
    primary_visual: {
      type: 'Comparison',
      variant: 'split_slider_before_after',
      props: {
        title: 'INSTITUTIONAL METRICS COMPARISON',
        leftLabel: '1953 CORONATION',
        leftValue: 'WESTMINSTER TRADITION',
        leftImage: getImage(0),
        rightLabel: '2023 SLIMMED COURT',
        rightValue: 'MODERNIZED REIGN',
        rightImage: getImage(2)
      }
    },
    imageIdx: 4,
  },
  {
    id: '22_diagram_systems_flow',
    name: 'Diagram: Connected Milestone Process Chain (Finance Explainer - Flowchart)',
    genre: 'finance-explainer',
    primary_visual: {
      type: 'Diagram',
      variant: 'step_by_step_process',
      props: {
        title: 'CONSTITUTIONAL TRANSITION CHAIN',
        steps: [
          { title: 'ACCESSION', desc: 'Sovereign Proclamation' },
          { title: 'PRIVY COUNCIL', desc: 'Oath of Allegiance' },
          { title: 'CORONATION', desc: 'Sacred Anointment' }
        ]
      }
    },
    imageIdx: 3,
  },
  {
    id: '23_kinetic_vox_punch',
    name: 'KineticText: High-Contrast Vox Punch (Vox Explainer - Bold Kinetic Type)',
    genre: 'vox-explainer',
    primary_visual: {
      type: 'KineticText',
      variant: 'word_by_word_punch',
      props: {
        text: 'THE INSTITUTION FACED ITS GREATEST RECKONING',
        emphasis: 'RECKONING',
        kicker: 'CRITICAL TURNING POINT'
      }
    },
    imageIdx: 4,
  },
];

function startLocalAssetServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      try {
        const rawPath = decodeURIComponent(req.url.replace(/^\/+/, ''));
        const resolved = path.resolve(rawPath.startsWith('/') ? path.join(root, rawPath) : rawPath);
        if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
          const ext = path.extname(resolved).toLowerCase();
          const mimeTypes = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.webp': 'image/webp',
            '.ttf': 'font/ttf',
            '.mp3': 'audio/mpeg',
          };
          res.writeHead(200, {
            'Content-Type': mimeTypes[ext] || 'application/octet-stream',
            'Access-Control-Allow-Origin': '*',
          });
          fs.createReadStream(resolved).pipe(res);
        } else {
          res.writeHead(404);
          res.end('Not found');
        }
      } catch (err) {
        res.writeHead(500);
        res.end(err.message);
      }
    });

    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      const baseUrl = `http://127.0.0.1:${port}`;
      console.log(`[Showcase] Local asset server at ${baseUrl}`);
      resolve({ server, baseUrl });
    });
  });
}

function asLocalUrl(value, baseUrl) {
  if (typeof value !== 'string') return value;
  const raw = value.trim();
  if (!raw || /^https?:\/\//i.test(raw)) return raw;
  return `${baseUrl}/${encodeURIComponent(path.resolve(raw))}`;
}

async function main() {
  fs.mkdirSync(galleryDir, { recursive: true });
  const { server, baseUrl } = await startLocalAssetServer();

  const sceneDurationSec = 1.5;
  const fps = 30;
  const sceneFrames = Math.round(sceneDurationSec * fps);

  const scenes = TEMPLATE_SPECS.map((spec, index) => {
    const imgPath = getImage(spec.imageIdx);
    const pProps = { ...spec.primary_visual.props };

    if (pProps.images) {
      pProps.images = pProps.images.map(img => ({
        ...img,
        src: asLocalUrl(img.src, baseUrl)
      }));
    }
    if (pProps.leftImage) pProps.leftImage = asLocalUrl(pProps.leftImage, baseUrl);
    if (pProps.rightImage) pProps.rightImage = asLocalUrl(pProps.rightImage, baseUrl);
    const sound_cues = [];
    if (index > 0) {
      sound_cues.push({ sfx_id: 'whoosh.light', time_local: 0.0, volume: 0.24, duration: 0.35 });
    }
    if (spec.primary_visual.type === 'AnimatedMap') {
      sound_cues.push({ sfx_id: 'map.ping', time_local: 0.1, volume: 0.35, duration: 0.45 });
      sound_cues.push({ sfx_id: 'map.draw', time_local: 0.25, volume: 0.30, duration: 0.7 });
    } else if (spec.primary_visual.type === 'DocumentHighlight') {
      sound_cues.push({ sfx_id: 'paper.rustle', time_local: 0.05, volume: 0.34, duration: 0.5 });
      sound_cues.push({ sfx_id: 'stamp.hit', time_local: 0.5, volume: 0.38, duration: 0.4 });
    } else if (spec.primary_visual.type === 'Collage') {
      sound_cues.push({ sfx_id: 'photo.slide', time_local: 0.1, volume: 0.34, duration: 0.4 });
      sound_cues.push({ sfx_id: 'paper.rustle', time_local: 0.25, volume: 0.28, duration: 0.45 });
    } else if (spec.primary_visual.type === 'Headline') {
      sound_cues.push({ sfx_id: 'paper.flip', time_local: 0.1, volume: 0.34, duration: 0.5 });
    } else if (spec.primary_visual.type === 'StatCard' || spec.primary_visual.type === 'Chart') {
      sound_cues.push({ sfx_id: 'data.tick', time_local: 0.15, volume: 0.32, duration: 0.35 });
    } else if (spec.primary_visual.type === 'ChapterTitle') {
      sound_cues.push({ sfx_id: 'impact.soft', time_local: 0.15, volume: 0.38, duration: 0.6 });
    } else if (spec.primary_visual.type === 'PersonProfile') {
      sound_cues.push({ sfx_id: 'camera.shutter', time_local: 0.15, volume: 0.36, duration: 0.35 });
    }

    return {
      id: spec.id,
      scene_index: index,
      genre: spec.genre,
      start: index * sceneDurationSec,
      duration: sceneDurationSec,
      media: {
        src: asLocalUrl(imgPath, baseUrl),
        type: 'image',
        fit: 'cover',
        position: 'center'
      },
      camera: {
        motion: index % 2 === 0 ? 'slow_push' : 'pan_left'
      },
      primary_visual: {
        type: spec.primary_visual.type,
        variant: spec.primary_visual.variant,
        props: pProps
      },
      sound_cues
    };
  });

  const manifest = {
    fps,
    width: 1920,
    height: 1080,
    duration: scenes.length * sceneDurationSec,
    duration_in_frames: scenes.length * sceneFrames,
    visual_dna: {
      genre: 'royal-history',
    },
    scenes
  };

  const manifestPath = path.join(root, 'output', 'all_templates_showcase.manifest.json');
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

  console.log(`[Showcase] Generated manifest with ${scenes.length} templates (${manifest.duration}s, ${manifest.duration_in_frames} frames)`);

  const serveUrl = await bundle({
    entryPoint: path.join(root, 'remotion', 'entry.jsx'),
    rootDir: root,
    publicDir: path.join(root, 'public'),
    webpackOverride: (config) => config,
  });

  const inputProps = { manifest };
  const composition = await selectComposition({ serveUrl, id: 'VidzeoDocumentary', inputProps });

  console.log(`[Showcase] Rendering video to ${showcaseMp4}...`);
  await renderMedia({
    composition,
    serveUrl,
    codec: 'h264',
    outputLocation: showcaseMp4,
    inputProps,
    concurrency: 4,
    scale: 1,
    onProgress: ({ progress }) => {
      const pct = Math.round(progress * 100);
      if (pct % 10 === 0) process.stdout.write(` ${pct}%`);
    }
  });

  console.log('\n[Showcase] Render complete! Extracting frames for every template...');

  // Extract a representative frame for each template scene at frame 22 (approx 0.75s into each 1.5s scene)
  TEMPLATE_SPECS.forEach((spec, index) => {
    const timestampSec = (index * sceneDurationSec + 0.75).toFixed(3);
    const outImg = path.join(galleryDir, `${spec.id}.jpg`);
    console.log(`[Showcase] Extracting ${spec.id} at ${timestampSec}s -> ${outImg}`);
    try {
      execSync(`ffmpeg -y -ss ${timestampSec} -i "${showcaseMp4}" -vframes 1 "${outImg}"`, { stdio: 'pipe' });
    } catch (err) {
      console.error(`Failed to extract ${spec.id}:`, err.message);
    }
  });

  server.close();
  console.log('[Showcase] ALL TEMPLATE FRAMES GATHERED SUCCESSFULLY!');
}

main().catch(err => {
  console.error('[Showcase] Fatal:', err);
  process.exit(1);
});
