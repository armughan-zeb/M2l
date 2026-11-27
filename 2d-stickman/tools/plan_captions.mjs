// Plan reel-style captions for script.json: split each line into short phrases + pick one yellow keyword per phrase.
//   node tools/plan_captions.mjs                 fill `caps` on lines that don't have one (uses the profile's phrase length)
//   node tools/plan_captions.mjs --force         re-plan every line (overwrites hand-written `caps`)
//   node tools/plan_captions.mjs --max-words 3   override the phrase length
//   node tools/plan_captions.mjs --dry           only print the plan, don't write
// It is a rule-based heuristic (numbers, years, names, acronyms, power words) — read the printout and fix any phrase
// by hand in script.json (`caps`: "phrase one | phrase *two"; a leading * marks the yellow keyword).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { planCaps } from '../src/core/capplan.js';
import { resolveProfile } from '../src/core/profiles.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const a = process.argv.slice(2), force = a.includes('--force'), dry = a.includes('--dry');
const mw = a.indexOf('--max-words') >= 0 ? +a[a.indexOf('--max-words') + 1] : null;
const file = path.join(ROOT, 'script.json'), script = JSON.parse(fs.readFileSync(file, 'utf8'));
let dur = 0; try { dur = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/lines.json'), 'utf8')).duration; } catch { /* voice not generated yet */ }
const cfg = resolveProfile(script, dur || 60), max = mw || cfg.capWords;
let n = 0;
script.lines.forEach((ln, i) => {
  if (!ln.caps || force) { ln.caps = planCaps(ln.text, { maxWords: max }); n++; }
  console.log(`${String(i).padStart(2)}  ${ln.caps}`);
});
console.log(`\nprofile ${cfg.name}, ≤ ${max} words/phrase, ${n} line(s) planned${dry ? ' (dry run — nothing written)' : ''}`);
if (!dry && n) fs.writeFileSync(file, JSON.stringify(script, null, 1) + '\n');
