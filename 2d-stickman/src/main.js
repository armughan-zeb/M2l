// Stickman Cartoon Studio — Main Renderer Loop & Frame-Exact Capture API
import { W, H, TAU, setCtx, setSize, ctx, clamp, eo, eio, seg } from './core/lib.js';
import { T, loadTiming, L } from './core/timing.js';
import { buildCaptions, drawCaption, captionList, captionInfo, setCaptionLayout } from './core/captions.js';
import { resolveProfile } from './core/profiles.js';
import { planSounds } from './core/sound.js';
import { buildShots } from './shots.js';

const cv = document.getElementById('cv');
const OP = (window.OP = { ready: false });
let mainCtx, VERT = false, OW = 1080, OH = 1920, CFG = null, SCRIPT = null, SHOTS = [];

function drawScene(t) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.letterSpacing = '0px';
  ctx.clearRect(0, 0, OW, OH);

  const S = SHOTS;
  let i = S.length - 1;
  while (i > 0 && S[i].at > t) i--;
  const sh = S[i];
  const end = i < S.length - 1 ? S[i + 1].at : T.dur;
  const d = Math.max(0.05, end - sh.at);
  const lt = t - sh.at;

  // Subtle cartoon camera zoom/breathing (clean full-bleed framing, zero dark borders)
  const punch = 1 + 0.025 * (1 - eo(clamp(lt / 0.22)));
  const drift = 1 + (sh.zoom ?? 0.02) * eio(clamp(lt / d));
  const [cx, cy] = sh.cam || [OW / 2, OH / 2];

  ctx.save();
  ctx.translate(cx, cy);
  ctx.scale(punch * drift, punch * drift);
  ctx.translate(-cx, -cy);
  sh.draw(lt, d, t);
  ctx.restore();

  ctx.globalAlpha = 1;
  ctx.letterSpacing = '0px';
}

function draw(t) {
  t = clamp(t, 0, T.dur - 1e-4);
  setCtx(mainCtx);
  drawScene(t);
  mainCtx.setTransform(1, 0, 0, 1, 0, 0);
  mainCtx.globalAlpha = 1;
  // Brief crisp fade on very start & end
  const fade = Math.min(clamp(t / 0.18), clamp((T.dur - t) / 0.25));
  if (fade < 1) {
    mainCtx.fillStyle = '#000';
    mainCtx.globalAlpha = 1 - fade;
    mainCtx.fillRect(0, 0, OW, OH);
    mainCtx.globalAlpha = 1;
  }
  drawCaption(t);
}

(async () => {
  try {
    const script = (SCRIPT = await (await fetch('script.json', { cache: 'no-store' })).json());
    await document.fonts.load('900 100px Inter');
    await document.fonts.load('700 100px Caladea');
    await loadTiming();
    CFG = resolveProfile(script, T.dur);

    VERT = CFG.format === 'vertical';
    OW = VERT ? 1080 : 1920;
    OH = VERT ? 1920 : 1080;
    setSize(OW, OH);
    cv.width = OW;
    cv.height = OH;
    mainCtx = cv.getContext('2d');
    setCtx(mainCtx);

    setCaptionLayout(
      VERT
        ? { W: OW, H: OH, vertical: true, capY: OH - 280, size: 78, maxw: 940 }
        : { W: OW, H: OH, vertical: false, capY: OH - 150, size: 72, maxw: 1680 }
    );
    buildCaptions(script, CFG);

    const built = buildShots();
    const cues = built.cues.slice();
    SHOTS = built.shots.sort((a, b) => a.at - b.at);
    if (!SHOTS.length) throw new Error('no shots built');

    const plan = planSounds(SHOTS, T.dur, CFG, script);
    cues.push(...plan.cues);

    Object.assign(OP, {
      ready: true,
      duration: () => T.dur,
      size: () => [OW, OH],
      draw,
      frame: (t, q = 0.92) => { draw(t); return cv.toDataURL('image/jpeg', q); },
      cues: () => cues.slice().sort((a, b) => (a.t ?? a.at ?? 0) - (b.t ?? b.at ?? 0)),
      bed: () => plan.bed,
      shots: () => SHOTS.map((s, i) => ({
        id: s.id, at: s.at, end: i < SHOTS.length - 1 ? SHOTS[i + 1].at : T.dur,
        line: s.line ?? null, motion: s.motion, note: s.note || ''
      })),
      captions: () => captionList().map((c) => ({
        ...c,
        phrases: c.phrases ? c.phrases.map((p) => p.runs.map((r) => (r.keyword ? '*' : '') + r.text).join('')) : []
      })),
      lintData: () => ({
        dur: T.dur,
        profile: CFG,
        script: SCRIPT,
        format: CFG.format,
        size: [OW, OH],
        shots: OP.shots(),
        cues: OP.cues(),
        bed: OP.bed(),
        captionInfo: captionInfo(),
        captions: captionInfo(),
        lines: T.lines,
        words: T.words
      })
    });

    const play = document.getElementById('play');
    if (!location.search.includes('capture')) {
      play.hidden = false;
      draw(0);
      play.onclick = async () => {
        play.hidden = true;
        let ac = null, src = null;
        try {
          const r = await fetch('data/mixed.wav').catch(() => fetch('data/mix.wav'));
          if (r && r.ok) {
            ac = new AudioContext();
            src = ac.createBufferSource();
            src.buffer = await ac.decodeAudioData(await r.arrayBuffer());
            src.connect(ac.destination);
            src.start();
          }
        } catch {}
        const t0 = performance.now();
        const tick = () => {
          const t = (performance.now() - t0) / 1000;
          if (t < T.dur) { draw(t); requestAnimationFrame(tick); }
          else { draw(T.dur - 1e-4); play.hidden = false; }
        };
        requestAnimationFrame(tick);
      };
    }
  } catch (err) {
    OP.bootError = String(err && err.stack || err);
  }
})();
