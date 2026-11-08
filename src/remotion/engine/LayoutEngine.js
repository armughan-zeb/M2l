/**
 * LayoutEngine.js — Universal Positioning & Variety Resolution for Documentary Scenes.
 * 
 * Supports:
 * - top_left, top_right, bottom_left, bottom_right, middle_left, middle_right, center
 * - Deterministic variety hashing across scenes so consecutive overlays rotate corners
 * - Direction-aware styling (left border vs right border, text alignment)
 */

const QUADRANTS = ['bottom_left', 'top_left', 'bottom_right', 'top_right'];

export function normalizePosition(raw) {
  if (!raw || typeof raw !== 'string') return null;
  const cleaned = raw.toLowerCase().replace(/[\s\-]+/g, '_');
  if (cleaned.includes('bottom') && cleaned.includes('left')) return 'bottom_left';
  if (cleaned.includes('bottom') && cleaned.includes('right')) return 'bottom_right';
  if (cleaned.includes('top') && cleaned.includes('left')) return 'top_left';
  if (cleaned.includes('top') && cleaned.includes('right')) return 'top_right';
  if (cleaned.includes('middle') && cleaned.includes('left')) return 'middle_left';
  if (cleaned.includes('middle') && cleaned.includes('right')) return 'middle_right';
  if (cleaned.includes('center') || cleaned.includes('middle')) return 'center';
  return null;
}

export function resolveCardPlacement(scene = {}, props = {}, defaultPos = 'bottom_left') {
  const hasMedia = Boolean(scene?.media?.src || scene?.media?.path);

  // If there's no media, default to center
  if (!hasMedia) {
    return {
      position: 'center',
      isCenter: true,
      isRight: false,
      isLeft: false,
      isTop: false,
      isBottom: false,
      containerStyle: {
        justifyContent: 'center',
        alignItems: 'center',
        padding: '0 8%',
      },
      cardStyle: {
        textAlign: 'center',
      },
    };
  }

  // 1. Check explicit position in props or scene
  const explicitRaw = props.position || props._position || scene.primary_visual?.props?.position || scene.design?.position;
  let pos = normalizePosition(explicitRaw);

  // 2. If not explicitly specified, calculate algorithmic variety
  if (!pos) {
    const sceneIndex = Number(scene.scene_index ?? 0);
    const seed = Number(scene.design?.seed ?? scene.graphic?.seed ?? 0);
    const quadIndex = (sceneIndex + seed) % QUADRANTS.length;
    pos = QUADRANTS[quadIndex];
  }

  const isRight = pos === 'top_right' || pos === 'bottom_right' || pos === 'middle_right';
  const isLeft = pos === 'top_left' || pos === 'bottom_left' || pos === 'middle_left';
  const isTop = pos === 'top_left' || pos === 'top_right';
  const isBottom = pos === 'bottom_left' || pos === 'bottom_right';
  const isCenter = pos === 'center';

  let containerStyle = {};
  let cardStyle = {
    textAlign: isRight ? 'right' : isCenter ? 'center' : 'left',
  };

  switch (pos) {
    case 'top_left':
      containerStyle = {
        justifyContent: 'flex-start',
        alignItems: 'flex-start',
        paddingTop: '6%',
        paddingLeft: '6%',
      };
      break;

    case 'top_right':
      containerStyle = {
        justifyContent: 'flex-start',
        alignItems: 'flex-end',
        paddingTop: '6%',
        paddingRight: '6%',
      };
      break;

    case 'bottom_right':
      containerStyle = {
        justifyContent: 'flex-end',
        alignItems: 'flex-end',
        paddingBottom: '8%',
        paddingRight: '6%',
      };
      break;

    case 'middle_left':
      containerStyle = {
        justifyContent: 'center',
        alignItems: 'flex-start',
        paddingLeft: '6%',
      };
      break;

    case 'middle_right':
      containerStyle = {
        justifyContent: 'center',
        alignItems: 'flex-end',
        paddingRight: '6%',
      };
      break;

    case 'center':
      containerStyle = {
        justifyContent: 'center',
        alignItems: 'center',
        padding: '0 8%',
      };
      break;

    case 'bottom_left':
    default:
      containerStyle = {
        justifyContent: 'flex-end',
        alignItems: 'flex-start',
        paddingBottom: '8%',
        paddingLeft: '6%',
      };
      break;
  }

  return {
    position: pos,
    isRight,
    isLeft,
    isTop,
    isBottom,
    isCenter,
    containerStyle,
    cardStyle,
  };
}
