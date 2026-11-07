const assert = require('assert');
const {
  buildTranscriptPrompt,
  extractProfileJson,
  sanitizeProfile,
  narratorProfileToPromptText,
  summarizeTranscript,
  buildNarratorProfile,
  NARRATOR_MODEL,
} = require('../src/agents/narratorVideo');

const sampleTranscript = 'This is a sample transcript with enough words to test the narrator profile builder. It talks about techniques and keeps the pacing snappy.';

assert.equal(NARRATOR_MODEL, 'nvidia/nemotron-3.5-super-vl:free');

const prompt = buildTranscriptPrompt({
  title: 'How to Win at Chess',
  channel: 'Test Channel',
  durationSec: 600,
  transcriptChars: sampleTranscript.length,
  transcript: sampleTranscript,
});
assert.ok(/structure/.test(prompt));
assert.ok(/tonality/.test(prompt));
assert.ok(/scriptFlow/.test(prompt));
assert.ok(/audienceBehaviour/.test(prompt));
assert.ok(/do NOT summarize or restate/i.test(prompt));
assert.ok(prompt.includes(sampleTranscript));

const extracted = extractProfileJson('Here you go:\n```json\n{"structure":"hook first","tonality":"energetic","scriptFlow":"linear","audienceBehaviour":"assumes beginner","hookStyle":"cold open","pacing":"fast","sentenceRhythm":"short","retentionTricks":"cliffhangers","signaturePhrases":["you know what","watch this"],"styleNotes":"very punchy"}\n```');
assert.ok(extracted);
assert.equal(extracted.tonality, 'energetic');
assert.ok(Array.isArray(extracted.signaturePhrases));

const bare = extractProfileJson('{ "structure": "plain json", "tonality": "calm" }');
assert.ok(bare);
assert.equal(bare.structure, 'plain json');
assert.equal(extractProfileJson('no json here at all'), null);

const profile = sanitizeProfile(extracted, 'fallback');
assert.equal(profile.tonality, 'energetic');
assert.deepEqual(profile.signaturePhrases, ['you know what', 'watch this']);
assert.ok(profile.styleNotes);

const sanitizedFallback = sanitizeProfile({}, 'LLM output was garbage');
assert.ok(sanitizedFallback.styleNotes.includes('garbage'));

const text = narratorProfileToPromptText(profile);
assert.ok(text.includes('STRUCTURE: hook first'));
assert.ok(text.includes('SIGNATURE PHRASES:'));
assert.equal(narratorProfileToPromptText(null), '');
assert.equal(narratorProfileToPromptText({}), '');

(async () => {
  const selectedFreeModel = 'google/gemma-4-26b-a4b-it:free';
  const modelsSeen = [];
  // summarizeTranscript should return a JSON profile when the LLM complies.
  const fakeChat = async ({ messages, model }) => {
    modelsSeen.push(model);
    const user = messages.find((m) => m.role === 'user').content;
    assert.ok(user.includes('TRANSCRIPT'));
    return { content: '{"structure":"A","tonality":"B","scriptFlow":"C","audienceBehaviour":"D","hookStyle":"E","pacing":"F","sentenceRhythm":"G","retentionTricks":"H","signaturePhrases":["x"],"styleNotes":"I"}' };
  };
  const summarized = await summarizeTranscript({
    apiKey: 'sk-test',
    model: selectedFreeModel,
    video: { title: 'T', channel: 'C', durationSec: 60, transcript: sampleTranscript, transcriptChars: sampleTranscript.length },
    chatCompletionFn: fakeChat,
  });
  assert.equal(summarized.tonality, 'B');

  // buildNarratorProfile with a stubbed transcript fetch and stubbed LLM.
  const fakeFetch = async () => ({
    ok: true,
    video: { title: 'T', channel: 'C', durationSec: 90, url: 'https://www.youtube.com/watch?v=abc' },
    transcript: sampleTranscript,
    transcriptChars: sampleTranscript.length,
    subtitleKind: 'auto',
  });
  const built = await buildNarratorProfile({
    videoUrl: 'https://www.youtube.com/watch?v=abc',
    apiKey: 'sk-test',
    model: selectedFreeModel,
    useCache: false,
    chatCompletionFn: fakeChat,
    fetchTranscriptFn: fakeFetch,
  });
  assert.ok(built.ok);
  assert.equal(built.video.title, 'T');
  assert.equal(built.model, selectedFreeModel);
  assert.ok(modelsSeen.every(model => model === selectedFreeModel), 'narrator analysis keeps the shared selected model');
  assert.equal(built.profile.tonality, 'B');

  await assert.rejects(() => buildNarratorProfile({ videoUrl: '', apiKey: 'sk-test', useCache: false }), /videoUrl/);
  await assert.rejects(() => buildNarratorProfile({ videoUrl: 'x', apiKey: '', useCache: false }), /api key/i);

  console.log('Narrator video checks passed');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
