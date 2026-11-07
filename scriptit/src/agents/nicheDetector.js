const { normalizeTopic } = require('../research/topicNormalizer');

const PATTERNS = [
  { niche: 'sports', words: ['football', 'soccer', 'nba', 'nfl', 'cricket', 'tennis', 'f1', 'formula', 'athlete', 'player', 'match', 'tournament', 'goal', 'league', 'team', 'coach', 'olympics', 'messi', 'ronaldo', 'lebron', 'federer', 'djokovic', 'championship', 'playoff'] },
  { niche: 'finance', words: ['bitcoin', 'crypto', 'ethereum', 'stock', 'market', 'trading', 'investor', 'investment', 'economy', 'inflation', 'fed', 'rate', 'hedge fund', 'wall street', 'nasdaq', 's&p', 'dow', 'dollar', 'currency', 'ipo', 'earnings'] },
  { niche: 'tech', words: ['ai', 'artificial intelligence', 'openai', 'chatgpt', 'gpt', 'llm', 'machine learning', 'google', 'apple', 'microsoft', 'meta', 'nvidia', 'startup', 'software', 'app', 'platform', 'api', 'developer', 'github', 'cyber', 'hack', 'chip', 'gpu'] },
  { niche: 'science', words: ['nasa', 'space', 'mars', 'moon', 'rocket', 'spacex', 'physics', 'quantum', 'biology', 'gene', 'dna', 'crispr', 'climate', 'planet', 'galaxy', 'telescope', 'discovery', 'experiment', 'research', 'scientist'] },
  { niche: 'health', words: ['vaccine', 'covid', 'pandemic', 'virus', 'disease', 'cancer', 'doctor', 'hospital', 'medicine', 'drug', 'fda', 'mental health', 'diet', 'nutrition', 'workout', 'fitness', 'sleep', 'wellness'] },
  { niche: 'politics', words: ['president', 'election', 'senate', 'congress', 'parliament', 'government', 'policy', 'law', 'court', 'supreme', 'biden', 'trump', 'democrat', 'republican', 'vote', 'campaign'] },
  { niche: 'gaming', words: ['game', 'gaming', 'playstation', 'xbox', 'nintendo', 'steam', 'esports', 'twitch', 'developer', 'rpg', 'fps', 'mmo', 'gta', 'grand theft auto', 'minecraft', 'fortnite'] },
  { niche: 'entertainment', words: ['movie', 'film', 'actor', 'actress', 'celebrity', 'music', 'album', 'song', 'concert', 'netflix', 'disney', 'marvel', 'dc', 'box office', 'oscar', 'grammy', 'show', 'series'] },
  { niche: 'history', words: ['war', 'empire', 'dynasty', 'ancient', 'medieval', 'revolution', 'world war', 'civil war', 'colonial', 'king', 'queen', 'civilization'] },
  { niche: 'crime', words: ['murder', 'killer', 'serial', 'fraud', 'scam', 'heist', 'mafia', 'cartel', 'arrested', 'sentenced', 'prison', 'trial', 'verdict', 'investigation'] },
  { niche: 'biography', words: ['born', 'childhood', 'early life', 'rise to fame', 'career', 'personal life', 'legacy', 'died', 'death of', 'life of', 'story of', 'nikola', 'inventor', 'scientist biography', 'who is', 'biography'] },
];

function detectNiche(topic) {
  const t = normalizeTopic(topic).canonical.toLowerCase();
  if (!t) return { niche: 'general', confidence: 0 };
  const scores = PATTERNS.map((p) => {
    let hits = 0;
    for (const w of p.words) {
      if (t.includes(w)) hits += 1;
    }
    return { niche: p.niche, hits };
  }).filter((s) => s.hits > 0);

  if (scores.length === 0) return { niche: 'general', confidence: 0 };
  scores.sort((a, b) => b.hits - a.hits);
  const top = scores[0];
  return { niche: top.niche, confidence: Math.min(1, top.hits / 3) };
}

function suggestFormat(niche) {
  switch (niche) {
    case 'sports':
    case 'finance':
    case 'tech':
      return 'explainer';
    case 'science':
      return 'documentary';
    case 'crime':
    case 'history':
    case 'biography':
      return 'documentary';
    case 'entertainment':
      return 'listicle';
    case 'gaming':
      return 'explainer';
    case 'health':
      return 'explainer';
    case 'politics':
      return 'news';
    default:
      return 'documentary';
  }
}

module.exports = { detectNiche, suggestFormat };
