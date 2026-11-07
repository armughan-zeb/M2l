const ENGINES = {
  listicle: {
    name: 'Ranked List Engine',
    structure: `1. The Hook: Open with the #1 item — the most surprising or impressive. One bold sentence.
2. The Setup: Why this list matters now. Context in 2 sentences.
3. The Countdown: Each item gets its own section.
   - Number, name, and one-line headline.
   - 2-3 sentences explaining why it earned this spot — fact, stat, or story.
   - Blank line between items.
4. The Wrap: A closing thought tying the list together.
5. CTA: Subscribe for more rankings.`,
  },
  category: {
    name: 'Category Breakdown Engine',
    structure: `1. The Hook: The big picture — why understanding these categories matters.
2. Overview: What binds them together. A simple framework.
3. Category 1: Name + defining trait. 2-3 sentences with an example.
4. Category 2: Name + defining trait. 2-3 sentences with an example.
5. Category 3: Name + defining trait. 2-3 sentences with an example.
6. Category 4: Name + defining trait. 2-3 sentences with an example.
7. The Comparison: Which category wins and why.
8. Close: What this means for the viewer.`,
  },
  puzzle: {
    name: 'Question Investigation Engine',
    structure: `1. The Hook: The question itself. No answer yet.
2. The Common Belief: What most people think.
3. The Problem: Why the common belief doesn't add up.
4. Clue 1: First piece of evidence that changes the picture.
5. Clue 2: Second piece that deepens the mystery.
6. Clue 3: The reveal — what actually happened.
7. The Implication: What this means going forward.
8. Close: A question for the viewer to ponder.`,
  },
  system: {
    name: 'System Explanation Engine',
    structure: `1. The Hook: The system's output — what it does in one line.
2. Why It Matters: Why the viewer should care how it works.
3. The Input: What goes in. The raw materials or prerequisites.
4. Step 1: First stage — simplest layer.
5. Step 2: Second stage — the core mechanism.
6. Step 3: Third stage — the finishing touch.
7. The Output: What comes out and why it's powerful.
8. The Glitch: Where the system breaks (optional).
9. Close: An analogy that sums up the whole system.`,
  },
  timeline: {
    name: 'Timeline Engine',
    structure: `1. The Hook: The end of the story first. Where we are now.
2. The Origin: Where it all began. A single moment or person.
3. Era 1: The early years — foundation and pioneers.
4. Turning Point: The event that changed everything.
5. Era 2: The middle period — growth and transformation.
6. Turning Point: Another inflection.
7. Era 3: The modern era — where we are today.
8. The Future: What comes next.
9. Close: A reflection on the full arc.`,
  },
  biography: {
    name: 'Rise & Fall Engine',
    structure: `1. The Hook: The peak moment — most dramatic achievement.
2. The Early Years: Childhood, context, first sparks of talent.
3. The Ascent: First break, early success, building momentum.
4. The Peak: The apex — biggest win, fame, or discovery.
5. The Struggle: Setbacks, failures, or controversies.
6. The Pivot: How they adapted or where things went wrong.
7. The Legacy: Where they are now and what they left behind.`,
  },
  comparison: {
    name: 'Comparison Engine',
    structure: `1. The Hook: The question — which one is truly better?
2. The Stakes: Why this comparison matters.
3. Contender A: Strengths, track record, key stats.
4. Contender B: Strengths, track record, key stats.
5. Head to Head: Direct comparison on 3 criteria.
   Criterion 1: Who wins and why.
   Criterion 2: Who wins and why.
   Criterion 3: Who wins and why.
6. The Verdict: Which one comes out on top.
7. Close: What this means for the viewer's choice.`,
  },
  collision: {
    name: 'Collision Engine',
    structure: `1. The Hook: The explosive moment — their first confrontation.
2. The Origins: Where each side came from.
3. The Spark: What ignited the rivalry.
4. Escalation 1: First major clash.
5. Escalation 2: The retaliation.
6. The Peak: Their biggest battle.
7. The Aftermath: Where things stand now.
8. The Lesson: What this rivalry reveals about human nature or the sport/industry.`,
  },
  era: {
    name: 'Era Portrait Engine',
    structure: `1. The Hook: A single image or moment that defines the era.
2. The Context: What the world was like before.
3. The People: The key figures who shaped this time.
4. The Innovation: What changed — tech, culture, ideas.
5. The Defining Event: The moment that captured the era.
6. The Legacy: How this era changed everything after.
7. Close: What we lost and what we gained.`,
  },
  investigation: {
    name: 'Investigation Engine',
    structure: `1. The Hook: The crime, scandal, or secret in one sentence.
2. The Cover-Up: What people thought was happening.
3. The First Crack: How the truth started leaking.
4. The Evidence: Documents, testimonies, data.
5. The Players: Who did what and why.
6. The Fallout: Consequences and punishments.
7. The Untold: What still remains hidden.
8. Close: A warning or call to action.`,
  },
  general: {
    name: 'General Story Engine',
    structure: `1. The Hook: Start with the most interesting moment.
2. The Context: What the viewer needs to know.
3. The Body: 3-5 key points with facts and examples.
4. The Insight: What makes this worth watching.
5. Close: A memorable sendoff.`,
  },
};

function getEngine(type) {
  return ENGINES[type] || ENGINES.general;
}

function getEngineStructure(type) {
  const engine = ENGINES[type] || ENGINES.general;
  return engine.structure;
}

function getEngineName(type) {
  const engine = ENGINES[type] || ENGINES.general;
  return engine.name;
}

module.exports = { ENGINES, getEngine, getEngineStructure, getEngineName };
