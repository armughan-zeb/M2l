# Generation cost controls

The normal OpenRouter pipeline uses Qwen 3.5 Flash for text and vision. The cost changes below preserve approved Remotion components, variants, niche grammar, caption/audio requirements, media verification, probe resolution and output headroom.

## Avoid repeated work

- Scene planning batches two scenes per request. A compact film-wide opportunity index and exact local evidence replace duplicated board detail. Full narrative context, the live template catalog and response schema remain available.
- Static director rules and niche grammar are sent once per request rather than repeated in several sections.
- Structured text responses are reused within the same project only after schema validation. Changed prompts, schemas, models, accounts or creative controls invalidate reuse. A small bounded memory cache covers disk-cache trouble. New projects receive independent direction.
- Vision decisions are tied to ordered media-file content hashes, full scene/system context, model, account, probe settings and implementation policy. Changed footage or narration is analyzed again. Only complete validated verdicts qualify. Live health probes are never served from the decision cache, and account changes receive their own health check.
- Identical concurrent requests share their result. Existing waiters also share a failed owner's exception, preventing duplicate retries during an outage; a new explicit request can retry. Failed or malformed answers are not stored as successful results.
- JSON-only object/list inputs lose formatting whitespace. Embedded scripts, quotes, captions, arrays and values remain intact. Ambiguous duplicate-key, non-finite or ordinary prose inputs remain unchanged.

`VIDZEO_RESPONSE_CACHE=1` enables structured text reuse by default. Set it to `0` to request fresh structured text responses. This setting does not disable factual vision reuse or health probes.

## What cost receipts show

Each generation keeps its provider ledger in `logs/openrouter-usage.jsonl` and reuse events in `logs/cost-optimizations.jsonl`. The `cost-<attempt>.json` receipt includes:

- Provider-reported dollars and unpriced/late responses.
- Input and output tokens by operation.
- Provider cached-input/cache-write tokens and reasoning tokens when returned.
- Local reuse counts separately for text and vision. A local reuse is an avoided call, not a paid provider-cache hit.

No estimated avoided dollar figure is substituted for real billing. Provider-cache token counts are informational; charges remain the provider-reported amounts.

## Local evidence, 2026-10-11

- The same two-scene request uses 48,818 → 27,638 UTF-8 message bytes (**43.39% smaller**). Complete serialized HTTP JSON is 60,550 → 39,100 bytes (**35.43% smaller**). The schema, model and 4,800-token output cap are unchanged.
- Six identical concurrent checks produce one mocked provider call. During a shared failure, six waiters also produce one call; a fresh successful retry produces the second call.
- **536 regression checks passed**, covering costs, caches, API routes, media verification, editing variety, layout, captions, audio cues and render handoff.

These are request-size and mocked-call measurements, not paid savings percentages or a new video acceptance result. Actual dollars vary with script length, candidate quality, fresh media and cache reuse. See `artifacts/director_cost_20261011/` for the preserved logs and measurements.

OpenRouter prompt caching is provider/model dependent; local verified reuse does not depend on an advertised provider cache discount. [Official caching documentation](https://openrouter.ai/docs/guides/best-practices/prompt-caching).
