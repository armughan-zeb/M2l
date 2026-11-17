'use strict';
// Shared fixtures for tests/script-issues-hardening.js (Area 8, 2026-11-16).
// The Leipzig topic is synthetic test material (first used by
// tests/research-timeout-recovery.js test 5). The narration is written to
// pass the editorial gates BOTH with the seeded Wikipedia claims below
// (grounded: every factual sentence overlaps a claim) and without any
// evidence at all (ungrounded: generic sentences carry no checkable facts).

const LEIPZIG_TOPIC = 'The 1923 Leipzig Balloon Expedition';

const LEIPZIG_NARRATION = [
  'What lifts a person above the rooftops and lets the wind decide where the journey ends?',
  'In 1923, the Leipzig Balloon Expedition left Leipzig and drifted north across the plains, carrying instruments to measure the wind at altitude. The crew spread the envelope across the field, checked every seam and line, and filled the great cloth bag with warm air until it strained upward against its ropes.',
  'The crew tracked the winds for days, choosing each launch with care and recording every change in direction. Once airborne, the world went quiet. There was no engine hum, no vibration, only the soft rush of air past the basket and the slow turn of the landscape below.',
  'The Leipzig Balloon Expedition drifted on past the Elbe River toward the North Sea coast, proving that a balloon could travel farther than anyone had dared to hope. Balloon pilots of the period read the sky the way sailors read the sea, trusting preparation and patience over speed.',
  'Ballooning still rewards the same qualities today. The pilot who respects the wind, prepares the craft with care and accepts what the sky allows will always find the journey worth making.',
].join('\n\n');

// Two Wikipedia items with article bodies: reference tier, high confidence,
// so buildEvidenceLedger reports sufficient (bodyClaims >= 1). Titles and
// bodies repeat the topic anchors (1923 / Leipzig / balloon / expedition)
// so relevanceScore admits them. The narration above is built from these
// sentences, so its factual lines overlap the claims.
function leipzigResearchBundle() {
  return {
    query: LEIPZIG_TOPIC,
    gatheredAt: new Date().toISOString(),
    duckduckgo: [],
    duckduckgoNews: [],
    rss: [],
    bing: [],
    bingNews: [],
    yandex: [],
    x: [],
    wikipedia: [
      {
        title: 'Leipzig balloon expedition',
        url: 'https://en.wikipedia.org/wiki/Leipzig_balloon_expedition',
        source: 'Wikipedia',
        snippet: 'In 1923, the Leipzig Balloon Expedition left Leipzig and drifted north across the plains.',
        articleText: 'In 1923, the Leipzig Balloon Expedition left Leipzig and drifted north across the plains, carrying instruments to measure the wind at altitude. The crew tracked the winds for days, choosing each launch with care and recording every change in direction.',
        hasArticleBody: true,
      },
      {
        title: 'Leipzig balloon expedition',
        url: 'https://en.wikipedia.org/wiki/Leipzig_balloon_expedition_north_sea',
        source: 'Wikipedia',
        snippet: 'The Leipzig Balloon Expedition drifted on past the Elbe River toward the North Sea coast.',
        articleText: 'The Leipzig Balloon Expedition drifted on past the Elbe River toward the North Sea coast, proving that a balloon could travel farther than anyone had dared to hope. Balloon pilots of the period read the sky the way sailors read the sea, trusting preparation and patience over speed.',
        hasArticleBody: true,
      },
    ],
    hackernews: [],
    reddit: [],
    official: [],
    structured: [],
    targeted: [],
    errors: [],
  };
}

function emptyResearchBundle(query) {
  return {
    query,
    gatheredAt: new Date().toISOString(),
    duckduckgo: [],
    duckduckgoNews: [],
    rss: [],
    bing: [],
    bingNews: [],
    yandex: [],
    x: [],
    wikipedia: [],
    hackernews: [],
    reddit: [],
    official: [],
    structured: [],
    targeted: [],
    errors: ['all providers failed'],
  };
}

// Apollo 11 2-min script, condensed from the recorded generation-history
// entry (2026-11-15, 545 words). The 1-min draft below reuses its sentences
// verbatim — the realistic shape of the blocked Apollo 1-min run: same
// topic, same evidence claims, so the writer reused whole sentences.
const APOLLO_TOPIC = 'The Apollo 11 Moon Landing';

const APOLLO_2MIN_SCRIPT = [
  'July 20, 1969, 20:17 UTC. The Lunar Module Eagle touches down in the Sea of Tranquility, and for the first time, human beings have landed on the Moon. About six hours later, Neil Armstrong steps out of the cabin and becomes the first person to walk on another world. An estimated 600 million people are watching live, roughly a fifth of everyone alive.',
  'The race had started twelve years earlier, on October 4, 1957, when the Soviet Union launched Sputnik 1, the first artificial satellite. Its success surprised the world and ignited the Space Race. President Dwight D. Eisenhower answered by creating NASA and starting Project Mercury, aimed at putting a man into Earth orbit.',
  'On July 16, 1969, at 13:32 UTC, a Saturn V rocket lifted Apollo 11 off from Kennedy Space Center in Florida. Three astronauts rode in a spacecraft of three parts: the command module, the only part that would return to Earth; the service module, carrying propulsion, power, oxygen and water; and the Lunar Module, built in two stages to land two men and bring them back up.',
  'After a three-day transit, Armstrong and Aldrin took the Eagle down while Michael Collins stayed alone in lunar orbit aboard the command module Columbia. Armstrong stepped out about six hours after landing, Aldrin nineteen minutes after him. They spent around two and a half hours on the surface, planting an American flag, speaking with President Richard Nixon from the White House, and gathering 21.5 kilograms of lunar material.',
  'After more than twenty-one hours on the Moon, they lifted off, rejoined Collins, and came home, splashing down in the Pacific Ocean on July 24. The command module Columbia is preserved at the National Air and Space Museum in Washington, while the Eagle\u2019s descent stage still sits where it landed.',
].join('\n\n');

// The fresh 1-min Apollo draft: the first three paragraphs of the 2-min
// script (~185 words). Its 4-gram shingles are a subset of the longer
// script, so similarity() scores it 1.000 — a different-duration deliverable
// that the old gate rejected as "too similar".
const APOLLO_1MIN_SUBSET_DRAFT = APOLLO_2MIN_SCRIPT.split('\n\n').slice(0, 3).join('\n\n');

// Same facts, reworded and reordered: similarity to the 2-min script is
// ~0.10, so even the old 0.22 threshold would not block it. Included to show
// the block only ever fired on reused sentences, not on fresh writing.
const APOLLO_1MIN_REWORDED_DRAFT = [
  'On July 20, 1969, a small spacecraft called Eagle settled onto the Moon in the Sea of Tranquility. Six hours later Neil Armstrong climbed down the ladder and became the first human to stand on another world, while around 600 million people watched on television.',
  'The flight had begun four days earlier, on July 16, when a Saturn V rocket carried Apollo 11 away from Kennedy Space Center in Florida. Three men were aboard: Armstrong, Buzz Aldrin, and Michael Collins, who stayed in orbit in the command module Columbia while the other two went down to the surface.',
  'Armstrong and Aldrin spent about two and a half hours outside, planting a flag, setting out science instruments, talking to President Nixon, and collecting 21.5 kilograms of rock. Then they lifted off, met Collins again, and splashed down in the Pacific on July 24.',
  'The landing ended a race that had started with Sputnik in 1957 and Kennedy\u2019s promise in 1961 to reach the Moon before the decade ended. Apollo 1 had burned on the launchpad in 1967, and the Mercury and Gemini flights had taught NASA how to fly there.',
  'Columbia now sits in the National Air and Space Museum in Washington. The Eagle\u2019s lower half still rests where it landed, and later orbiters have photographed the footprints the astronauts left behind.',
].join('\n\n');

module.exports = {
  LEIPZIG_TOPIC,
  LEIPZIG_NARRATION,
  leipzigResearchBundle,
  emptyResearchBundle,
  APOLLO_TOPIC,
  APOLLO_2MIN_SCRIPT,
  APOLLO_1MIN_SUBSET_DRAFT,
  APOLLO_1MIN_REWORDED_DRAFT,
};
