const DURATION_WORDS = {
  '1min': 160,
  '3min': 480,
  '5min': 800,
  '8min': 1280,
  '10min': 1600,
  '15min': 2400,
  '20min': 3200,
  '30min': 4800,
  '45min': 7200,
  '60min': 9600,
};

function wordsForDuration(d) {
  if (!d) return 1500;
  const value = String(d).trim().toLowerCase();
  if (DURATION_WORDS[value]) return DURATION_WORDS[value];
  const seconds = /^(\d+(?:\.\d+)?)\s*(sec|secs|second|seconds|s)$/i.exec(value);
  if (seconds) return Math.max(75, Math.min(15000, Math.round(parseFloat(seconds[1]) * (160 / 60))));
  const m = /^(\d+(?:\.\d+)?)\s*(min|mins|minute|minutes|m)$/i.exec(value);
  if (m) return Math.max(75, Math.min(15000, Math.round(parseFloat(m[1]) * 160)));
  const h = /^(\d+(?:\.\d+)?)\s*(hour|hours|hr|hrs|h)$/i.exec(value);
  if (h) return Math.max(75, Math.min(15000, Math.round(parseFloat(h[1]) * 60 * 160)));
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
