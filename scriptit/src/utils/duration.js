const DURATION_WORDS = {
  '1min': 210,
  '3min': 630,
  '5min': 1050,
  '8min': 1680,
  '10min': 2100,
  '15min': 3150,
  '20min': 4200,
  '30min': 6300,
  '45min': 9450,
  '60min': 12600,
};

function wordsForDuration(d) {
  if (!d) return 1500;
  const value = String(d).trim().toLowerCase();
  if (DURATION_WORDS[value]) return DURATION_WORDS[value];
  const seconds = /^(\d+(?:\.\d+)?)\s*(sec|secs|second|seconds|s)$/i.exec(value);
  if (seconds) return Math.max(75, Math.min(15000, Math.round(parseFloat(seconds[1]) * (210 / 60))));
  const m = /^(\d+(?:\.\d+)?)\s*(min|mins|minute|minutes|m)$/i.exec(value);
  if (m) return Math.max(75, Math.min(15000, Math.round(parseFloat(m[1]) * 210)));
  const h = /^(\d+(?:\.\d+)?)\s*(hour|hours|hr|hrs|h)$/i.exec(value);
  if (h) return Math.max(75, Math.min(15000, Math.round(parseFloat(h[1]) * 60 * 210)));
  return 1500;
}

function durationToLabel(d) {
  if (!d) return '10 min';
  if (DURATION_WORDS[d]) {
    return d.replace('min', ' minute');
  }
  return d;
}

module.exports = { DURATION_WORDS, wordsForDuration, durationToLabel };
