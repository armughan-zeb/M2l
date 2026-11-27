# 2D Stickman Cartoon Style — Locked Visual Specification

This repository recreates the vibrant **2D Stickman Cartoon Shorts** aesthetic (inspired by *Henry Stickmin* & *Green Stick Stories*): expressive stick figures with bold silhouettes inside bright, full-bleed illustrated 2D worlds.

## 1. Canvas & Format (Full-Bleed Native)
- **Landscape (`wide`)**: `1920×1080` (16:9) — native full-bleed canvas, phrase captions at `y = 930`.
- **Vertical Shorts (`short` / `reel`)**: `1080×1920` (9:16) — native full-bleed canvas (no letterbox bars or blurry background strips).
- **Clean Framing**: Zero forced dark corner blobs or murky vignettes. Colors are clean, sunlit, and saturated.

## 2. Stickman Character Rig (`src/stickman.js`)
Every character uses `stickman({ ... })` for locked visual consistency across every shot:

| Variant | Head | Torso | Limbs & Hands | Feet |
|---|---|---|---|---|
| `'white'` | Smooth cel-shaded `#FFFFFF` round head (`64×68`), subtle cool-gray crescent shadow, `5.5px` `#121216` outline | Pure curved black stick spine (`11px` lineWidth) — **no boxy rectangle torsos** | Articulated 2-segment Bezier limbs (`9.5px`) + forearm-aligned **4-fingered white cartoon gloves** with mirrored inward thumbs | Classic flat-sole black **teardrop shoes** |
| `'black'` | Glossy `#0F0F13` head (`67×70`) with crisp white specular highlight arc along the top curve | Tapered `#D93228` red crew-neck t-shirt + `#2176CC` blue shorts with 2-tone cel shading | Articulated stick limbs + forearm-aligned **dark silhouette 4-fingered cartoon hands** | Teardrop shoes |
| `'casual'` | Same as `'white'` | Customizable `shirtCol` & `shortsCol` over stick limbs | White cartoon gloves | Teardrop shoes |

### Expressive Face Rules
- **Eyes**: Side-by-side tall vertical oval white sclera (`rx = 0.23R, ry = 0.29R`) with thin crisp border (`3px`) and tracking solid pupils (`lookX`, `lookY`).
- **Moods**: `'flat'`, `'happy'` (`^ ^` arcs), `'squint'` (`> <`), `'shocked'` (extra-tall sclera + dilated pupils), `'determined'` / `'angry'` (angled lids + slanted brows), `'smug'`, `'worried'`, `'blink'`.
- **Mouths**: `'smile'`, `'bigsmile'` (teeth + tongue), `'open'` (yelling/shocked with upper teeth & tongue), `'frown'`.
- **On `'black'` heads**: Eyebrows and neutral/frown mouth lines draw in crisp `#FFFFFF` so expressions always pop.

## 3. Environments & Props (`src/environments.js`)
- **Full-Bleed Illustrated 2D Worlds**:
  - `tropicalIslandBg(t)` + `drawPalmHut(cx, groundY, s, t)` (bright cyan sky, sunburst rays, seagulls, turquoise surf waves, golden sand, palm trees, lashed palm-frond A-frame shelter).
  - `cartoonJungleBg(t, 'sunny' | 'dusk')` (rounded sunlit emerald canopy puffs, golden light shafts, dirt footpath).
  - `cartoonBambooAmbushBg(t)` (segmented bamboo stalks, tall swaying grass).
  - `cartoonMangroveRiverBg(t)` (sparkling teal river, arching mangrove prop roots).
- **Cartoon Animals & Tactical Diagrams**:
  - `cartoonTiger`, `cartoonTigerLunge`, `cartoonTigerFace`, `cartoonTigerStrikeDiagram` (smooth cel-shaded feline curves + target reticles).
- **Props**: `safariHat`, `spreadCoat`, `woodStick`, `palmLeaf`, `clanger` (with `soundShockwaves`), and `backwardMask` (`drawBackwardHeadMask`).

## 4. Pacing, Captions & Sound
- **Zero-Silence Narration**: Edge TTS lines trimmed cleanly with `gap: 0.02s`.
- **Fast Cuts**: `1.5–4.5 s` per scene (`short`) or `2–5 s` (`wide`). Every shot declares a `motion` and uses continuous secondary movement (breathing bob, swaying leaves, ocean foam, tail sway).
- **Clean Typography**: Outlined cartoon badges (`cartoonBadge`) and optional bold outlined phrase captions with yellow `*keyword` highlights — never giant cluttered paper boxes.
