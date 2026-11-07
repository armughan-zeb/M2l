# Mixed Media production workflow

The production path is one storyboard and one coordinator. It keeps the
YouTube clip lane intact, adds the complete web-image waterfall, and records
every provider/model decision in the final manifest.

1. **Plan** — OpenRouter scene brain uses the configured primary model and an
   ordered fallback chain. It emits scene-local subjects, actions, search
   variants, and diversity hints.
2. **Contract** — each scene receives a visual requirement, timing window,
   media-type policy, and a unique-asset preference. The mixed planner spreads
   motion across the opening and breaks long same-source runs.
3. **Retrieve** — YouTube and web run as parallel primary lanes. Web sources
   are grouped into fast scrapers/APIs, rights-friendly and HTML rescue,
   editorial site rescue, and slow gallery-dl last resort. Providers have
   cache coalescing, circuit breakers, bounded concurrency, and telemetry. A
   definitive Yandex robot challenge opens the per-job circuit immediately so
   the remaining providers continue without repeated blocked requests.
4. **Verify** — local decode/safety/relevance checks run first. OpenRouter
   vision compares bounded finalists with its own ordered multimodal fallback
   chain. A model outage is recorded as degraded availability; a clear wrong
   entity, unsafe image, subtitle-heavy clip, or watermark remains a hard veto.
5. **Recover** — rejected or empty scenes retry the opposite real-media lane,
   then the next web tier, then the narrowly scoped stock last resort when the
   job allows it. Neutral cards are never used to hide a retrieval failure.
6. **Render** — the coordinator rejects duplicate paths/content hashes and
   avoids adjacent same-master clips when alternatives exist. The final
   manifest includes source maps, fallback layers, model routing, resolution
   status, and asset fingerprints for auditing.

The normalized defaults are `pipeline_v2_mode=on`,
`cross_source_fallback=true`, `web_collect_all_sources=true`, and
`unique_web_assets_required=true`. Operators can override provider/model lists
with `VIDZEO_WEB_ONLY_PROVIDER`, `VIDZEO_SCENE_BRAIN_FALLBACK_CHAIN`, and
`VIDZEO_QWEN_VISION_FALLBACK_MODELS`.
