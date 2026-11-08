// Shared, bounded color treatment for the real media layers in preview/export.
// Documents and measured graphics retain their source colors.
export const COLOR_GRADES = Object.freeze({
  natural: { contrast: 1.1, saturation: 1.08, sepia: 0, hue: 0, brightness: 1.02 },
  archival: { contrast: 1.14, saturation: .76, sepia: .17, hue: -5, brightness: 1.04 },
  heritage: { contrast: 1.15, saturation: .78, sepia: .18, hue: -4, brightness: 1.05 },
  investigative: { contrast: 1.16, saturation: .68, sepia: .05, hue: -8, brightness: 1.04 },
  forensic: { contrast: 1.22, saturation: .6, sepia: .02, hue: -10, brightness: 1.02 },
  technical: { contrast: 1.13, saturation: .94, sepia: .03, hue: -8, brightness: 1.02 },
  blueprint: { contrast: 1.2, saturation: .65, sepia: 0, hue: 195, brightness: 1.02 },
  cosmic: { contrast: 1.2, saturation: 1.15, sepia: 0, hue: 215, brightness: 1.01 },
  royal: { contrast: 1.16, saturation: 1.1, sepia: .08, hue: -2, brightness: 1.03 },
  nature: { contrast: 1.12, saturation: 1.18, sepia: 0, hue: 4, brightness: 1.03 },
  action: { contrast: 1.15, saturation: 1.12, sepia: 0, hue: 0, brightness: 1.02 },
  warm: { contrast: 1.1, saturation: 1.09, sepia: .09, hue: -3, brightness: 1.03 },
  source: { contrast: 1, saturation: 1, sepia: 0, hue: 0, brightness: 1 },
});

export function resolveColorGrade(scene = {}, visualDNA = {}, options = {}) {
  const type = String(scene.primary_visual?.type || '');
  const requested = String(scene.design?.color_grade || scene.media?.color_grade || scene.primary_visual?.props?.color_grade || '').toLowerCase();
  const genre = String(visualDNA.genre || scene.category || '').toLowerCase();
  const treatment = String(visualDNA.image_treatment || '').toLowerCase();

  let name = 'natural';
  if (/DocumentHighlight|Headline/.test(type)) {
    name = 'source';
  } else if (COLOR_GRADES[requested]) {
    name = requested;
  } else if (scene.media?.preserve_source_colors === true) {
    name = 'source';
  } else if (options.archival || /archival-dossier|classic|vintage|motoring/.test(genre) || treatment === 'warm_archival') {
    name = 'archival';
  } else if (/broadsheet-heritage|newspaper|british|victorian/.test(genre)) {
    name = 'heritage';
  } else if (/royal-prestige|royal|monarch|dynasty/.test(genre)) {
    name = 'royal';
  } else if (/engineering-blueprint|railway|train|blueprint|cad/.test(genre)) {
    name = 'blueprint';
  } else if (/investigat|crime/.test(genre)) {
    name = 'investigative';
  } else if (/forensic-noir|after death|autopsy|horror/.test(genre) || treatment === 'desaturated_contrast') {
    name = 'forensic';
  } else if (/cosmic-science|space|astronomy|galaxy|astrophysics/.test(genre)) {
    name = 'cosmic';
  } else if (/nature-wildlife|nature|animal|ocean|jungle/.test(genre)) {
    name = 'nature';
  } else if (/cyber-technology|technology|finance|military/.test(genre) || treatment === 'cool_modern') {
    name = 'technical';
  } else if (/athletic-action|sports|gaming|racing|automotive/.test(genre)) {
    name = 'action';
  } else if (/travel|culture/.test(genre)) {
    name = 'warm';
  }

  const grade = COLOR_GRADES[name] || COLOR_GRADES.natural;
  const exposure = Number(options.brightness);
  const brightness = name === 'source' ? 1 : Number.isFinite(exposure) ? Math.min(3.2, Math.max(.7, exposure)) : grade.brightness;
  return {
    name,
    filter: `contrast(${grade.contrast}) saturate(${grade.saturation}) sepia(${grade.sepia}) hue-rotate(${grade.hue}deg) brightness(${brightness})`,
  };
}
