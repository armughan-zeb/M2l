'use strict';

/**
 * Universal Structure Schemas for YouTube Script Generation.
 * 
 * Implements canonical internal representations for 4 core video formats:
 * 1. Narrative / Cinematic / Story: Dan Harmon Story Circle (8 nodes) + Save the Cat (15 beats)
 * 2. Documentary / Informational: Single thesis statement, 3 acts by impact, sequence-level outline, explicit Point of Attack
 * 3. Top 10 / Ranked List: Escalating stakes towards #1, distinct visual/tonal beats, text-overlay labels, progress callbacks
 * 4. Commentary / Reaction: Upfront stance, running tension between claim and truth
 */

const FORMATS = {
  NARRATIVE: 'narrative',
  DOCUMENTARY: 'documentary',
  LISTICLE: 'listicle',
  COMMENTARY: 'commentary',
  EXPLAINER: 'explainer',
};

// 1. Dan Harmon Story Circle (8 nodes)
const STORY_CIRCLE_NODES = [
  { id: 'comfort_zone', name: 'You (Comfort Zone)', targetPercent: 0.10, description: 'Establish the character/protagonist in their familiar, ordinary world.' },
  { id: 'need_want', name: 'Need (Want Something)', targetPercent: 0.20, description: 'A sharp desire, lack, or inciting incident disrupts the status quo.' },
  { id: 'unfamiliar_situation', name: 'Go (Unfamiliar World)', targetPercent: 0.35, description: 'Crossing the threshold into an unexpected, challenging environment.' },
  { id: 'adapt_search', name: 'Search (Adaptation)', targetPercent: 0.50, description: 'Facing trials, learning new rules, making crucial mistakes and adapting.' },
  { id: 'find_payoff', name: 'Find (Meeting the Goal)', targetPercent: 0.65, description: 'Getting what they wanted, discovering the breakthrough or missing piece.' },
  { id: 'heavy_price', name: 'Take (Pay the Price)', targetPercent: 0.75, description: 'The crisis: a severe consequence, revelation, or cost for what was taken.' },
  { id: 'return_familiar', name: 'Return (Coming Back)', targetPercent: 0.88, description: 'Returning to where they started, but carrying the prize and scars.' },
  { id: 'changed', name: 'Change (Transformed)', targetPercent: 1.00, description: 'Demonstrating how the person, system, or world is permanently altered.' }
];

// Save the Cat (15 beats) for longer scripts
const SAVE_THE_CAT_BEATS = [
  { id: 'opening_image', name: 'Opening Image', targetPercent: 0.05 },
  { id: 'setup', name: 'Setup', targetPercent: 0.10 },
  { id: 'theme_stated', name: 'Theme Stated', targetPercent: 0.15 },
  { id: 'catalyst', name: 'Catalyst', targetPercent: 0.20 },
  { id: 'debate', name: 'Debate', targetPercent: 0.25 },
  { id: 'break_into_two', name: 'Break into Two', targetPercent: 0.30 },
  { id: 'b_story', name: 'B Story', targetPercent: 0.35 },
  { id: 'fun_and_games', name: 'Fun and Games', targetPercent: 0.50 },
  { id: 'midpoint', name: 'Midpoint', targetPercent: 0.55 },
  { id: 'bad_guys_close_in', name: 'Bad Guys Close In', targetPercent: 0.65 },
  { id: 'all_is_lost', name: 'All Is Lost', targetPercent: 0.75 },
  { id: 'dark_night', name: 'Dark Night of the Soul', targetPercent: 0.80 },
  { id: 'break_into_three', name: 'Break into Three', targetPercent: 0.85 },
  { id: 'finale', name: 'Finale', targetPercent: 0.95 },
  { id: 'final_image', name: 'Final Image', targetPercent: 1.00 }
];

// 2. Documentary / Informational Architecture
const DOCUMENTARY_SCHEMA = {
  format: FORMATS.DOCUMENTARY,
  requiredFields: ['thesisStatement', 'pointOfAttack', 'acts'],
  actStructure: [
    {
      act: 1,
      name: 'Act I: Point of Attack & The Illusion',
      purpose: 'Enters the story at the exact high-impact frame. Presents the initial event or accepted premise, then shatters it with the core thesis.',
      targetShare: 0.25,
      sequenceTypes: ['point_of_attack', 'accepted_paradigm', 'the_rupture']
    },
    {
      act: 2,
      name: 'Act II: The Hidden Mechanism & Pressure',
      purpose: 'Ordered by impact, not chronology. Unpacks the driving forces, competing incentives, and the pivotal turning point where stakes doubled.',
      targetShare: 0.50,
      sequenceTypes: ['competing_forces', 'decisive_pivot', 'unforeseen_escalation']
    },
    {
      act: 3,
      name: 'Act III: The Reckoning & True Meaning',
      purpose: 'Deliver the core payoff promised in the thesis. Show the permanent fallout, the lesson, and what this actually means right now.',
      targetShare: 0.25,
      sequenceTypes: ['the_consequence', 'thesis_resolution', 'enduring_impact']
    }
  ]
};

// 3. Top 10 / Ranked List Architecture
const LISTICLE_SCHEMA = {
  format: FORMATS.LISTICLE,
  rules: [
    'Escalate stakes towards #1 — never front-load the best item.',
    'Each item must have a distinct visual/tonal beat and text-overlay label.',
    'Include numbered progress callbacks every 2-3 items ("That was #6. #5 is where the rules broke...").',
    'Niche specificity beats broad generic claims.'
  ],
  itemSchema: {
    rank: 'number',
    label: 'string (short text-overlay)',
    tonalBeat: 'string (e.g., Shocking, Technical Mastery, Hilarious Failure, Defining Moment)',
    evidenceSnippet: 'string',
    whyItEarnedSpot: 'string',
    progressCallback: 'string | null'
  }
};

// 4. Commentary / Reaction Architecture
const COMMENTARY_SCHEMA = {
  format: FORMATS.COMMENTARY,
  rules: [
    'Stance must be stated early (within the first 20 seconds).',
    'Maintain continuous tension between "What they claim" vs "What is actually true".',
    'The gap between claim and reality drives viewer retention.',
    'End on a clear, decisive verdict, not an ambiguous fence-sit.'
  ],
  sections: [
    { id: 'stance_and_claim', name: 'The Stance & The Big Claim', targetShare: 0.20 },
    { id: 'dissection_layer_1', name: 'Deconstruction 1: The Superficial Story', targetShare: 0.25 },
    { id: 'evidence_gap', name: 'The Reality Gap: What Really Happened', targetShare: 0.30 },
    { id: 'larger_consequence', name: 'Why This Deception Matters', targetShare: 0.15 },
    { id: 'verdict', name: 'Final Verdict & Takeaway', targetShare: 0.10 }
  ]
};

// 5. System / Explainer Architecture
const EXPLAINER_SCHEMA = {
  format: FORMATS.EXPLAINER,
  rules: [
    'Start with the system output or consequence before explaining internal mechanics.',
    'One journey: trace a single real-world input through the entire machine.',
    'Explore the failure case / glitch where the system breaks.',
    'Anchor abstract logic with tangible concrete analogies.'
  ],
  sections: [
    { id: 'observable_magic', name: 'The Visible Output', targetShare: 0.15 },
    { id: 'raw_inputs', name: 'The Prerequisite / Input', targetShare: 0.20 },
    { id: 'engine_room', name: 'The Core Mechanism', targetShare: 0.35 },
    { id: 'failure_mode', name: 'The Failure Mode / Stress Point', targetShare: 0.15 },
    { id: 'mental_model', name: 'The Core Mental Model', targetShare: 0.15 }
  ]
};

/**
 * Returns the canonical structure template for a given format type.
 */
function getStructureSchema(formatType, options = {}) {
  const normalized = String(formatType || '').toLowerCase();
  
  if (['story', 'narrative', 'cinematic', 'biography', 'case-study'].includes(normalized)) {
    return {
      format: FORMATS.NARRATIVE,
      nodes: options.useSaveTheCat ? SAVE_THE_CAT_BEATS : STORY_CIRCLE_NODES,
      framework: options.useSaveTheCat ? 'Save the Cat (15 beats)' : 'Dan Harmon Story Circle (8 nodes)',
      rules: [
        'Focus on character desire and consequence.',
        'Keep the 8 nodes distinct — each sequence must trigger the next.',
        'Show, do not tell, the transformation in the final node.'
      ]
    };
  }

  if (['listicle', 'list', 'top10', 'top-10', 'category'].includes(normalized)) {
    return LISTICLE_SCHEMA;
  }

  if (['commentary', 'reaction', 'review', 'collision', 'comparison'].includes(normalized)) {
    return COMMENTARY_SCHEMA;
  }

  if (['explainer', 'system', 'tutorial'].includes(normalized)) {
    return EXPLAINER_SCHEMA;
  }

  // Default to documentary / informational
  return DOCUMENTARY_SCHEMA;
}

module.exports = {
  FORMATS,
  STORY_CIRCLE_NODES,
  SAVE_THE_CAT_BEATS,
  DOCUMENTARY_SCHEMA,
  LISTICLE_SCHEMA,
  COMMENTARY_SCHEMA,
  EXPLAINER_SCHEMA,
  getStructureSchema
};
