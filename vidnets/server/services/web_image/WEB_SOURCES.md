# Web image sources — audit (Nov 10, 2026, web-first)

Production path: `app.py` → `services.web_image_service.resolve_web_images_for_video_fast`
→ `services/web_image/resolver.py:_resolve_scene` → `_discover_source_query`
→ per-source harvester. Every source below is judged against that path:
WIRED means a scene query actually reaches it; DEAD means it is defined
but never yields rows in production, with the reason. No API keys are
used anywhere in this file's scope — only direct retrieval (scrapers,
public JSON APIs, gallery-dl) per operator instruction.

## Universal scraper engines (`universal_scraper.py`, defaults in `DEFAULT_ENGINES`)

| Engine | Status | Reason / evidence |
|---|---|---|
| yandex | WIRED | Default. Live 10/10 (Nov 10). Golden Gate pool: 224 rows. |
| bing | WIRED | Default. Live 10/10. Golden Gate pool: 14 (combined attribution). |
| duckduckgo | WIRED | Default. Live 10/10 after the 15s-budget + retry fix. Golden Gate: 67. |
| flickr | WIRED | Default. Live 10/10. Golden Gate: 49. |
| baidu | DEAD | Hard-blocked by Baidu ("Forbid spider access"). Class kept; reachable only via `VIDZEO_UNIVERSAL_SCRAPER_ENGINES` override. |
| openverse | DEAD | `api.openverse.org` 504s through the egress proxy. Class kept; override only. |

## Resolver fast sources (`_FAST_SOURCES`, defaults in `_enabled_fast_sources`)

| Source | Status | Reason / evidence |
|---|---|---|
| universal_scraper | WIRED | Un-droppable by operator instruction; runs for every web-image query. |
| yandex (api) | WIRED | `YandexSource` HTML scraper; circuit breaker trips on robot-check. |
| bing_hybrid | WIRED | `hybrid_search.search_bing`. |
| ddg_hybrid | WIRED | `hybrid_search.search_ddg`. Added when no explicit legacy setting. |
| flickr_api | WIRED | Flickr REST API (`flickr_api_source.search_flickr_api`), no scraping. |
| wikimedia | WIRED | `WikimediaSource` + rights enrichment. Golden Gate: 10. |
| library_of_congress | WIRED | **Wired Nov 10, 2026 (this audit).** Public JSON API, no key, trust 0.98, rights-documented. Was defined in `services/sources/loc_source.py` but never dispatched. Live-verified: `locomotive` → 20 rows. Recall varies by query (`railroad` → 0). |
| primary_archive | WIRED | Topic → official-domain `site:` query (`nasa.gov`, `si.edu`, `archives.gov`, `fbi.gov`, …), run through the hybrid lanes. |
| google_images | WIRED but dead on this egress | In the defaults, but Google serves a JS bootstrap shell to non-JS clients → 0 rows (Golden Gate: 0). The 10-min circuit breaker bounds the cost to ~1 failed request per window. Kept wired; reachable via `VIDZEO_WEB_ONLY_PROVIDER=google_images`. |
| brave_images | DEAD | Needs `BRAVE_SEARCH_API_KEY` — an API key; none supplied, none allowed. `is_available()` is False without it. |
| searxng_images | DEAD by default | Needs a configured SearXNG instance (`VIDZEO_SEARXNG_ENABLE` / `VIDZEO_SEARXNG_URLS`); none configured. |
| openverse (api) | DEAD | Demoted from defaults Nov 10 (504s). Reachable via legacy switch / only-provider. |
| getty_bing / getty_ddg | DEAD | Demoted from defaults Nov 10: watermarked editorial frames, 1 row in the BNSF pool's first 51 candidates, vision usually rejects. Reachable via legacy / only-provider. |

## Resolver slow sources (`_SLOW_SOURCES`)

Run only when the fast lanes return no high-relevance candidates (and, in
fast mode, only for a semantically-locked final query). gallery-dl 1.32.19
is installed, so the `gdl` lanes can run.

| Source | Status | Reason / evidence |
|---|---|---|
| pinterest (gdl) | WIRED | Slow lane. |
| flickr (gdl) | WIRED | Slow lane. |
| reddit (api + gdl) | DEAD by default | Gated by `VIDZEO_WEB_ENABLE_REDDIT` (default 0). Registered for diagnostics. |
| 500px | DEAD | Listed in `_GALLERYDL_SOURCES` but deliberately undispatched: gallery-dl's 500px search needs a logged-in `x-500px-access-token-prod` cookie and returns no URLs without it (verified live Nov 10, 2026: 40 s, 0 URLs). Wiring it in would burn slow-harvest budget for nothing. |

## Standalone API harvesters (`services/sources/`)

| Source | Status | Reason / evidence |
|---|---|---|
| DuckDuckGoSource (`duckduckgo`, api) | DEAD / orphaned | Imported and handled in `_discover_api_urls`, but no source entry dispatches it. DDG coverage already comes from the universal engine + `ddg_hybrid`; wiring it in would triple DDG. Left as-is. |
| GoogleCSESource | DEAD / orphaned | Needs a Google CSE API key + CX. Never imported. |
| OpenverseSource | WIRED | Via the openverse api lane (demoted, see above). |
| RedditSource | WIRED | Via the reddit slow lanes (disabled by default, see above). |
| LibraryOfCongressSource | WIRED | Via the `library_of_congress` api lane (Nov 10, 2026). |

## Stock (Pexels / Pixabay — `services/real_media_service.py`)

| Source | Status | Reason / evidence |
|---|---|---|
| Pexels | DEAD | Needs `PEXELS_API_KEY`; not present in the runtime `.env` (verified: 0 matches). No API keys per operator instruction. The stock lane fails fast without it. |
| Pixabay | DEAD | Needs `PIXABAY_API_KEY`; same as Pexels. |

## Other

| Item | Status | Reason |
|---|---|---|
| `services/sources/sources.yaml` | DEAD / orphaned | Niche waterfall registry (cricket, reddit, wikimedia, …). No Python code reads it. Left in place; not a retrieval path. |
| `services/web_image/source_broker.py` | WIRED | Source catalog, fallback layers, per-job health breaker, telemetry — used by the resolver on every job. |
| `services/web_image/hybrid_search.py` | WIRED | `search_bing` / `search_ddg` / `download_bulk` for the hybrid lanes. |
| `services/web_image/primary_archives.py` | WIRED | See `primary_archive` above. |
| `services/web_image/flickr_api_source.py` | WIRED | See `flickr_api` above. |

## Defaults after this audit

Fast (per scene query): universal_scraper (yandex/bing/ddg/flickr) +
yandex + bing_hybrid + ddg_hybrid + flickr_api + wikimedia +
library_of_congress + primary_archive + google_images (dead on this
egress, circuit-broken). Slow (only when fast lanes fail): pinterest +
flickr via gallery-dl. Everything else is documented dead above with its
reason; nothing was removed except the 500px non-dispatch, which was
already the behavior — the audit only wrote the reason down.
