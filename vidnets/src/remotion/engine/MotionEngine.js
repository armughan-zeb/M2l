import { interpolate, spring } from 'remotion';

/**
 * Camera motion transform calculator.
 * Smoothly interpolates transform matrix over scene progress [0..1].
 */
export function calculateCameraTransform(camera = 'slow_push', progress = 0) {
  const p = Math.max(0, Math.min(1, progress));
  const mode = String(camera).toLowerCase();

  switch (mode) {
    case 'none':
    case 'cut':
    case 'hard_cut':
      return 'scale(1.0)';
    case 'slow_push': {
      const scale = 1 + 0.08 * p;
      return `scale(${scale.toFixed(4)})`;
    }
    case 'pan_left': {
      const tx = (-2.8 * p).toFixed(3);
      return `translate(${tx}%, 0) scale(1.05)`;
    }
    case 'pan_right': {
      const tx = (2.8 * p).toFixed(3);
      return `translate(${tx}%, 0) scale(1.05)`;
    }
    case 'pan_up': {
      const ty = (-2.5 * p).toFixed(3);
      return `translate(0, ${ty}%) scale(1.05)`;
    }
    case 'pan_down': {
      const ty = (2.5 * p).toFixed(3);
      return `translate(0, ${ty}%) scale(1.05)`;
    }
    case 'pull_out': {
      const scale = 1.08 - 0.08 * p;
      return `scale(${scale.toFixed(4)})`;
    }
    case 'focus_zoom': {
      const scale = 1 + 0.14 * p;
      return `scale(${scale.toFixed(4)})`;
    }
    case 'drift': {
      const tx = (1.8 * p).toFixed(3);
      const ty = (1.0 * p).toFixed(3);
      return `translate(${tx}%, ${ty}%) scale(1.04)`;
    }
    case 'cinematic_parallax': {
      const tx = (-2.2 * p).toFixed(3);
      const ty = (1.2 * p).toFixed(3);
      const scale = (1.02 + 0.06 * p).toFixed(4);
      return `translate(${tx}%, ${ty}%) scale(${scale})`;
    }
    case 'static':
    default:
      return 'scale(1.0)';
  }
}

export function getSceneDuration(scene = {}, fps = 30) {
  return Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
}

export function getSceneProgress(frame, scene = {}, fps = 30) {
  const duration = getSceneDuration(scene, fps);
  return Math.min(1, Math.max(0, frame / Math.max(1, duration - 1)));
}

/**
 * Directorial Spring physics calculator.
 * Tuned to { stiffness: 175, damping: 15, mass: 0.36 } for snappy, crisp entrance.
 */
export function computeSpringEnter(frame, fps = 30, delay = 0, stiffness = 175, damping = 15, mass = 0.36) {
  const localFrame = Math.max(0, frame - delay);
  return spring({
    frame: localFrame,
    fps,
    config: { damping, stiffness, mass },
  });
}

/**
/**
 * Standard visual entrance & exit style calculator.
 * 
 * @param {string} entrance - Entrance animation preset name
 * @param {number} enterVal - Spring progress value [0..1]
 * @param {number} _progress - Overall scene timeline progress [0..1] (retained for call compatibility)
 * @param {boolean} _isGraphic - Retained for call compatibility. The scene Sequence owns its boundary.
 */
export function computeEntranceStyle(entrance = 'fade', enterVal = 1, _progress = 0, _isGraphic = false) {
  const mode = String(entrance).toLowerCase();
  const v = Math.max(0, Math.min(1, enterVal));

  // Scene visuals must remain present through their final frame. Each component is
  // already contained by a Remotion Sequence, so this shared "graphic exit" fade
  // only created a blank tail inside the current scene. Elements that need an
  // intentional exit should animate their own opacity without hiding the scene.
  const netOpacity = v;

  switch (mode) {
    case 'none':
    case 'cut':
    case 'hard_cut':
      return { opacity: 1 };
    case 'mask_reveal': {
      const clipProgress = (v * 100).toFixed(1);
      return {
        clipPath: `polygon(0 0, ${clipProgress}% 0, ${clipProgress}% 100%, 0 100%)`,
        opacity: netOpacity,
      };
    }
    case 'slide_up': {
      const ty = ((1 - v) * 45).toFixed(1);
      return {
        transform: `translateY(${ty}px)`,
        opacity: netOpacity,
      };
    }
    case 'slide_left': {
      const tx = ((1 - v) * 50).toFixed(1);
      return {
        transform: `translateX(${tx}px)`,
        opacity: netOpacity,
      };
    }
    case 'slide_right': {
      const tx = ((1 - v) * -50).toFixed(1);
      return {
        transform: `translateX(${tx}px)`,
        opacity: netOpacity,
      };
    }
    case 'scale_pop': {
      const s = (0.75 + v * 0.25).toFixed(3);
      return {
        transform: `scale(${s})`,
        opacity: netOpacity,
      };
    }
    case 'split_wipe': {
      const inset = ((1 - v) * 50).toFixed(1);
      return {
        clipPath: `inset(0 ${inset}% 0 ${inset}%)`,
        opacity: netOpacity,
      };
    }
    case 'fade':
    default:
      return {
        opacity: netOpacity,
      };
  }
}

/** Keep a photographic source visible at a scene boundary. Scene transitions
 * own image cuts; apply only spatial media movement, never a full-frame fade or
 * zero-width wipe that can produce a black/low-information opening frame. */
export function computeMediaEntranceStyle(entrance = 'none', enterVal = 1) {
  const mode = String(entrance || 'none').toLowerCase();
  const v = Math.max(0, Math.min(1, enterVal));
  if (mode === 'slide_up') return { opacity: 1, transform: `translateY(${((1 - v) * 30).toFixed(1)}px)` };
  if (mode === 'slide_left') return { opacity: 1, transform: `translateX(${((1 - v) * 34).toFixed(1)}px)` };
  if (mode === 'slide_right') return { opacity: 1, transform: `translateX(${((1 - v) * -34).toFixed(1)}px)` };
  if (mode === 'scale_pop') return { opacity: 1, transform: `scale(${(0.96 + v * 0.04).toFixed(3)})` };
  return { opacity: 1 };
}
