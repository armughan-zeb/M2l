# Vidzeo Documentary Architecture

Vidzeo's video generation pipeline uses a dual-engine architecture: an AI-directed semantic editorial brain paired with a high-fidelity local rendering system.

---

## 1. High-Level System Architecture

```
[User Request / Script / Audio]
               │
               ▼
   [Script & Speech Analyzer]
   ├── Whisper Word-Level Timings
   ├── Semantic Beat Detection
   └── Fact / Metric Extraction
               │
               ▼
   [Editorial Director Engine]
   ├── Style & Genre Resolution (Vox, Magnates, Netflix, Rathee, MrBeast)
   ├── Visual DNA Synthesis (Typography, Palette, Motion Physics)
   ├── Windowed Cadence Planner (1 graphic per 2-3 scenes)
   └── Synchronized Sound Cue Planner (Pre-roll Whoosh + Impact)
               │
               ▼
     [SemanticEditManifest]
               │
       ┌───────┴───────────────────────────────┐
       ▼                                       ▼
[Remotion React Engine]              [FFmpeg Local Renderer]
(Primary High-End Pipeline)          (Emergency Fallback)
├── Headless Chrome / Node.js        ├── Hardware Accelerated QSV / NVENC
├── Spring Physics & Kinetic Type    ├── PIL Anti-Aliased Graphics
├── Real Inter-Scene Transitions     ├── Smooth Slide & Fade Overlays
└── Multi-Track Audio Mixing         └── Stream-Copy Concat Pipeline
```

---

## 2. Core Components & Directory Mapping

### A. Semantic Editorial Brain (`server/services/`)
- `semantic_edit_engine.py`: Main orchestrator. Transforms raw narration into a full `SemanticEditManifest` with scene boundaries, camera motions, graphic templates, and sound cues.
- `editorial_cue_planner.py`: Source-grounded cue planning. Maps exact word indices to on-screen kinetic graphics.
- `editorial_director/`: Modular direction grammar:
  - `visual_dna.py`: Color theory, typography pairing, and genre aesthetics.
  - `scene_grammar.py`: Structural scene composition.
  - `sound_director.py`: Spatial and temporal sound design.

### B. Remotion Motion Graphics Engine (`src/remotion/`)
- `DocumentaryComposition.jsx`: Master Remotion composition. Sequentially orchestrates scenes, media treatments, graphic overlays, camera shake, visual textures, and multi-track audio.
- `engine/`:
  - `StyleEngine.js`: Visual DNA resolver with 12 documentary genre presets (`vox-editorial`, `magnates-noir`, `dhruv-clarity`, `netflix-prestige`, `mrbeast-retention`, etc.).
  - `MotionEngine.js`: Spring dynamics, entrance styles, camera transforms, and Ken Burns motion calculations.
  - `SceneRegistry.js`: Component resolver and dispatcher.
- `components/`:
  - `TextCard.jsx`: 6 broadcast-grade card styles with exit animations and blur-to-sharp physics.
  - `WordHighlighter.jsx`: Real-time keyword sweeps and glow pulses.
  - `StatCard.jsx`: KPI metric cards with tabular exponential count-up.
  - `ChapterTitle.jsx`, `Timeline.jsx`, `Chart.jsx`, `AnimatedMap.jsx`.
- `motion-graphics/`:
  - `useMotion.jsx`: Core physics hook providing channel-specific spring damping/stiffness and exit transforms.
  - `NumberCounter.jsx`: Tabular digit counter with exponential slowdown.
  - `KineticType.jsx`: Word-by-word kinetic animation.
- `transitions.jsx` & `transitions/motion.jsx`:
  - `ink_bleed`, `push_blur`, `snap_zoom`, `cross_dissolve`, `whip_pan`, `slide`, `light_leak`.

### C. Local Assets & Typography (`public/` & `fonts/`)
- `public/font-library/`: Locally cached TrueType fonts (Inter, Oswald, Archivo Black, Barlow Condensed, DM Sans, Plus Jakarta Sans, Lora, JetBrains Mono, Bebas Neue, Montserrat, Cinzel).
- `public/sound-effects/`: 80+ high-quality .wav sound effects categorized into whooshes, impacts, paper foley, telemetry ticks, and ambient drones.

---

## 3. Render Execution & Routing Policy (`native_render_policy.py`)

1. **Default Backend**: `remotion` (configurable via `VIDZEO_RENDER_BACKEND` environment variable or API payload).
2. **Execution Flow**:
   - `render_semantic_documentary()` builds manifest and launches `scripts/render-remotion-manifest.js`.
   - Node spins up an internal asset server and renders frames concurrently via Remotion headless browser.
   - If Remotion encounters any missing dependencies, the engine **automatically falls back to FFmpeg** without crashing the job.
   - The FFmpeg fallback utilizes PIL anti-aliased text rendering with smooth entry slide-up and alpha fade keyframing.
