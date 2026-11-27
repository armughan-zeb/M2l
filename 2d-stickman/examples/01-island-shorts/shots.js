// 2 Stickmen Stranded on an Island — Exact 2D Stickman Cartoon Style Showcase (1080x1920 Shorts)
import { W, H, lerp, eo, seg } from '../../src/core/lib.js';
import { L, B } from '../../src/core/timing.js';
import { stickman } from '../../src/stickman.js';
import { tropicalIslandBg, drawPalmHut } from '../../src/environments.js';

export function buildShots() {
  const S = [], CUES = [], add = (o) => (S.push(o), o);
  const cut_ = (i) => (i === 0 ? 0 : L(i));

  add({
    id: 'S0', line: 0, motion: 'draw', at: 0, note: 'Two stickmen stranded on sunny island beach',
    cam: [W * 0.5, H * 0.5], zoom: 0.02,
    draw(lt, d, t) {
      const { groundY } = tropicalIslandBg(t);
      stickman({
        x: W * 0.32, y: groundY + 45, s: 1.08, dir: 1, kind: 'black',
        pose: 'stand', mood: 'worried', mouth: 'frown',
        palmLeaf: true, leafAngle: 15, armF: 148, elbowF: -22, armB: -18,
        lookX: 0.35, t
      });
      stickman({
        x: W * 0.72, y: groundY + 55, s: 1.06, dir: -1, kind: 'white',
        pose: 'stand', mood: 'flat', mouth: 'smile',
        armF: 122, elbowF: -34, armB: -18,
        lookX: 0.4, t
      });
    }
  });

  add({
    id: 'S1a', line: 1, motion: 'sweep', at: cut_(1), note: 'White stickman carries sticks while black stickman relaxes',
    cam: [W * 0.5, H * 0.5], zoom: 0.025,
    draw(lt, d, t) {
      const { groundY } = tropicalIslandBg(t);
      stickman({
        x: W * 0.44, y: groundY + 95, s: 1.02, dir: 1, kind: 'black',
        pose: 'recline', mood: 'squint', mouth: 'smile',
        armF: 145, armB: -25, t
      });
      const wx = lerp(W * 0.88, W * 0.62, eo(lt / d));
      stickman({
        x: wx, y: groundY + 55, s: 1.08, dir: -1, kind: 'white',
        pose: 'stand', walk: t * 9, mood: 'determined', mouth: 'smile',
        woodStick: true, stickAngle: -45, armF: 65, elbowF: -22, armB: -25,
        lookX: 0.35, t
      });
    }
  });

  add({
    id: 'S1b', line: 1, motion: 'rise', at: B(1, 0.52), note: 'Palm-leaf shelter hut stands ready on the beach',
    cam: [W * 0.5, H * 0.52], zoom: 0.03,
    draw(lt, d, t) {
      const { groundY } = tropicalIslandBg(t);
      const hs = lerp(0.85, 1.0, eo(seg(lt, 0, 0.35)));
      drawPalmHut(W * 0.44, groundY + 35, hs, t);
      stickman({
        x: W * 0.79, y: groundY + 60, s: 1.06, dir: -1, kind: 'white',
        pose: 'stand', mood: 'happy', mouth: 'bigsmile',
        armF: 105, elbowF: -20, armB: -22, lookX: 0.3, t
      });
    }
  });

  add({
    id: 'S2', line: 2, motion: 'stamp', hit: 0.25, at: cut_(2), note: 'Black stickman sits atop the hut while white stickman stands beside it',
    cam: [W * 0.5, H * 0.5], zoom: 0.02,
    draw(lt, d, t) {
      const { groundY } = tropicalIslandBg(t);
      const { roofY } = drawPalmHut(W * 0.43, groundY + 45, 1.02, t);
      stickman({
        x: W * 0.43, y: roofY + 172, s: 1.06, dir: 1, kind: 'black',
        pose: 'sit', mood: 'shocked', mouth: 'open',
        armF: 28, elbowF: 18, armB: -35, elbowB: -18,
        lookX: 0.35, lookY: 0.15, t
      });
      stickman({
        x: W * 0.77, y: groundY + 65, s: 1.06, dir: -1, kind: 'white',
        pose: 'stand', mood: 'flat', mouth: 'smile',
        armF: 14, elbowF: 16, armB: -16, elbowB: -16,
        lookX: 0.32, lookY: -0.1, t
      });
    }
  });

  CUES.push({ name: 'whoosh', at: 0.05 });
  CUES.push({ name: 'swish', at: cut_(1) });
  CUES.push({ name: 'pop', at: B(1, 0.52) });
  CUES.push({ name: 'stamp', at: cut_(2) });

  return { shots: S, cues: CUES };
}
