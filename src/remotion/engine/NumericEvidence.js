// Parse the whole numeric phrase; never accept one word out of a compound value.
const SMALL = {zero:0,one:1,two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,ten:10,
  eleven:11,twelve:12,thirteen:13,fourteen:14,fifteen:15,sixteen:16,seventeen:17,eighteen:18,nineteen:19};
const TENS = {twenty:20,thirty:30,forty:40,fifty:50,sixty:60,seventy:70,eighty:80,ninety:90};
const SCALES = {thousand:1e3,million:1e6,billion:1e9,trillion:1e12};
export function parseNumericSpan(span) {
  const text = String(span || '').toLowerCase().replace(/,/g,'');
  const groups = text.match(/-?\d+(?:\.\d+)?|[a-z]+/g) || [];
  let group = 0, total = 0, seen = false, active = false, previous = '', lastScale = Infinity;
  let completed = false, sign = 1;
  for (let i = 0; i < groups.length; i++) {
    const word = groups[i];
    if (!seen && (word === 'minus' || word === 'negative')) { sign = -1; continue; }
    const numeric = /^-?\d/.test(word);
    const small = Object.prototype.hasOwnProperty.call(SMALL, word);
    const tens = Object.prototype.hasOwnProperty.call(TENS, word);
    const scale = SCALES[word];
    if (word === 'and' && active && (group >= 100 || total > 0)) continue;
    if (numeric || small || tens) {
      if (completed || (active && ['small','digit'].includes(previous))
          || (active && previous === 'tens' && (!small || SMALL[word] >= 10))) return NaN;
      group += numeric ? Number(word) : small ? SMALL[word] : TENS[word];
      seen = true; active = true; previous = numeric ? 'digit' : small ? 'small' : 'tens';
    } else if (word === 'hundred' && active && group > 0 && group < 100) {
      group *= 100; previous = 'hundred';
    } else if (scale && active && scale < lastScale) {
      total += group * scale; group = 0; previous = 'scale'; lastScale = scale;
    } else {
      if (active) { completed = true; active = false; }
    }
  }
  return seen ? sign * (total + group) : NaN;
}

export function metricIsGrounded(item, narration) {
  if (!item || !item.source_span || !Number.isFinite(Number(item.value))) return false;
  const span = String(item.source_span).toLowerCase();
  const story = String(narration || '').toLowerCase();
  const position = story.indexOf(span);
  if (position < 0 || parseNumericSpan(span) !== Number(item.value)) return false;
  const before = story.slice(0, position), after = story.slice(position + span.length);
  if (/[a-z0-9]/.test(span[0] || '') && /[a-z0-9]$/.test(before)) return false;
  if (/[a-z0-9]$/.test(span) && /^[a-z0-9]/.test(after)) return false;
  const numericWord = 'zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|trillion|minus|negative';
  const numberToken = new RegExp(`^(?:${numericWord}|-?\\d+(?:\\.\\d+)?)$`);
  const words = span.match(/-?\d+(?:\.\d+)?|[a-z]+/g) || [];
  // The evidence must include the whole compound: "twenty" cannot ground
  // 20 when the narration actually says "twenty-six" or "twenty million".
  if (numberToken.test(words[words.length - 1] || '')
      && new RegExp(`^[\\s-]+(?:and\\s+)?(?:${numericWord}|\\d)\\b`).test(after)) return false;
  if (numberToken.test(words[0] || '')
      && new RegExp(`(?:\\b(?:${numericWord})|\\d)[\\s-]+$`).test(before)) return false;
  const prefix = String(item.prefix || '').trim();
  const currencies = {'$': /\$|\bdollars?\b|\busd\b/, '£': /£|\bpounds?\b|\bgbp\b/, '€': /€|\beuros?\b|\beur\b/};
  if (prefix && !(currencies[prefix] ? currencies[prefix].test(span) : span.includes(prefix.toLowerCase()))) return false;
  const suffix = String(item.suffix || '').trim().toLowerCase();
  return !suffix || (suffix === '%' ? /%|\bpercent\b/.test(span) : span.includes(suffix));
}
