# GTA VI 1-min — per-scene accuracy diagnosis (proof job a57cb5c7)

- **Job:** `a57cb5c7`, run `a57cb5c7-run-0002-fabe0c5d7c55`, output dir
  `~/workspace/vidnets-runtime/output/GTA_VI_1Min_20261116_181729_8f06fbb8/`
- **Repo / code:** `~/workspace/M2l`, branch `codex/muse-agent-driven-reconnect`, HEAD `55edcca`
  ("Media pipeline fix: no-limit web, parallel YT, validator-only vision, gaming 90% YT").
- **Outcome (from the job's own records):** 6/8 real — web 5/8, YouTube 1/8, graphics 2/8
  (`checkpoints/media_plan.json`: `source_accounting` Web Images 5, YouTube Extracted Frame 1,
  Grounded Graphic Fallback 2; `terminal_accounting` accepted 6, degraded 2).
- **Method:** read-only. Every claim below is tied to a record in the proof job or to the code at
  HEAD `55edcca`. Where the records do not prove a claim, the table says so explicitly
  ("NOT IN RECORDS") instead of inferring it.
- **Index convention:** `media_plan.json` scenes are **0-based** (0–7). `queries.json` rows are
  **1-based** (`scene_number` 1–8). `youtube_scene_00N.attempts.json` files are 1-based.
  This document uses 0-based scene numbers throughout and gives the 1-based row where it matters.
- **Correction applied (user order, 2026-11-16 ~20:21 PKT):** GTA V footage is allowed in a GTA VI
  video **where the scene's own narration is about GTA V** (its sales, the decade-long wait, its
  legacy). GTA V is wrong only for scenes whose narration is specifically about GTA VI content
  (trailers, Leonida, Lucia/Jason, the new Vice City). The 2002 GTA: Vice City cutscene stays
  rejected (wrong era, dated, watermarked). Every GTA V rejection below is re-classified
  scene-by-scene against that scene's narration. The "subject game" column is the game the
  scene's narration is about, not the video's title.

## Evidence read

| Record | What it proves |
|---|---|
| `checkpoints/media_plan.json` (`metadata.media_resolution_v2`, `source_labels`, `source_accounting`, `visual_requirements`, `source_scene_texts`) | Per-scene winner, `query_used`, `candidate_id`, `asset_fingerprint`, `fallback_reason`, `visual_decision`, full `attempts` trace, `candidate_ledger` |
| `checkpoints/provider_web_candidates.json` | Web pool actually returned per scene (keys `"0"`, `"1"`, `"6"` only), per-candidate engine audits, `downloaded_candidate_count`, `reused` flags |
| `checkpoints/queries.json` (`web`, `youtube`, `stock`) | Planned queries per scene (1-based), `visual_intent`, `forbidden_subjects`, `retrieval_positive_terms` / `retrieval_negative_terms` (all `[]`) |
| `checkpoints/media_history/media_plan-ff8f…​.json` and `media_plan-ea7e…​.json` | Two earlier resolution passes of the same job |
| `logs/youtube-worker-3f7b9d458a3a25c0.log` (13:28 UTC), `logs/youtube-worker-4a75879ea57c5792.log` (13:53 UTC), `logs/youtube-worker-16c8d58ebe18d83c.log` (14:07 UTC) | YT search counts, rank-audit buckets, download outcomes |
| `temp/jobs/a57cb5c7/a57cb5c7-run-0002-fabe0c5d7c55/youtube_scene_00{1..7}.attempts.json`, `yt_clip_scene_00{1,2,3,6,7}.mp4.audit.json`, `youtube_content_recovery/youtube_scene_00{1..4}.attempts.json` | The one YT clip attempted per scene: title, channel, `metadata_score`, download status |
| `temp/vidnets-answer-*.json` (13:58–14:26 UTC 2026-11-16) | Vision verdicts (clip-level, per-frame, frame-selection). File hashes are cited per verdict below |
| `vidnets/server/services/youtube_pipeline_v2.py`, `universal_clip_classifier.py`, `media_resolution_coordinator.py`, `media_query_strategy.py`, `visual_requirement.py`, `vidzeo_brain.py` at HEAD `55edcca` | The gates, caps, budgets and validator the job ran under |

## Headline numbers (all from the records)

- **Web:** 5 distinct images were ever downloaded (`provider_web_candidates.json`: 1 for scene 0,
  4 for scene 1, 2 rows / 1 distinct for scene 6). **All 5 are Pinterest-hosted**
  (`br.pinterest.com` / `i.pinimg.com`). Only **3 distinct images** appear in the finished video:
  GTA VI key art (md5 `f8afedec2…`, scenes 0 + 4), Vice City/Miami shot (md5 `7d05b5fd0…`,
  scenes 3 + 7), Miami neon still (scenes 1 only; md5 of the file on disk `6bb444f7…`).
  Two further vision-accepted web images in scene 1's pool (Miami skyline `a8646625…`,
  second Vice City shot `ec2409c6…`) were **never used by any scene**.
- **Web wave 1 returned 0 candidates for all 8 scenes** (`media_plan.json` attempts:
  `web discovery_result wave 1 status empty candidate_count 0` for every scene). Every web
  candidate in the job came from the cross-lane / late-spool fallback, not from wave 1.
- **YouTube:** the 384-candidate search (log `youtube-worker-4a75879ea57c5792.log`, 13:53 UTC)
  produced **1 metadata winner per scene, 5 downloaded, 3 download-failed, 1 accepted as a
  frame** (scene 5, `_yl5ELcmugI:frame:2`). The 90% gaming YT target was missed at 1/8.
- **Reuse is the default, not the exception:** of the 5 web acceptances, 3 are reuses
  (scenes 3, 4, 7). The web reuse cap is 2 (`media_resolution_coordinator.py:1215-1237`).
  Both reused images sit exactly at the cap (key art: scenes 0 + 4; Vice City shot: scenes 3 + 7).
- **Earlier checkpoint `media_plan-ff8f…​.json` had scenes 2 AND 6 accepted**
  (scene 2 with scene 1's image, scene 6 with `youtube:qq76pQsI1iw:frame:1`); the final plan
  degrades both. The overturning step is **not logged** anywhere in the job's records (§B).

## Per-scene diagnosis table

Narration quotes are verbatim from `media_plan.json` → `metadata.source_scene_texts`
(also `checkpoints/provider_web_candidates.json` → `scene_texts`).

### Scene 0 — accepted, WEB (key art) — subject game: GTA V (history/legacy)

- **Narration gist:** "Grand Theft Auto Six is the most anticipated video game of the decade, and
  Rockstar Games has kept fans waiting for more than ten years since Grand Theft Auto Five, a game
  that has sold more than two hundred million copies." **Subject game: GTA V** — the scene's
  factual content is GTA V's sales and the wait it caused. (The title "Six" is spelled out;
  see §C for why that spelling matters to the gates.)
- **Queries planned** (`queries.json` web `scene_number` 1; YT `scene_number` 1):
  web primary `Rockstar Games logo studio`, supporting `Rockstar Games Grand Theft Auto VI key art
  official`, fallback `Rockstar Games headquarters building photo`; YT variants include
  `Rockstar Games studio Grand Theft Auto VI announcement` and
  `Rockstar Games Grand Theft Auto VI V 200 million copies sold milestone`.
  `retrieval_positive_terms` / `retrieval_negative_terms`: `[]`.
- **Queries actually used:** web winner's `query_used` =
  `Rockstar Games Grand Theft Auto Six photograph` (the 2nd planned query in the winner's
  `queries` list, not the primary). The YT worker's actually-attempted query was
  `Rockstar Games studio Grand Theft Auto VI announcement gameplay footage`
  (`youtube_scene_001.attempts.json`) — **not verbatim any `queries.json` variant**.
- **Candidates — web:** pool of **1** (`provider_web_candidates.json` key `"0"`).
  Engine audits on the winner: query 1 (`Rockstar Games Grand Theft Auto VI key art official`)
  **0 rows** — Yandex, Bing, Flickr all `ReadTimeout` (~15 s), DuckDuckGo bot-gate;
  query 2 (`Rockstar Games Grand Theft Auto Six photograph`) **60 rows** (Yandex 20, Bing 20,
  Flickr 20). `downloaded_candidate_count` 8, pool 1: of 60 rows only the key art survived
  dedupe/rank/download. The winner's own `queries` list names 8 planned queries
  (incl. `Rockstar Games Grand Theft Auto Five photograph` and `… Five documentary`);
  **no engine-audit rows exist for queries 3–8, so whether they were ever issued is NOT IN
  RECORDS.**
- **Candidates — YouTube:** **1** attempted: `N-xHcvug3WI`, `Grand Theft Auto V: Official Gameplay
  Video`, Rockstar Games, `metadata_score` **356.0** (the highest metadata score of any clip in
  the job), timestamp 14.0 s, downloaded, quality-check passed.
- **Winner and why:** web key art, `web:scn_920430008982465c3dae`,
  `https://br.pinterest.com/pin/959196420643250674/` (image `i.pinimg.com/736x/51/73/8d/…`,
  md5 `f8afedec2bf23c74dfc240d075b8318e`, sha256 fingerprint `63a2158ab7c2…`).
  Deterministic resolver verdict, not a pixel judgment: identity 100 / action 100 / context 55 /
  quality 85 / final **55.0**, `verification_status: deterministic_web_image`,
  `fallback_reason: web_image_rescued_before_stock_fallback`. It won because it was the **only**
  web candidate in the scene's pool, and the YT clip had already been rejected at the visual gate.
- **Why the others lost:** the YT clip `N-xHcvug3WI` passed the hard gate, then the visual gate
  recorded `insufficient_visible_subject_match`; the clip-level vision verdict
  (`vidnets-answer-c49a2401a64f45db89ced80a7.json`, 13:58 UTC) is **REJECT, final 25,
  `fatal_mismatch: true`, `entity_conflict: true`** — "footage is Grand Theft Auto V gameplay,
  not Grand Theft Auto VI… Wrong game, wrong city (Los Santos, not Vice City/Leonida)".
  **That rejection is WRONG under the corrected scene-relative rule:** the verdict's own reason
  names the scene as "a GTA VI scene about Rockstar Games and GTA V's 200 million sales" and
  then rejects the footage for being GTA V — the very game the narration is about. A second,
  later vision pass judged the same frames correctly: the frame-selection verdict
  (`vidnets-answer-9ef9f4a2f2aab22e612779295aa55434.json`, 14:09 UTC) is **acceptable: true,
  `selected_index: 1`**, reason: "the narration explicitly cites GTA V (200M+ copies sold) and
  the decade-long wait for GTA VI, so GTA V franchise footage is a closely-related real shot
  for this scene… No candidate is the wrong subject". **No record in the job consumes that
  accept** — scene 0's attempts contain no `verified_topic_frame_pool` entry, and the frames
  were recorded only as `skipped_after_scene_budget`. The correct GTA V footage was therefore
  available, vision-approved in one pass, and still not used.
- **Status:** accepted (web). **Accuracy verdict: WRONG MEDIA for the narration** — key art of
  GTA VI illustrates a scene whose facts are GTA V's sales/legacy; the correct GTA V clip was
  rejected by the clip-level verdict and its later frame-level acceptance was never used.
  **GTA V rejection classification: WRONG (1 of 1 clip-level rejections for this scene).**

### Scene 1 — accepted, WEB (Miami neon still) — subject game: GTA VI (new Vice City)

- **Narration gist:** "The game returns to Vice City, now set in the fictional state of Leonida,
  a sun-soaked reimagining of Florida filled with beaches, swamps, and neon streets."
  **Subject game: GTA VI.**
- **Queries planned** (web #2; YT #2): web primary `Vice City neon streets`, supporting
  `Vice City Miami South Beach neon night photo`, fallback `Vice City Florida Everglades swamp
  photo`; YT primary `Vice City Grand Theft Auto VI neon streets night footage`.
  `visual_intent`: "Aerial and street-level shots of neon-lit Vice City streets, beaches and
  swamps". Positive/negative terms: `[]`.
- **Queries actually used:** winner's `query_used` = the supporting query
  `Vice City Miami South Beach neon night photo`. The YT worker's attempted query was the
  generic `Vice City footage` (`youtube_scene_002.attempts.json`), not the planned query.
- **Candidates — web:** pool of **4**, all Pinterest (`i.pinimg.com`): primary
  `2064943c…` (Miami South Beach neon), alt1 `4f/61/82/…` (Vice City shot), alt2 `a8/64/66/…`
  (Miami skyline night), alt3 `ec/24/09/…`. Query 1 (`Vice City neon streets`) 0 rows;
  query 2 (`Vice City Miami South Beach neon night photo`) **65 rows**. `downloaded_candidate_count`
  8, pool 4. All four rows carry an identical deterministic verdict (identity 100 / action 100 /
  context 55 / quality 85 / final 55.0) in the ledger — the "accept" is metadata-driven, not
  pixel-driven.
- **Candidates — YouTube:** **1** attempted: `IJQO-LxtwQM`,
  `GTA VICE CITY Gameplay Walkthrough FULL GAME [4K 60FPS PS5 PRO] - No Commentary`, channel
  `MKIceAndFire`, `metadata_score` 234.0, timestamp 7046.73 s, transcript match 86.652
  ("town is now. It's all out there waiting for us…"). Downloaded.
- **Winner and why:** web primary `2064943c…` (`web:scn_9d3c414661a07bf7f74b`, fingerprint
  `eacaf5156564…`), `fallback_reason: web_image_rescued_before_stock_fallback`, final 55.0.
  It won as the top-ranked of the only web pool in the job, after the YT clip failed vision.
  It is a real Miami/Vice City-style neon photo and is broadly on-topic — but note it is a
  **real-world Miami photo**, not GTA VI footage, for a scene about the game's fictional Vice City.
- **Why the others lost:** `IJQO-LxtwQM` — clip vision
  (`vidnets-answer-cbc4425b03cf3226603340d20cbe4b14.json`, 13:58 UTC) **REJECT, final 30,
  `fatal_mismatch: true`**: "footage is the original 2002 Grand Theft Auto: Vice City, not Grand
  Theft Auto VI… dated low-poly 1980s characters… subtitles naming Tommy". Frame-selection
  (`vidnets-answer-e64470e36f1943550d32f858fc3d4b6a.json`, 14:11 UTC) **acceptable: false**:
  "every frame carries a visible watermark in the bottom-right corner. Wrong game and era."
  **Correct rejection — stays rejected per the user's order.** The same 2002 footage also
  supplied the scene's `N-xHcvug3WI` frame pool: frame-selection
  (`vidnets-answer-67e75ece1a4513f4d8891bcd1aeda93f.json`, 14:13 UTC) **acceptable: false** —
  "None shows Vice City, its beaches, swamps, or neon streets. GTA V footage is the wrong game
  and the wrong location for this scene" — **correct rejection** (GTA VI-subject scene).
  In the coordinator, the `N-xHcvug3WI` frames failed the hard gate as
  `canonical_entity_mismatch` (attempts). Web alt1/alt2/alt3 were accepted in the ledger but
  not selected; alt1 went on to serve scenes 3 and 7.
- **Status:** accepted (web). **Accuracy verdict: broadly on-topic but not game footage**
  (real Miami photo for a fictional-city scene). **GTA V rejections: CORRECT (2002 Vice City:
  correct; GTA V frames: correct).**

### Scene 2 — DEGRADED, graphic — subject game: GTA VI (Leonida Keys / trailers)

- **Narration gist:** "The trailers show the Leonida Keys, the swamps of the Grassrivers, and the
  neon of Vice City itself, a world far bigger and more detailed than anything Rockstar has built
  before." **Subject game: GTA VI.**
- **Queries planned** (web #3; YT #3): web primary `Leonida Keys Florida Keys aerial photo`,
  supporting `Leonida Keys Florida Keys bridge turquoise water`, fallback
  `Leonida Keys tropical island chain aerial photograph`; YT primary
  `Leonida Keys Grand Theft Auto VI Keys Florida island chain aerial footage`.
  `visual_intent`: "Island chain and tropical keys scenery with boats and turquoise water".
- **Queries actually used:** `query_used` in the final plan is
  `Vice City Miami South Beach neon night photo` — **scene 1's query, not this scene's own**.
  This scene's own Leonida Keys queries produced **no surviving candidate anywhere in the
  records** (`provider_web_candidates.json` has no key `"2"`; attempts: web discovery empty,
  web cross-lane fallback empty, YT discovery empty).
- **Candidates — web:** **0 of its own.** The only web rows that ever touched this scene are
  scene 1's pool, via rescue (below).
- **Candidates — YouTube:** the worker **did** download a real candidate for this scene, and the
  coordinator never saw it: `uInR-MJOVeo`, `GTA 6 Leonida Keys – Everything Confirmed`, channel
  `GTA6`, `metadata_score` 145.0, timestamp 213.1 s, status `downloaded`, audit `selected: true`,
  `semantic_verified: true`, `visual_checked: true`, watermark probability 0.9 (tolerated)
  (`youtube_scene_003.attempts.json`, `yt_clip_scene_003.mp4.audit.json`). **It appears 0 times
  in `storyboard.json`, `scene_contracts.json` and `media_plan.json`, and has no vision verdict
  file and no attempts entry.** Its disappearance between worker and coordinator is
  **NOT IN RECORDS — no rejection entry exists for it anywhere in the job.**
  The recovery worker separately attempted the real official trailer
  `VQRLujxTm3c`, `Grand Theft Auto VI Trailer 2`, Rockstar Games, `metadata_score` 224.0
  (`youtube_content_recovery/youtube_scene_003.attempts.json`) — **download_failed,
  `transport_timeout` after 102.16 s** (`youtube_clip_timeout_after_61s`; log
  `youtube-worker-16c8d58ebe18d83c.log`: "Sign in to confirm you're not a bot" for the same
  video ID). No vision verdict exists for it either (never downloaded).
- **Why it degraded:** attempts in order — web discovery empty (0.27 ms); YT discovery empty;
  web cross-lane fallback empty (171.8 s); `verified_topic_frame_pool` 4 × `N-xHcvug3WI` frames,
  all hard-gate `canonical_entity_mismatch`; `pre_stock_web_rescue` **accepted** scene 1's image
  (`…/web_v2_1794837785694_ab481a/scene_0002.jpg`, the Miami still already used by scene 1);
  then `graphic terminal_media_fallback grounded_after_real_media_exhausted`.
  So the rescue **did** accept a real image and the scene **still** ended graphic. The step
  that overturned the accepted rescue is **NOT IN RECORDS** — no gate attempt is logged
  between the rescue-accepted line and the graphic line. The earlier checkpoint
  `media_plan-ff8f…​.json` shows this scene **accepted** with that same image, so the
  overturn happened in a later resolution pass. The most likely mechanism (named as a
  suspect, not proven) is the adjacent-reuse diversity gate: the rescued image is scene 1's
  asset and scene 2 is adjacent to scene 1 ("the same still in two consecutive scenes reads
  as a frozen frame", `media_resolution_coordinator.py:3373`, `:5824-5833`
  `adjacent_asset_reuse_blocked` — which is recorded firing for scene 7, but **not**
  recorded for scene 2). Note also that scene 1's pool held two **unused, vision-accepted**
  alternates (alt2 Miami skyline, alt3) that the rescue never took; why the rescue preferred
  the already-used primary over the unused alternates is **NOT IN RECORDS**.
- **GTA V verdicts for this scene:** `N-xHcvug3WI` frames rejected in frame-selection
  (`vidnets-answer-661b745f1ceae905c128d9edf15bd5b3.json`, 14:14 UTC) **acceptable: false** —
  "All four candidates are GTA V gameplay, not GTA VI Leonida Keys footage: wrong game and
  wrong map" — **CORRECT rejection** (GTA VI-subject scene).
- **Status:** degraded, graphic (`graphic:fallback:2`, `fallback_reason:
  all_verified_real_media_exhausted`). **Accuracy verdict: no real media at all, although a
  downloaded Leonida Keys clip (`uInR-MJOVeo`) and a real Trailer 2 attempt existed.**
  **What was exhausted:** not the pool of real media in the world — the scene's own web wave
  (0 rows), the coordinator-visible YT pool (0, because the one downloaded clip never arrived),
  and the frame pool (4 wrong-game frames). The rescue's one accepted image was then
  overturned by an unlogged step.

### Scene 3 — accepted, WEB (Vice City shot, reused) — subject game: GTA VI (Lucia/Jason)

- **Narration gist:** "At the center of the story are Lucia Caminos and Jason Duval, two partners
  in crime whose story begins with Lucia walking out of prison and reuniting with Jason."
  **Subject game: GTA VI.**
- **Queries planned** (web #4; YT #4): web primary
  `Lucia Caminos and Jason Duval Lucia Caminos Jason Duval GTA VI`, supporting
  `Lucia Caminos and Jason Duval GTA VI Lucia Caminos character art`, fallback `… Jason Duval
  character art`; YT primary `Lucia Caminos and Jason Duval Lucia Caminos Jason Duval Grand Theft
  Auto VI characters`. `visual_intent`: "Lucia Caminos leaving prison gates and embracing Jason
  Duval". Note the query repeats the entity three times and carries **no viewpoint and no
  action** — the mined viewpoint ("prison gates", "embracing") from the visual intent never
  entered the issued query (§D).
- **Queries actually used:** `query_used` = `Vice City Miami South Beach neon night photo`
  (**scene 1's query**). This scene's own Lucia/Jason queries produced **no surviving
  candidate** (`provider_web_candidates.json` has no key `"3"`; attempts: web discovery empty,
  web cross-lane empty, YT discovery empty).
- **Candidates — web:** **0 of its own.** Winner is scene 1's pool alt1.
- **Candidates — YouTube:** **1** attempted: `mHaQ_n03qeU`,
  `JASON DUVAL VS LUCIA CAMINOS ! #GTA6`, channel `SAVVYONE619`, description
  "Gran Theft auto 6 in WWE 2k26.", `metadata_score` 46.0 (lowest of the job), timestamp 97.8 s,
  **download_failed** (`yt_dlp_or_ffmpeg`, "Sign in to confirm you're not a bot",
  log `youtube-worker-4a75879ea57c5792.log`). The recovery worker attempted the real official
  trailer `l6RRdTFuepk`, `Grand Theft Auto VI - Trailer 2 | PS5 Games`, PlayStation,
  `metadata_score` 224.0 (`youtube_content_recovery/youtube_scene_004.attempts.json`) —
  **download_failed, `transport_timeout` after 108.67 s**
  (`youtube_clip_timeout_after_61s`). Neither was ever vision-checked.
- **Winner and why:** web alt1 of scene 1's pool (`web:scn_9d3c414661a07bf7f74b:alt1`,
  `https://i.pinimg.com/736x/4f/61/82/4f6182bed8a2288fa0d6d1e818f754de.jpg`, md5 `7d05b5fd0…`,
  fingerprint `ebd04eb9c5cb…`), `fallback_reason: web_image_rescued_before_stock_fallback`,
  score 0.8935714285714287. It is the **same Vice City shot that also serves scene 7**
  (reuse count 2 = cap). It won purely as a rescue reuse: it is a generic Vice City image with
  **nothing depicting Lucia, Jason, a prison, or a reunion**. The scene's `visual_decision`
  is the boilerplate deterministic verdict (identity 100 / action 100 / context 55 / final 55.0)
  stamped onto the wrong scene's image.
- **Why the others lost:** the only YT candidate was a WWE 2K26 parody video that failed to
  download; the real Trailer 2 failed to download on timeout. The `N-xHcvug3WI` frame pool
  (4 frames) failed the hard gate as `forbidden_confusable_entity`. Frame-selection
  (`vidnets-answer-14365209f01b852611d523ba5c7847ef.json`, 14:15 UTC) **acceptable: false** —
  "None of the four frames depicts Lucia Caminos or Jason Duval or any GTA VI footage. All four
  are gameplay from Grand Theft Auto V" — **CORRECT rejection** (GTA VI-subject scene).
- **Status:** accepted (web). **Accuracy verdict: WRONG MEDIA** — a generic Vice City still for
  a scene about two named protagonists leaving prison. **GTA V rejection: CORRECT.**

### Scene 4 — accepted, WEB (key art, reused) — subject game: GTA VI (the trailers)

- **Narration gist:** "The first trailer arrived in December 2023 and broke viewing records
  within a day, while a second trailer in May 2025 showed more of Leonida and its characters."
  **Subject game: GTA VI.**
- **Queries planned** (web #5; YT #5): web primary `Leonida Grand Theft Auto VI landscape`,
  supporting `Leonida GTA VI characters`, fallback `Leonida GTA VI screenshots`; YT primary
  `Leonida Grand Theft Auto VI world showcase`. Note `forbidden_subjects` for this row includes
  `video game official trailer gameplay` — the very footage the scene is about (§D).
- **Queries actually used:** `query_used` = `Rockstar Games Grand Theft Auto Six photograph`
  (**scene 0's query**). Own queries produced **no surviving candidate**
  (`provider_web_candidates.json` has no key `"4"`; attempts: web discovery empty, web
  cross-lane empty — 9 ms, YT discovery empty).
- **Candidates — web:** **0 of its own.** Winner is scene 0's key art, second use (cap 2).
- **Candidates — YouTube:** **1** attempted: `Srn4oirGZIQ`,
  `GTA 6: Explore Leonida | All 6 Locations & 8 Characters (Cinematic Showcase) #gta6`,
  channel `RIFTRUSH`, `metadata_score` 72.0, timestamp 51.1 s, **download_failed,
  `transport_timeout` after 86.81 s** (`youtube_clip_timeout_after_61s`,
  "Requested format is not available" also logged). No recovery attempt exists for this scene
  (`youtube_content_recovery/` holds scenes 001–004 only). No vision verdict exists for it.
- **Winner and why:** web key art (`web:scn_920430008982465c3dae`, md5 `f8afedec2…`, same file
  as scene 0), `fallback_reason: web_image_rescued_before_stock_fallback`, final 55.0.
  Second use of the key art — the cap (2) is now spent, which is what later blocks scene 6 (§B).
- **Why the others lost:** the only YT candidate died on download timeout. The
  `N-xHcvug3WI` frame pool (4 frames) passed the hard gate and failed the visual gate as
  `insufficient_visible_subject_match` in the coordinator; the per-frame vision verdicts
  (`vidnets-answer-282c6d3f…​`, `-287db4d21f…​`, `-6d75118d3e…​`, `-aadb6c27c…​`,
  14:18–14:19 UTC) are all **REJECT, final 14–18, `fatal_mismatch: true`** — "this is Grand
  Theft Auto V gameplay from 2013… while the narration is about the Grand Theft Auto VI
  trailers (December 2023 and May 2025) showing Leonida and Vice City" — **CORRECT
  rejections** (GTA VI-subject scene).
- **Status:** accepted (web). **Accuracy verdict: WRONG MEDIA for the narration** — static key
  art for a scene about two record-breaking trailers; no trailer frame is shown.
  **GTA V rejection: CORRECT.**

### Scene 5 — accepted, YOUTUBE frame (the only YT win) — subject game: GTA VI (delays)

- **Narration gist:** "Rockstar first promised the game for autumn 2025, then pushed it to May
  2026, and later delayed it again to November 19, 2026, saying the extra time is needed to
  finish it to their standard." **Subject game: GTA VI.** (The dates are abstract — no frame
  can literally show a delay; vision correctly judged the trailer frames as documentary
  context.)
- **Queries planned** (web #6; YT #6): web primary `Rockstar Games GTA VI delay announcement`,
  supporting `Rockstar Games GTA VI November 19 2026 release date`; YT primary
  `Rockstar Games Grand Theft Auto VI GTA delayed November 2026 announcement`.
- **Queries actually used:** `query_used` is empty (`''`) — the winner is a YT frame with no
  query recorded. The YT worker's attempted query was
  `Rockstar Games Grand Theft Auto VI GTA delayed November 2026 announcement gameplay`
  (`youtube_scene_006.attempts.json`).
- **Candidates — web:** **0** (`provider_web_candidates.json` has no key `"5"`; attempts: web
  discovery empty, web cross-lane empty).
- **Candidates — YouTube:** **1** attempted: `_yl5ELcmugI`, `Grand Theft Auto VI: Pre-Load Now`,
  Rockstar Games, `metadata_score` 217.0, timestamp 3.0 s, downloaded, quality-check passed.
- **Winner and why:** `youtube:_yl5ELcmugI:frame:2` (fingerprint `d7e4632a9bd7…`),
  `fallback_reason: same_video_timestamp_frame`, `visual_decision` final 78.75,
  status `deterministic_degraded`. Clip-level vision
  (`vidnets-answer-3535f6f1c7cc4751341c1d179cf31bd3.json`, 13:58 UTC) **ACCEPT, final 70**:
  "frames are Grand Theft Auto VI trailer footage - neon-lit Vice City nightlife, the GTA VI
  logo… Correct game and setting… Note the final frame carries a 'PRE-LOAD NOW' promotional
  text overlay". The clip itself was recorded `visual_rejected` at the coordinator's visual
  gate, but the frame fallback recovered it: frame-selection
  (`vidnets-answer-b70c20c9f82aeee7cd4706b94568e41f.json`, 14:20 UTC) **acceptable: true,
  `selected_index: 1`**, and the per-frame verdict
  (`vidnets-answer-cdd21f92149ba280f0e250d74a57884f.json`, 14:21 UTC) **ACCEPT, final 72**:
  "Authentic GTA VI trailer title card… Correct game, correct era (Trailer 2, May 2025)".
  Frame 4 (the `PRE-LOAD NOW` card) was correctly excluded as `explicit_scene_conflict` in
  the attempts. This is the job's only moving-clip-derived win — and it is a **still frame**,
  not a moving clip.
- **Why the others lost:** there were no other candidates for this scene in either lane.
- **Status:** accepted (YouTube extracted frame). **Accuracy verdict: CORRECT game and era,
  acceptable documentary context for an abstract date narration; a still, not a moving clip.**
  **GTA V rejections: none for this scene (GTA V never entered its pool).**

### Scene 6 — DEGRADED, graphic — subject game: GTA VI (launch platforms)

- **Narration gist:** "When it arrives, it launches first on PlayStation 5 and Xbox Series X
  and S, with a PC version yet to be announced." **Subject game: GTA VI.**
- **Queries planned** (web #7; YT #7): web primary
  `video game official trailer gameplay Grand Theft Auto VI gameplay screenshot`, supporting
  `… GTA VI PlayStation 5 gameplay`, fallback `… GTA VI Xbox Series X gameplay`; YT primary
  `video game official trailer gameplay Grand Theft Auto VI PlayStation 5`. These are
  word-salad queries (§D): they stack generic tokens (`video game official trailer gameplay`)
  in front of the entity and name a platform noun, not a visible action.
- **Queries actually used:** `query_used` is empty (`''`). This scene's own queries produced
  **no fresh candidate**: its web pool (`provider_web_candidates.json` key `"6"`) holds **2
  rows that are the same key art** (`reused: true`, `query_used:
  `Rockstar Games Grand Theft Auto Six photograph` — **scene 0's query**, not this scene's),
  i.e. the pool is a reuse of scene 0's image, not a result of this scene's search.
- **Candidates — web:** **0 fresh.** The 2 pool rows are the key art at reuse count 2 = cap.
- **Candidates — YouTube:** **1** attempted: `qq76pQsI1iw`,
  `Grand Theft Auto VI: An Extended Look Coming August 27`, Rockstar Games,
  `metadata_score` 213.0, timestamp 3.0 s, downloaded, quality-check passed.
  Clip-level vision (`vidnets-answer-2a21a23f0ea8face5a55f21c32a5c67f.json`, 13:58 UTC)
  **ACCEPT, final 78**: "frames show official Grand Theft Auto VI material - Rockstar Games
  logo, the GTA VI key art of Lucia Caminos and Jason Duval… Correct game, correct characters,
  correct setting." Per-frame vision (`vidnets-answer-14f3bc3525ca16b1a99744693cbd2dd2.json`,
  14:23 UTC) **ACCEPT, final 74** for `youtube:qq76pQsI1iw:frame:3`: "Authentic Grand Theft
  Auto VI promotional art showing the game's two protagonists Lucia and Jason in Vice
  City/Leonida… Accept as best real media for this abstract platform-announcement beat."
- **Why it degraded, despite vision accepting the clip:** the attempts trace is —
  web discovery empty; YT discovery ok (1 candidate); clip hard gate passed, visual gate
  `visual_rejected`; web cross-lane fallback 2 candidates, both hard-gate passed, visual gate
  `skipped_after_scene_budget`; `same_video_timestamp_frame_fallback` 4 candidates, then
  `scene_budget_exhausted`; frames 1, 2, 4 recorded `youtube_frame_recovery`
  (the recovery path in `media_resolution_coordinator.py:3143-3145` returns **accepted** for
  clean extracted frames); frame 3 recorded `visual_rejected`; then `graphic
  terminal_media_fallback grounded_after_real_media_exhausted`. There is **no**
  `pre_stock_web_rescue` attempt for this scene: the rescue loop skips candidates whose reuse
  count has reached the cap (`media_resolution_coordinator.py:7440-7448`), and the only web
  candidates were the key art at count 2. The final resolution has `visual_decision: null`,
  `alternates` of 1 (`youtube:qq76pQsI1iw:frame:2`, fingerprint `384685f4…`), and status
  degraded. **Two contradictions in the records are unresolved:**
  (1) the attempts record `frame:3` as `visual_rejected` while the standalone vision file
  accepts the same `frame:3` at 74 — same candidate ID, opposite outcomes, no record
  reconciles them (possibly two different frame sets / two vision passes; NOT IN RECORDS);
  (2) the earlier checkpoint `media_plan-ff8f…​.json` shows this scene **accepted** with
  `youtube:qq76pQsI1iw:frame:1`, while the final plan degrades it — the overturning step is
  **NOT IN RECORDS**. Prime suspects, named not proven: the mixed-media fresh/reuse filter
  (`media_resolution_coordinator.py:5267-5284`, `VIDZEO_WEB_IMAGE_REUSE_CAP` default **1** —
  when no fresh candidate exists, only never-used assets survive the filter) and the
  scene-budget gating of finalist consideration (`:4595-4600`, `:4741-4747`
  `skipped_after_scene_budget` — every scene in the job hit `scene_budget_exhausted` after
  its first vision round; the budget floor for an external-agent job is 300 s,
  `:3515-3522`, `:3622`).
- **GTA V verdicts for this scene:** none — GTA V never entered its pool.
- **Status:** degraded, graphic (`graphic:fallback:6`). **Accuracy verdict: the worst miss in
  the job — a vision-accepted real GTA VI clip (78) with vision-accepted frames was available
  and the scene still shipped a graphic.** **What was exhausted:** the web reuse cap on the
  only web image in its pool (key art, 2/2 uses), and — by the records — nothing else; the
  YT frames were verified, not exhausted.

### Scene 7 — accepted, WEB (Vice City shot, reused) — subject game: GTA VI (legacy/next chapter)

- **Narration gist:** "For Rockstar, Grand Theft Auto Six is not just a sequel, it is the next
  chapter of the biggest entertainment series in the world." **Subject game: GTA VI** — the
  "next chapter" is GTA VI; the narration does not mention GTA V at all.
- **Queries planned** (web #8; YT #8): web primary
  `Rockstar Games Grand Theft Auto series games collection`, supporting
  `Rockstar Games Grand Theft Auto legacy`, fallback
  `Rockstar Games Grand Theft Auto VI finale key art`; YT primary
  `Rockstar Games Grand Theft Auto VI series history`.
- **Queries actually used:** `query_used` = `Vice City Miami South Beach neon night photo`
  (**scene 1's query**). Own queries produced **no surviving candidate**
  (`provider_web_candidates.json` has no key `"7"`; attempts: web discovery empty, web
  cross-lane empty, YT discovery empty).
- **Candidates — web:** **0 of its own.** Winner is scene 1's pool alt1, second use (cap 2).
- **Candidates — YouTube:** **0 downloaded.** The worker attempted `EiQEBYDox_k` (title **NOT IN
  RECORDS** — it never downloaded; the log records only bot-gate errors: "Sign in to confirm
  you're not a bot", `youtube-worker-4a75879ea57c5792.log`); a partial file
  `yt_clip_scene_008.candEiQEBYDox_k_ts3.raw.mp4.part` exists in the run dir. No vision
  verdict exists for it.
- **Winner and why:** web alt1 (`web:scn_9d3c414661a07bf7f74b:alt1`, md5 `7d05b5fd0…`, same
  file as scene 3), `fallback_reason: web_image_rescued_before_stock_fallback`, score
  0.8935714285714287. The attempts also record the diversity gate working as designed here:
  `reuse_diversity_gate: adjacent_asset_reuse_blocked, reused_scene_index 6`,
  `semantic_reuse_gate: requirement_hash_mismatch` (×2), and
  `nearest_verified_neighbor_reuse: rejected_semantic_identity_mismatch` (scenes 6 and 5) —
  the YT neighbours were correctly refused as reuses. But the web rescue then took the same
  Vice City shot scene 3 already used (non-adjacent, so allowed; cap now 2/2).
- **Why the others lost:** the only YT candidate never downloaded (bot-gate). The
  `N-xHcvug3WI` frame pool (4 frames) passed the hard gate and failed the visual gate as
  `insufficient_visible_subject_match`. Frame-selection
  (`vidnets-answer-60083e916559da5035e2c63d5ea49ae7.json`, 14:25 UTC) **acceptable: false** —
  "Wrong subject: all four frames are Grand Theft Auto V gameplay (San Andreas / Los Santos),
  not Grand Theft Auto VI… for a scene about GTA VI as the next chapter of the series" —
  **CORRECT rejection** (GTA VI-subject scene; the narration is about VI as the next chapter).
- **Status:** accepted (web). **Accuracy verdict: WRONG MEDIA** — a Vice City still (already
  used for the Lucia/Jason scene) for a closing scene about the series' legacy; nothing in the
  frame depicts a series, a legacy, or a next chapter. **GTA V rejection: CORRECT.**

## (a) Why the web lane returned the same images for different scenes

**Short answer: the web lane only ever found 5 images, all from Pinterest, and the resolver
prefers reusing a top-ranked image over using a fresh alternate — the reuse cap (2) was hit as
the normal outcome, and two scenes had no web pool of their own at all.**

1. **Nearly identical queries?** The *planned* per-scene queries are distinct (see the table).
   But the queries that *produced winners* are nearly identical: only **two distinct
   `query_used` values** cover all five web acceptances —
   `Rockstar Games Grand Theft Auto Six photograph` (scenes 0, 4) and
   `Vice City Miami South Beach neon night photo` (scenes 1, 3, 7).
   Scenes 2, 3, 4, 7 never won on their own queries; they won on scenes 0/1's queries via
   rescue/reuse. So the executed queries are near-identical even though the planned ones are not.
2. **Reuse cap hit as the default:** yes. Web cap is 2
   (`media_resolution_coordinator.py:1215-1237` `_reuse_limit_for_resolution`, web branch
   returns 2; comment at `:3355`: "web 2, YouTube 1"). Both reused images end exactly at 2
   (key art scenes 0+4; Vice City shot scenes 3+7), and 3 of the 5 web acceptances are reuses.
   The cap then actively harmed scene 6: its only web candidates were the key art at 2/2, so
   the rescue loop skipped them (`:7440-7448`) and no rescue was attempted.
   The cap is also asymmetric with the unused alternates: scene 1's pool held two further
   vision-accepted images (alt2 Miami skyline, alt3) that **no scene ever used**, while
   scenes 3 and 7 both took alt1. The records do not log why alt1 was preferred twice over
   the unused alternates (NOT IN RECORDS).
3. **Source diversity — yes, a cause.** All 5 downloaded web images are Pinterest-hosted
   (`br.pinterest.com` / `i.pinimg.com`; `provider_web_candidates.json` domains). The search
   itself returned 60 rows (scene 0, query 2) and 65 rows (scene 1, query 2), but only 1 and
   4 respectively survived to the pool (`downloaded_candidate_count` 8 per scene) — the
   surviving set is a Pinterest monoculture. The first query of each scene returned **0 rows**
   (Yandex/Bing/Flickr `ReadTimeout` ~15 s, DuckDuckGo bot-gate), so the wave only ever
   succeeded on the second query; queries 3–8 in each scene's planned list have no audit rows
   (NOT IN RECORDS whether they ran). No Pexels/Pixabay or other stock source contributed
   anything (the job has no Pexels/Pixabay keys; the stock lane in `queries.json` exists but
   produced no winner).
4. **Structural cause:** wave-1 web discovery returned 0 candidates for **all 8 scenes**;
   the only web candidates came from the cross-lane/late-spool fallback. Five of eight scenes
   therefore had no web pool of their own (`provider_web_candidates.json` keys are only
   `"0"`, `"1"`, `"6"`), and the rescue tier could only hand them images from scenes 0/1's
   pools. With 2 pools feeding 5 scenes and a cap of 2, repetition was arithmetically forced.

## (b) Why scenes 2 and 6 fell to graphics

**Short answer: scene 2's rescue was accepted and then overturned by an unlogged step; scene 6's
only web candidates were cap-exhausted reuses and its vision-accepted YT frames were lost in
the final resolution pass. In neither scene was "all real media" actually exhausted in the
world — it was exhausted *as the coordinator could see it*.**

| | Scene 2 (Leonida Keys) | Scene 6 (platforms) |
|---|---|---|
| Own web wave | 0 rows (discovery empty; cross-lane web fallback empty, 171.8 s) | 0 fresh rows (discovery empty) |
| Own YT discovery (coordinator view) | empty (0) | ok (1: `qq76pQsI1iw`) |
| YT worker reality | downloaded `uInR-MJOVeo` (Leonida Keys, selected:true) — **never reached the coordinator** (0 mentions in storyboard / scene_contracts / media_plan; NOT IN RECORDS why). Recovery tried real Trailer 2 `VQRLujxTm3c` — download timeout 102 s | downloaded `qq76pQsI1iw`, clip vision **ACCEPT 78**, frame:3 **ACCEPT 74** |
| Frame pool | 4 × `N-xHcvug3WI`, all hard-gate `canonical_entity_mismatch` | 4 × `qq76pQsI1iw` frames; 1, 2, 4 `youtube_frame_recovery` (accepted), 3 `visual_rejected` (contradicts the standalone ACCEPT-74 file — unresolved, §Scene 6) |
| Web rescue | **Accepted** scene 1's image (`scene_0002.jpg`) — then graphic anyway; overturning step NOT IN RECORDS. Earlier checkpoint `ff8f…​` had it accepted | **No rescue attempted** — only web candidates were key art at reuse 2/2 = cap, skipped (`media_resolution_coordinator.py:7440-7448`) |
| What was exhausted | Coordinator-visible fresh media (own pools empty; frame pool wrong-game) + the accepted rescue, overturned unlogged | The web reuse cap on the sole pooled image; the YT frames were verified, not exhausted |

Additional facts: scene 2's graphic is `graphic:fallback:2`, entity `Leonida Keys`, template
`kinetic_statement`; scene 6's is `graphic:fallback:6`, entity `Grand Theft Auto VI`. Both
carry `fallback_reason: all_verified_real_media_exhausted` (`app.py:14936` renders that status
for grounded graphics). The label is misleading for both scenes: scene 2 had a downloaded
Leonida Keys clip and an accepted rescue image; scene 6 had a vision-accepted GTA VI clip.
What was truly exhausted in each case was the set of candidates the final resolution pass
was willing to consider — not the real media that existed for the scene.

## (c) YouTube 1/8 vs the 90% gaming target — where the 383 went

**Short answer: only 8 of the 384 were ever attempted. 200 died at metadata rank, 176 rank
survivors were never attempted (only the top-1 per scene is downloaded), 3 of the 8 died on
download, 1 downloaded clip vanished before the coordinator, 2 clips were vision-rejected,
and of the 2 vision-accepted clips only 1 frame was used — the other clip's frames were lost
in the final pass. The exact-title gate and `detect_niche_conflict` run at rank time, but a
spelled-out "Six" in the narration makes both of them blind for the scenes that matter most.**

### The 384, accounted for (log `youtube-worker-4a75879ea57c5792.log`, 13:53 UTC, 8 scenes)

| Stage | Count | Evidence |
|---|---|---|
| Unique search candidates | **384** | `search complete: 384 unique candidate(s)`; fan-out capped 30/48 queries, 6 container pools |
| Rejected at metadata rank | **200** | rank audit: `niche_conflict=96`, `canonical_entity_mismatch=53`, `low_score=25`, `too_short=13`, `copyright_high_risk_media_channel=8`, `streamer_or_reaction_package=5` (sums to 200) |
| Survived rank | 184 | 384 − 200 |
| Attempted for download (top-1 per scene) | **8** | `ranked 8/8 metadata winner(s)`; `youtube_scene_001–007.attempts.json` (1 clip each) + scene 8 `EiQEBYDox_k` (partial file only) |
| Rank survivors never attempted | 176 | 184 − 8. Only the top-1 per scene is downloaded; the 55edcca claim "≥3 options/scene" is **not** what the run did — 1 option per scene was attempted |
| Downloaded | 5 | `N-xHcvug3WI` (S0), `IJQO-LxtwQM` (S1), `uInR-MJOVeo` (S2), `_yl5ELcmugI` (S5), `qq76pQsI1iw` (S6) |
| Download-failed | 3 | `mHaQ_n03qeU` (S3, bot-gate "Sign in to confirm you're not a bot"), `Srn4oirGZIQ` (S4, `transport_timeout`, 86.81 s), `EiQEBYDox_k` (S7, bot-gate; title NOT IN RECORDS) |
| Downloaded but never reached coordinator | 1 | `uInR-MJOVeo` (S2) — 0 mentions in storyboard / scene_contracts / media_plan; no verdict; NOT IN RECORDS why |
| Clip vision REJECT | 2 | `N-xHcvug3WI` 25 (S0 — **WRONG** under the corrected rule, see below); `IJQO-LxtwQM` 30 (S1, 2002 Vice City — correct, stays rejected) |
| Clip vision ACCEPT | 2 | `_yl5ELcmugI` 70 (S5), `qq76pQsI1iw` 78 (S6) |
| Used in the video | **1 frame** | `_yl5ELcmugI:frame:2` (S5). `qq76pQsI1iw` frames were vision-accepted and still lost (scene 6 degraded; earlier checkpoint `ff8f…​` had `frame:1` accepted) |

Notes on the rank buckets: the log does not break down the 96 `niche_conflict` by cause, so
how many of the 96 are same-franchise different-title (GTA V / San Andreas / Vice City 2002)
vs genuine niche drift is **NOT IN RECORDS**. Likewise `gaming_exact_title_missing` does not
appear as a separate bucket — whether those −999s are folded into `canonical_entity_mismatch`
(53) or were never counted is **NOT IN RECORDS**. The earlier 359-candidate run
(`youtube-worker-3f7b9d458a3a25c0.log`, 13:28 UTC) shows the same shape with smaller counts
(`niche_conflict=84`, `canonical_entity_mismatch=49`); its log ends mid-run after
scene 2's clip, with no completion line. The 4-scene recovery run (14:07 UTC, 245 candidates)
tried the two real Trailer 2 clips (`VQRLujxTm3c`, `l6RRdTFuepk`, both `metadata_score` 224)
and lost both to `youtube_clip_timeout_after_61s` (elapsed 102 s / 109 s); it finished 2/4.

### GTA V rejections, split correct vs wrong (corrected scene-relative rule)

| Scene | Subject game | GTA V verdict(s) | Correct? |
|---|---|---|---|
| 0 | **GTA V** (sales / wait) | Clip REJECT 25 (`c49a…​`); frame-selection **ACCEPT**, selected 1 (`9ef9…​`) | **Clip reject WRONG; frame accept RIGHT.** The clip-level verdict contradicts its own reason (it names the GTA V-sales narration, then rejects for being GTA V). This is the one wrong GTA V rejection in the job |
| 1 | GTA VI (new Vice City) | Frames REJECT (`67e7…​`, 14:13 UTC); hard-gate `canonical_entity_mismatch` ×4 | **CORRECT** |
| 2 | GTA VI (Leonida Keys) | Frames REJECT (`661b…​`, 14:14 UTC); hard-gate `canonical_entity_mismatch` ×4 | **CORRECT** |
| 3 | GTA VI (Lucia/Jason) | Frames REJECT (`1436…​`, 14:15 UTC); hard-gate `forbidden_confusable_entity` ×4 | **CORRECT** |
| 4 | GTA VI (trailers) | Frames REJECT, final 14–18 (`282c…​`, `287d…​`, `6d75…​`, `aadb…​`, 14:18–14:19 UTC) | **CORRECT** |
| 5 | GTA VI (delays) | GTA V never entered the pool | — |
| 6 | GTA VI (platforms) | GTA V never entered the pool | — |
| 7 | GTA VI (next chapter) | Frames REJECT (`6008…​`, 14:25 UTC); visual `insufficient_visible_subject_match` ×4 | **CORRECT** |

A second per-frame batch (`2953…​`, `8767…​`, `b0a9…​`, `dca1…​`, 14:26 UTC, final 14,
`entity_conflict: "Grand Theft Auto V"`, reason "requires GTA VI / Leonida imagery under strict
identity") **cannot be attributed to a scene from its contents** — the file names no scene
and no scene's attempts match it unambiguously — so it is counted in neither column above.
It is further evidence that GTA V frames were re-judged under a title-absolute rule late in
the run.

### Why `N-xHcvug3WI` got into the pool at all (code trace, reproduced, not guessed)

The gate and the conflict check both run at **rank time, before download**, in the metadata
scorer (`youtube_pipeline_v2.py:1606-1621`): `_gaming_exact_title_gate` → `v2_reject_reason =
"gaming_exact_title_missing"`, score −999; then `detect_niche_conflict` → `v2_reject_reason =
"niche_conflict"`, score −999. Both should have killed a GTA V clip for a GTA VI scene. They
did not, for scene 0, for two compounding reasons — verified by executing the actual functions
at HEAD `55edcca` against the job's real texts:

1. **The spelled-out "Six" is invisible to the title regexes.**
   `_GTA_VI_RE` (`youtube_pipeline_v2.py:690-693`) matches `grand theft auto (vi|6)`,
   `gta (vi|6)`, `gta six` — it does **not** match `Grand Theft Auto Six` spelled out.
   Reproduced: `_GTA_VI_RE.search("Grand Theft Auto Six")` → `False`.
   `_gta_entry` (`universal_clip_classifier.py:358-384`) likewise returns `""` for scene 0's
   narration (`_gta_entry(scene 0 text)` → `""`), while `_gta_entry("Grand Theft Auto V:
   Official Gameplay Video")` → `"v"`. So `_franchise_entry_conflict` (`:387-398`,
   requires both entries non-empty and different) returns `False`, and
   `detect_niche_conflict("Rockstar Games " + scene 0 text, N title + description)` returns
   `(False, "gaming", "gaming")` — both sides score "gaming", no conflict. The same
   blindness holds for scene 1 (subject "Vice City", text names no GTA entry at all) and
   scene 7 ("Grand Theft Auto Six" spelled out again).
   Note the asymmetry: for scenes whose subject *is* a parseable entry, the conflict check
   does fire — reproduced: scene 3 → `(True, "crime", "gaming")`, scene 4 →
   `(True, "film_tv", "gaming")`, scene 7 with subject "Grand Theft Auto VI" →
   `(True, "history", "gaming")` — but those firings come from the niche taxonomy, not from
   the franchise-entry check, and scene 0/1 never reach them.
2. **The exact-title gate depends on `domain_anchor` being in the scorer's context.**
   Reproduced: `_gaming_exact_title_gate(N, "Rockstar Games", scene 0 text,
   {"domain_anchor": "Grand Theft Auto VI"})` → `True` (would block); with context `{}` or
   `None` → `False` (gate off — `_is_gta_vi_text`, `:714-715`, finds no VI match in subject +
   spelled-out-"Six" text). The empirical outcome proves the gate did not block it in the
   run that selected it: `N-xHcvug3WI` became scene 0's metadata winner with
   `metadata_score` **356.0** and no `v2_reject_reason` appears in any record for it.
   Whether the scorer's context in that run lacked `domain_anchor` (the gate then being off
   exactly as reproduced) or the worker's rank path never calls the scorer gates is
   **NOT IN RECORDS** — the attempts/audit files record the winner and its score, not the
   scorer context. Both candidate explanations are on the table for the fix; the "Six"
   regex hole is proven either way.
3. **The designed GTA V exception could not have admitted it either.**
   `_gta_v_supporting_gameplay` (`:748-772`) requires the context flag
   `gta_vi_supporting_gameplay` **and** a comparison term in the scene
   (`_GTA_V_COMPARISON_TERMS`, `:703-707`: compare/previous/predecessor/prior/earlier/legacy/…).
   Scene 0's text contains none of those terms, and no record shows the flag set for this job
   (the effective config in the job dir carries no gaming/supporting-gameplay flags;
   `media_plan.json` records `gaming_mode: true` only). So the clip entered through the
   regex blindness, not through the designed exception — and the exception as written would
   still not admit it for scene 0, because the scene's own words ("more than ten years
   since… sold more than two hundred million copies") are a legacy narration without a
   comparison keyword.

**Metadata rank did not bury real GTA VI clips — it never got the chance to rank them down.**
The two real official GTA VI trailers that reached the download stage (`VQRLujxTm3c` Trailer 2,
`l6RRdTFuepk` Trailer 2 | PS5 Games, both `metadata_score` 224) were ranked as winners and
lost to **download transport timeouts**, not to rank or vision. The rank stage's real damage
was different: with only the top-1 per scene attempted, a single download failure or a single
wrong winner (GTA V for scene 0, 2002 Vice City for scene 1, a WWE parody for scene 3)
ended the scene's YT lane outright — there was no 2nd or 3rd option attempted despite the
55edcca claim of "≥3 options/scene".

## (d) Query quality — the validator passes queries that retrieve the wrong scene's media

**Short answer: the planned queries are mostly distinct and entity-correct, but the contract
they pass is thin — required terms are the entity name alone, "action" terms are abstract
verbs, viewpoint phrases rarely reach the issued query, and the queries the YT worker
actually attempted were generic 2–4 word phrases, not the planned ones. Three queries
cannot retrieve scene-accurate media by construction (a fictional place has no real photo).**

What the 55edcca "validator" actually is: there is no single `validate_query` function in the
repo. The contract is enforced by (i) `visual_requirement.py` mining `required_terms` (the
canonical entity, plus one mined attribute, e.g. S5 `["Grand Theft Auto VI", "2025"]`, S6
`["Grand Theft Auto VI", "xbox series"]`) and `action_terms` (`:254-311`, `_action_terms`);
(ii) `media_query_strategy.py` `enrich_scene_queries` / `_viewpoint_phrases` (`:96-196`),
which mines viewpoint phrases from narration **and** visual intent (55edcca addition) and
visible nouns from the `_VISIBLE` list; (iii) the brain prompt schema in `vidzeo_brain.py`
(`:219`, `:264`: "All required strings must be nonempty", "Do not borrow another section's
subject"). The per-scene `visual_requirements` in the job show what that contract reduced to:

| Scene | `required_terms` | `action_terms` (mined) | `visual_intent` in requirement |
|---|---|---|---|
| 0 | `["Rockstar Games"]` | `["kept", "fans", "waiting", "sold"]` | `documentary_context` (generic — the rich intent from `queries.json` did not survive into the requirement) |
| 1 | `["Vice City"]` | `["soaked", "reimagining", "filled", "swamps", "neon", "streets"]` | `documentary_context` |
| 2 | `["Leonida Keys"]` | `["show", "swamps", "neon", "city", "detailed", "built"]` | `documentary_context` |
| 3 | `["Lucia Caminos and Jason Duval"]` | `["walking", "prison", "reuniting"]` | `documentary_context` |
| 4 | `["Leonida"]` | `["arrived", "broke", "viewing", "showed"]` | `documentary_context` |
| 5 | `["Grand Theft Auto VI", "2025"]` | `["promised", "pushed", "delayed", "saying", "needed"]` | `documentary_context` |
| 6 | `["Grand Theft Auto VI", "xbox series"]` | `["announced"]` | `documentary_context` |
| 7 | `["Grand Theft Auto VI", "entertainment series"]` | `[]` | `documentary_context` |

All 8 requirements have `strict_identity: true`, `confidence: 0.85`. The "action" terms are
mostly abstract or unfilmable verbs (`kept`, `promised`, `pushed`, `delayed`, `saying`,
`needed`, `arrived`, `broke`) — they satisfy an action *field* while naming nothing a camera
can show. `required_terms` is the entity name alone for scenes 0–4, so any candidate whose
metadata contains the entity word passes — which is exactly how the key art passed scene 0
(`candidate_evidence.matched_terms: ["rockstar", "games"]`, identity_score 100) while
showing nothing about GTA V's sales, Rockstar's studio, or waiting fans.

Concrete validation-passing-but-wrong examples (each row passed the contract and retrieved
the wrong scene's media, or nothing):

1. **Scene 2, web `Leonida Keys Florida Keys aerial photo`** — entity + viewpoint ("aerial")
   + photo. Passes. But the Leonida Keys are fictional; the query can only return real
   Florida Keys travel/stock photos. There is no real-world photograph of the scene's
   subject. Result in the job: 0 surviving candidates. Same structural defect in scene 4's
   `Leonida Grand Theft Auto VI landscape` (returns generic key art/screenshots, not trailer
   moments) and scene 1's `Vice City Miami South Beach neon night photo` (returns a real
   Miami photo for a fictional city — the winner).
2. **Scene 6, web `video game official trailer gameplay Grand Theft Auto VI gameplay
   screenshot`** — entity + "gameplay" + "screenshot". Passes. It is a token stack, not a
   scene description: it names no platform action, no console, no launch moment, and it
   reuses the phrase `video game official trailer gameplay` that is listed as
   `forbidden_subjects` for five other scenes in the same job. Its YT sibling,
   `video game official trailer gameplay Grand Theft Auto VI PlayStation 5`, adds a platform
   noun, not a visible action. The actual winner, `qq76pQsI1iw` ("An Extended Look Coming
   August 27"), is a promo announcement, not launch-platform footage.
3. **Scene 0, web `Rockstar Games logo studio`** (primary) — entity-correct, passes. The
   narration is about GTA VI anticipation and GTA V's 200M sales; a logo/studio photo answers
   neither fact. The winner came from the supporting query (key art), which is also
   off-narration. The plan *did* contain the right queries —
   `Rockstar Games Grand Theft Auto Five photograph` / `… Five documentary` (in the winner's
   `queries` list) — but they have no engine-audit rows, so whether they ran is NOT IN RECORDS.
4. **Scene 3, web `Lucia Caminos and Jason Duval Lucia Caminos Jason Duval GTA VI`** —
   the entity repeated three times, no viewpoint, no action. The visual intent ("Lucia
   Caminos leaving prison gates and embracing Jason Duval") contains the viewpoint
   ("prison gates") and the action ("embracing"), but neither word reached the issued query.
   Result: 0 surviving candidates.
5. **Scene 4, `forbidden_subjects` includes `video game official trailer gameplay`** —
   the scene is *about* the trailers, yet the footage phrase for trailers is forbidden for
   it (and for scenes 0, 1, 2, 3, 5). The forbidden list is copied per-row without regard to
   the scene's own subject. The same list also forbids `Leonida Keys` for the Leonida scene
   (scene 5 row) and `Vice City` for the Vice City scene's neighbours — a row-level
   "do not borrow another section's subject" rule applied so bluntly that it forbids the
   scene's own footage vocabulary.
6. **The YT worker did not use the planned queries.** The attempted queries in
   `youtube_scene_00N.attempts.json` are `Rockstar Games studio Grand Theft Auto VI
   announcement gameplay footage` (S0), `Vice City footage` (S1), `Leonida Keys footage`
   (S2), `Lucia Caminos Jason Duval footage` (S3) — generic 2–4 word phrases, none verbatim
   from `queries.json`. `retrieval_positive_terms` and `retrieval_negative_terms` are `[]`
   on all 16 query rows, and `youtube_query_intents` are `[]` throughout, so nothing
   downstream could steer the worker back to the specific queries.
7. **Distinctness, measured:** the 8 web primaries are all different strings, and the 8 YT
   primaries are all different strings — the *plan* is distinct. But the *executed* outcome
   collapses to 2 web queries (see §A) and 1 YT clip per scene, so distinctness of the plan
   did not survive execution. The validator checks the plan's fields, not the executed
   queries or the retrieved candidates' scene fit.

## (e) Vision verdicts — no real GTA VI candidate was wrongly rejected by vision

**Short answer: confirmed, with the evidence checked verdict-by-verdict. Every real GTA VI
candidate vision saw, it accepted. Every rejection of real GTA VI footage happened before
vision (download failure) or after vision (coordinator loss), not by vision. The one
wrongful rejection in the job is the GTA V clip for scene 0 — wrong under the corrected
scene-relative rule, not a GTA VI wrongful rejection.**

| Real GTA VI candidate | Vision verdict(s) | Outcome |
|---|---|---|
| `_yl5ELcmugI` clip (S5) | ACCEPT 70 (`3535…​`, 13:58 UTC): "Correct game and setting" | Frame:2 used (ACCEPT 72, `cdd2…​`) — the job's only YT win |
| `_yl5ELcmugI:frame:4` (PRE-LOAD NOW card) | Frame-selection: "dominated by a large PRE-LOAD NOW promotional text… not usable as documentary footage" (`b70c…​`) | Correctly excluded; coordinator recorded `explicit_scene_conflict` |
| `qq76pQsI1iw` clip (S6) | ACCEPT 78 (`2a21…​`, 13:58 UTC): "Correct game, correct characters, correct setting" | **Lost downstream** — scene degraded despite acceptance (see §B / scene 6) |
| `qq76pQsI1iw:frame:3` (S6) | ACCEPT 74 (`14f3…​`, 14:23 UTC): "Accept as best real media for this abstract platform-announcement beat" | **Lost downstream**; attempts record the same candidate as `visual_rejected` (unresolved contradiction, §Scene 6) |
| `VQRLujxTm3c` Trailer 2 (S2 recovery) | **No verdict** — never downloaded (transport timeout) | Lost before vision |
| `l6RRdTFuepk` Trailer 2 \| PS5 Games (S3 recovery) | **No verdict** — never downloaded (transport timeout) | Lost before vision |
| `Srn4oirGZIQ` Leonida showcase (S4) | **No verdict** — never downloaded (transport timeout) | Lost before vision |
| `uInR-MJOVeo` Leonida Keys (S2) | **No verdict** — downloaded, but never reached the coordinator; no verdict file, no ledger entry | Lost before vision; whether vision would have accepted it is NOT IN RECORDS (fan channel "GTA6", "Everything Confirmed" — likely commentary/explainer packaging, watermark 0.9; cannot claim it would have passed) |
| `IJQO-LxtwQM` 2002 Vice City (S1) | REJECT 30 (`cbc4…​`): 2002 game, Tommy subtitles, watermark | Correct; stays rejected per user order |
| `mHaQ_n03qeU` WWE parody (S3) | **No verdict** — never downloaded (bot-gate) | Lost before vision; title/description alone mark it as wrong (WWE 2K26) |

Two further checks:

- **The web "accepts" are not vision judgments.** All five accepted web images carry the
  identical deterministic verdict (identity 100 / action 100 / context 55 / quality 85 /
  final 55.0, `source: web_image_resolver`, `verification_status: deterministic_web_image`
  or `pending_visual_verification`). The scores do not vary between images, so they cannot
  be pixel-level verdicts; they are metadata resolver scores stamped as accepted. No web
  candidate was ever vision-rejected in this job — the web lane's failure mode is not
  wrongful rejection but **non-arrival** (0-row waves) and **reuse**.
- **The `N-xHcvug3WI` clip verdict is the job's one wrongful rejection, and it is a GTA V
  rejection, not a GTA VI one.** Its reason text is self-contradictory under the corrected
  rule (it names the GTA V-sales narration, then rejects for being GTA V), and the later
  frame-selection pass (`9ef9…​`, acceptable:true, selected 1) judged the same footage
  correctly for scene 0. Vision was inconsistent with itself across passes for this clip;
  the coordinator kept the earlier, wrong verdict and discarded the later, right one
  (the accept has no consumer in scene 0's records).

## What is NOT proven by the records (do not treat as fact)

1. Whether the web wave issued planned queries 3–8 per scene (only queries 1–2 have engine
   audits on the winning candidates).
2. Why `uInR-MJOVeo` never reached the coordinator (no rejection entry exists).
3. What overturned scene 2's accepted rescue and scene 6's accepted `qq76pQsI1iw:frame:1`
   between checkpoint `ff8f…​` and the final plan (no logged gate between the accept and the
   graphic in either scene's final attempts).
4. Why scene 6's `frame:3` is `visual_rejected` in the attempts while the standalone vision
   file accepts it at 74 (same candidate ID, opposite outcomes).
5. Why scenes 3 and 7 both took scene 1's alt1 while alt2/alt3 (also vision-accepted) were
   never used.
6. Whether the YT scorer's context in the 384-run contained `domain_anchor` (decides whether
   the exact-title gate was off by context or never called).
7. The composition of the 96 `niche_conflict` rank rejects (no per-candidate breakdown in the log).
8. The title of `EiQEBYDox_k` (scene 7's YT candidate — never downloaded).
9. Which scene the 14:26 UTC per-frame `N-xHcvug3WI` verdict batch belongs to (the files name
   no scene).
10. Whether the 359-run and 384-run worker logs are attempt 1 vs attempt 2 of the job or
    re-runs of the same attempt (the logs carry no run IDs; the attempts files live only in
    run-0002).

## Root causes, one per question

- **(a) Web repetition:** only 2 web pools (scenes 0, 1) fed 5 scenes through rescue/reuse,
  all Pinterest, with the cap of 2 hit as the normal outcome — because wave 1 returned 0 rows
  for every scene and 5 of 8 scenes never got a pool of their own.
- **(b) Scenes 2 and 6:** scene 2's rescue was accepted then overturned by an unlogged step
  (earlier checkpoint proves it was accepted); scene 6's only web candidates were cap-spent
  reuses while its vision-accepted YT frames were lost in the final pass. "Exhausted" labels
  the coordinator's visible set, not the real media that existed.
- **(c) YouTube 1/8:** only the top-1 per scene of 384 was ever attempted; the rank gates that
  should have kept GTA V out are blind to the spelled-out "Six" in the narration
  (`_GTA_VI_RE` / `_gta_entry` don't match it), and the exact-title gate's firing depends on
  a `domain_anchor` context the records don't show — while the real Trailer 2 clips that did
  win rank died on download timeouts.
- **(d) Query quality:** the contract the queries pass is entity-name-only required terms plus
  abstract "action" verbs; viewpoint/action mined from the intent rarely reaches the issued
  query, the YT worker used generic 2–4 word queries instead of the plan, and three queries
  (fictional Leonida Keys) cannot retrieve scene-accurate media by construction.
- **(e) Vision:** no real GTA VI candidate was wrongly rejected by vision — both GTA VI clips
  it saw were accepted (70, 78); the losses were download timeouts before vision and
  coordinator losses after vision. The one wrongful rejection is GTA V for scene 0, where
  vision contradicted itself across passes and the coordinator kept the wrong verdict.
