// How to Survive a Tiger Attack — 11-scene 30-second 2D Stickman Cartoon Film (1920x1080 Landscape).
import { ctx, W, H, lerp, eo, eob, seg, rr, circle, line, bigText, C } from './core/lib.js';
import { L, B } from './core/timing.js';
import {
  stickman,
  drawBackwardHeadMask,
  soundShockwaves,
  cartoonTiger,
  cartoonTigerLunge,
  cartoonTigerFace,
  cartoonTigerStrikeDiagram
} from './stickman.js';
import {
  cartoonJungleBg,
  cartoonBambooAmbushBg,
  cartoonMangroveRiverBg
} from './environments.js';

const INK = C.ink;

function cartoonBadge(text, x, y, sc = 1, bg = '#FFFFFF', fg = INK, size = 34) {
  if (sc <= 0) return;
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(sc, sc);
  ctx.font = `900 ${size}px Inter`;
  const w = ctx.measureText(text).width + 54;
  const h = size * 1.65;
  rr(-w / 2, -h / 2, w, h, h / 2);
  ctx.fillStyle = bg; ctx.fill();
  ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.stroke();
  ctx.fillStyle = fg;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(text, 0, 2);
  ctx.restore();
}

export function buildShots() {
  const S = [], CUES = [], add = (o) => (S.push(o), o);
  const cut_ = (i) => (i === 0 ? 0 : L(i));

  // ---- S0: Tall Bamboo Ambush & Title ----
  add({
    id: 'S0', line: 0, motion: 'draw', at: 0, note: 'Bengal tiger stalking stickman from behind in sunlit bamboo',
    cam: [W * 0.5, H * 0.5], zoom: 0.03,
    draw(lt, d, t) {
      const { groundY } = cartoonBambooAmbushBg(t);

      // Striker tiger creeping low on left
      const stalkX = lerp(340, 490, eo(lt / d));
      cartoonTiger({ x: stalkX, y: groundY + 12, s: 1.05, dir: 1, pose: 'crouch', mouth: 'growl', t });

      // White stickman walking unaware on right
      stickman({
        x: 1520 + (lt / d) * 45, y: groundY + 14, s: 1.15, dir: 1, kind: 'white',
        safariHat: true, mood: 'flat', mouth: 'smile', walk: t * 5, lookX: 0.3, t
      });

      // Clean outlined cartoon title framed in open upper-left/center sky
      const a = eob(seg(lt, 0.05, 0.4));
      bigText('HOW TO SURVIVE', 840, 195, 84, '#FFFFFF', 0, a);
      bigText('A TIGER ATTACK', 840, 290, 96, '#FFEE58', 0, a);
      if (lt > 0.35) {
        cartoonBadge('40 MPH REAR AMBUSH', 840, 385, eob(seg(lt, 0.35, 0.7)), '#D32F2F', '#FFFFFF', 30);
      }
    }
  });

  // ---- S1a: Rule 1 — Never Run ----
  add({
    id: 'S1a', line: 1, motion: 'impact', hit: 0.25, at: cut_(1), note: 'Running triggers deadly chase instinct',
    cam: [W * 0.5, H * 0.5], zoom: 0.04,
    draw(lt, d, t) {
      const { groundY } = cartoonJungleBg(t, 'dusk');

      const chaseX = lerp(420, 680, eo(lt / d));
      cartoonTiger({ x: chaseX, y: groundY + 12, s: 1.12, dir: 1, pose: 'stalk', mouth: 'roar', t: t * 2.2 });

      const runX = lerp(1180, 1490, eo(lt / d));
      stickman({
        x: runX, y: groundY + 14, s: 1.12, dir: 1, kind: 'white',
        safariHat: true, mood: 'shocked', mouth: 'open', walk: t * 14,
        armF: 48, elbowF: 28, armB: -48, elbowB: -28, t
      });

      // Red X warning over fleeing action
      const xK = eob(seg(lt, 0.2, 0.55));
      ctx.save(); ctx.translate(runX, groundY - 210); ctx.scale(xK, xK);
      ctx.strokeStyle = '#FF1744'; ctx.lineWidth = 18; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(-70, -70); ctx.lineTo(70, 70); ctx.moveTo(70, -70); ctx.lineTo(-70, 70); ctx.stroke();
      ctx.restore();

      cartoonBadge('RULE 1: NEVER RUN!', 960, 115, eob(seg(lt, 0.08, 0.4)), '#D32F2F', '#FFFFFF', 36);
    }
  });

  // ---- S1b: Macro Predator Roaring Close-Up ----
  add({
    id: 'S1b', line: 1, motion: 'zoom', at: B(1, 0.50), note: 'Macro close-up of roaring tiger chase instinct',
    cam: [W * 0.5, H * 0.5], zoom: 0.06,
    draw(lt, d, t) {
      const bg = ctx.createRadialGradient(960, 540, 100, 960, 540, 1000);
      bg.addColorStop(0, '#7F1D1D'); bg.addColorStop(0.6, '#450A0A'); bg.addColorStop(1, '#1C0404');
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);

      cartoonTigerFace(960, 530, 0.90, t, 'roar');
      cartoonBadge('FLEEING TRIGGERS 40 MPH CHASE INSTINCT', 960, 115, eob(seg(lt, 0.08, 0.45)), '#FFEE58', INK, 34);
    }
  });

  // ---- S2a: Stand Tall & Spread Jacket Wide ----
  add({
    id: 'S2a', line: 2, motion: 'rise', at: cut_(2), note: 'Stickman spreads jacket wide overhead to look huge',
    cam: [W * 0.5, H * 0.5], zoom: 0.04,
    draw(lt, d, t) {
      const { groundY } = cartoonJungleBg(t, 'sunny');

      cartoonTiger({ x: 500, y: groundY + 12, s: 1.0, dir: 1, pose: 'crouch', mouth: 'growl', t });

      const growS = lerp(1.12, 1.28, eo(seg(lt, 0.05, 0.55)));
      stickman({
        x: 1320, y: groundY + 14, s: growS, dir: -1, kind: 'white',
        safariHat: true, mood: 'determined', mouth: 'smile',
        spreadCoat: true, armF: 122, elbowF: -12, armB: -122, elbowB: 12,
        lookX: 0.35, t
      });

      // Green upward scale arrow
      ctx.save(); ctx.globalAlpha = eo(seg(lt, 0.2, 0.6));
      line([[1560, groundY - 40], [1560, groundY - 430]], '#00E676', 8);
      line([[1560, groundY - 430], [1535, groundY - 392]], '#00E676', 8);
      line([[1560, groundY - 430], [1585, groundY - 392]], '#00E676', 8);
      ctx.restore();

      cartoonBadge('RULE 2: STAND TALL & SPREAD JACKET WIDE', 960, 115, eob(seg(lt, 0.08, 0.45)), '#FFFFFF', INK, 34);
    }
  });

  // ---- S2b: Lock Intense Eye Contact ----
  add({
    id: 'S2b', line: 2, motion: 'glow', at: B(2, 0.50), note: 'Direct eye contact standoff disrupts ambush',
    cam: [W * 0.5, H * 0.5], zoom: 0.04,
    draw(lt, d, t) {
      const { groundY } = cartoonJungleBg(t, 'dusk');

      // Golden eye-lock beam between tiger & stickman
      const pulse = 0.7 + 0.3 * Math.sin(t * 16);
      ctx.save();
      ctx.strokeStyle = `rgba(255, 238, 88, ${0.9 * pulse})`;
      ctx.lineWidth = 7; ctx.setLineDash([18, 12]);
      ctx.beginPath(); ctx.moveTo(635, groundY - 135); ctx.lineTo(1285, groundY - 445); ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();

      cartoonTiger({ x: 480, y: groundY + 12, s: 1.02, dir: 1, pose: 'crouch', mouth: 'line', t });
      stickman({
        x: 1340, y: groundY + 14, s: 1.24, dir: -1, kind: 'white',
        safariHat: true, mood: 'determined', mouth: 'smile',
        spreadCoat: true, armF: 118, elbowF: -12, armB: -118, elbowB: 12,
        lookX: 0.35, t
      });

      cartoonBadge('LOCK DIRECT EYE CONTACT', 960, 115, eob(seg(lt, 0.08, 0.4)), '#FFEE58', INK, 36);
    }
  });

  // ---- S3: Slow Retreat + Shouting & Clanging Metal ----
  add({
    id: 'S3', line: 3, motion: 'sweep', at: cut_(3), note: 'Back away slowly while clanging metal pot and shouting',
    cam: [W * 0.5, H * 0.5], zoom: 0.03,
    draw(lt, d, t) {
      const { groundY } = cartoonBambooAmbushBg(t);

      const tigX = lerp(490, 380, eo(lt / d));
      cartoonTiger({ x: tigX, y: groundY + 12, s: 0.98, dir: 1, pose: 'crouch', mouth: 'line', t });

      const retX = lerp(1150, 1380, eo(lt / d));
      stickman({
        x: retX, y: groundY + 14, s: 1.18, dir: -1, kind: 'white',
        safariHat: true, mood: 'angry', mouth: 'open', walk: -t * 4,
        clanger: true, armF: 68, elbowF: -24, armB: -22,
        lookX: 0.38, t
      });

      soundShockwaves(retX - 85, groundY - 350, t, '#00E5FF');
      soundShockwaves(retX - 110, groundY - 270, t + 0.3, '#FFEE58');

      cartoonBadge('RULE 3: BACK AWAY SLOWLY • SHOUT & CLANG METAL', 960, 115, eob(seg(lt, 0.08, 0.45)), '#FFFFFF', INK, 34);
    }
  });

  // ---- S4a: Sundarbans Delta — Backward Face Mask Trick ----
  add({
    id: 'S4a', line: 4, motion: 'draw', at: cut_(4), note: 'Sundarbans villager wearing backward face mask fools tiger',
    cam: [W * 0.5, H * 0.5], zoom: 0.03,
    draw(lt, d, t) {
      const { groundY } = cartoonMangroveRiverBg(t);

      cartoonTiger({ x: 540, y: groundY + 12, s: 1.0, dir: 1, pose: 'crouch', mouth: 'line', t });

      // "?" reaction bubble above confused tiger
      const qK = eob(seg(lt, 0.25, 0.65));
      ctx.save(); ctx.translate(675, groundY - 245); ctx.scale(qK, qK);
      circle(0, 0, 42, '#FFFFFF'); ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.stroke();
      ctx.font = '900 46px Inter'; ctx.fillStyle = '#E65100';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('?', 0, 2);
      ctx.restore();

      // Black stickman villager facing away wearing backward mask on back of head
      stickman({
        x: 1260, y: groundY + 14, s: 1.18, dir: -1, kind: 'black',
        backwardMask: true, armF: 16, armB: -16, t
      });

      cartoonBadge('SUNDARBANS HACK: BACKWARD FACE MASKS', 960, 115, eob(seg(lt, 0.08, 0.45)), '#FFEE58', INK, 36);
    }
  });

  // ---- S4b: Macro Close-Up of Backward Mask Trick ----
  add({
    id: 'S4b', line: 4, motion: 'zoom', at: B(4, 0.50), note: 'Close-up diagram of how backward mask prevents rear ambush',
    cam: [W * 0.5, H * 0.5], zoom: 0.04,
    draw(lt, d, t) {
      cartoonMangroveRiverBg(t);

      rr(220, 200, 1480, 560, 28);
      ctx.fillStyle = 'rgba(14, 30, 26, 0.92)'; ctx.fill();
      ctx.strokeStyle = '#FFEE58'; ctx.lineWidth = 6; ctx.stroke();

      drawBackwardHeadMask(1290, 465, 132, 138);
      ctx.font = '900 28px Inter'; ctx.fillStyle = '#FFF8E1'; ctx.textAlign = 'center';
      ctx.fillText('MASK ON BACK OF HEAD', 1290, 665);

      cartoonTigerFace(600, 460, 0.45, t, 'growl');
      ctx.font = '900 28px Inter'; ctx.fillStyle = '#FFB74D'; ctx.textAlign = 'center';
      ctx.fillText('THINKS PREY IS WATCHING', 600, 665);

      line([[790, 460], [1085, 460]], '#00E676', 8);
      cartoonBadge('DETERS AMBUSH', 940, 460, 1, '#1B5E20', '#FFFFFF', 22);
      cartoonBadge('FOOLS AMBUSH PREDATORS FROM BEHIND', 960, 115, eob(seg(lt, 0.08, 0.45)), '#FFFFFF', INK, 34);
    }
  });

  // ---- S5a: Mid-Air Tiger Lunge — Fight Back! ----
  add({
    id: 'S5a', line: 5, motion: 'impact', hit: 0.20, at: cut_(5), note: 'Tiger mid-air lunge countered by stickman defender',
    cam: [W * 0.5, H * 0.5], zoom: 0.05,
    draw(lt, d, t) {
      const { groundY } = cartoonJungleBg(t, 'dusk');

      const leapX = lerp(520, 770, eo(lt / d));
      const leapY = groundY - 220 - Math.sin((lt / d) * Math.PI) * 85;
      cartoonTigerLunge(leapX, leapY, 1.08, 1, t);

      stickman({
        x: 1380, y: groundY + 14, s: 1.20, dir: -1, kind: 'white',
        safariHat: true, mood: 'angry', mouth: 'open',
        woodStick: true, stickAngle: -40, armF: 78, elbowF: -22, armB: -35,
        lookX: 0.35, t
      });

      cartoonBadge('IF IT LUNGES: FIGHT BACK WITH FORCE!', 960, 115, eob(seg(lt, 0.08, 0.4)), '#D32F2F', '#FFFFFF', 36);
    }
  });

  // ---- S5b: Tactical Anatomical Strike Zones ----
  add({
    id: 'S5b', line: 5, motion: 'draw', at: B(5, 0.50), note: 'Target sensitive eyes, nose, and ears',
    cam: [W * 0.5, H * 0.5], zoom: 0.03,
    draw(lt, d, t) {
      const bg = ctx.createLinearGradient(0, 0, 0, H);
      bg.addColorStop(0, '#0D1B2A'); bg.addColorStop(1, '#070D14');
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);

      for (let gx = 120; gx < W; gx += 120) line([[gx, 0], [gx, H]], 'rgba(255, 255, 255, 0.06)', 2);
      for (let gy = 120; gy < H; gy += 120) line([[0, gy], [W, gy]], 'rgba(255, 255, 255, 0.06)', 2);

      cartoonTigerStrikeDiagram(960, 520, 0.86, t);
      cartoonBadge('STRIKE SENSITIVE ZONES: EYES • NOSE • EARS', 960, 115, eob(seg(lt, 0.08, 0.4)), '#FFEE58', INK, 34);
    }
  });

  // ---- S6: Triumphant Finale ----
  add({
    id: 'S6', line: 6, motion: 'stamp', hit: 0.35, at: cut_(6), note: 'Defeated tiger retreats while stickmen celebrate survival',
    cam: [W * 0.5, H * 0.5], zoom: 0.03,
    draw(lt, d, t) {
      const { groundY } = cartoonJungleBg(t, 'sunny');

      // Defeated tiger walking off left
      const retTigX = lerp(520, 310, eo(lt / d));
      cartoonTiger({ x: retTigX, y: groundY + 12, s: 0.88, dir: -1, pose: 'stalk', mouth: 'line', t });

      // Both White & Black stickmen standing victorious side by side!
      stickman({
        x: 1120, y: groundY + 14, s: 1.18, dir: -1, kind: 'white',
        safariHat: true, mood: 'happy', mouth: 'bigsmile',
        spreadCoat: true, armF: 115, elbowF: -14, armB: -115, elbowB: 14, t
      });
      stickman({
        x: 1460, y: groundY + 14, s: 1.15, dir: -1, kind: 'black',
        mood: 'happy', mouth: 'bigsmile',
        armF: 125, elbowF: -25, armB: -22, t
      });

      cartoonBadge('STANDING YOUR GROUND SAVES YOUR LIFE', 960, 125, eob(seg(lt, 0.15, 0.55)), '#FFEE58', INK, 36);
    }
  });

  CUES.push({ name: 'whoosh', at: 0.08 });
  CUES.push({ name: 'boom', at: cut_(1) });
  CUES.push({ name: 'rumble', at: B(1, 0.50) });
  CUES.push({ name: 'riser', at: cut_(2) });
  CUES.push({ name: 'ding', at: B(2, 0.50) });
  CUES.push({ name: 'swish', at: cut_(3) });
  CUES.push({ name: 'pop', at: cut_(4) });
  CUES.push({ name: 'click', at: B(4, 0.50) });
  CUES.push({ name: 'boom', at: cut_(5) });
  CUES.push({ name: 'tick', at: B(5, 0.50) });
  CUES.push({ name: 'stamp', at: cut_(6) });

  return { shots: S, cues: CUES };
}
