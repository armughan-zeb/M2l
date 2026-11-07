# ScriptIT - YouTube Script Generator Pro

A desktop Electron app that writes YouTube scripts using **OpenRouter** (BYOK) and free open-web research (DuckDuckGo + RSS + Reddit JSON). No backend, no monthly bill, no rate-limited paid APIs.

## Quick start (Windows)

1. Install [Node.js 18+](https://nodejs.org)
2. Double-click `start.bat`
3. First run installs dependencies automatically
4. App opens → click the gear → paste your [OpenRouter API key](https://openrouter.ai/keys)
5. Enter a topic → Generate

## Features

- **BYOK** — your OpenRouter key is stored encrypted locally (AES via electron-store). It never leaves your machine except to call OpenRouter.
- **Free research** — DuckDuckGo HTML + Bing HTML + Wikipedia REST API + Hacker News Algolia + RSS feeds (Google News, IGN, Eurogamer, GameSpot, Kotaku, Polygon, ESPN, CoinDesk, NASA, …) + Reddit (JSON → RSS fallback). All parallel with retry, no API keys required.
- **Smart niche detection** — type "Messi" → sports. "Bitcoin" → finance. "Nikola Tesla" → biography. The format is auto-suggested and the right niche RSS feeds are pulled in (e.g. gaming sites for "GTA VI").
- **Format-aware prompts** — Documentary, Faceless Story, News Breakdown, Biography, Explainer, Listicle, Sports Analysis. Each has a different structure, hook, and retention shape.
- **Editorial quality gate** — generation stops when web evidence is too weak; accepted drafts must pass grounding, hook, pacing, length, repetition, and unsupported-inference checks, followed by a factual audit on real model runs.
- **Story-first writing** — the planner chooses a narrative strategy (turning point, contradiction, evidence trail, decisive choice, escalation, and more) and passes the selected channel structure into the actual writer prompt.
- **Model routing** — pick from cheap (DeepSeek V3, Llama 3.1 8B, Gemini Flash), better (Qwen 2.5 72B, Mistral Large), premium (Claude 3.5 Sonnet, GPT-4o, Claude Opus), or long context (Gemini 2.5 Pro).
- **30-minute research cache** — generating on the same topic twice in a row is instant.
- **In-app editor + .md export** — read, copy, or save the script as a Markdown file with title options, description, tags, chapters, and thumbnail text.
- **Durable research corpus** — SQLite stores resumable jobs, source snapshots, claims, provenance, and script runs locally.
- **Fast-then-deep research** — a fast evidence pass returns quickly while a deeper pass continues in the background and checkpoints progress.
- **Failure-safe generation** — provider outages, rate limits, malformed model responses, and weak research return labeled partial or evidence-only output instead of blank cards.
- **Structured free-data discovery** — GDELT, OpenAlex, Crossref, and topic-relevant USGS data run through the resilient provider layer.
- **Publication guard** — empty, partial, failed, or unaudited outputs are blocked from publishing by the validation interface.

## Architecture

```
User → Topic / Format / Model
  ↓
Research Job (checkpointed, resumable)
  - Fast pass → SQLite source/claim corpus
  - Deep pass → deduplication + evidence map
  ↓
Research Agent (parallel, retried, circuit breakers)
  - DuckDuckGo (2 queries)
  - Bing HTML (dated snippets)
  - Wikipedia REST (summary + full plaintext extract)
  - Hacker News Algolia
  - RSS feeds (Google News + niche: gaming/tech/sports/…)
  - Reddit (JSON → old.reddit RSS fallback)
  - GDELT / OpenAlex / Crossref / USGS structured discovery
  ↓
Evidence Ledger + Narrative Planner
  - claim IDs / authority tiers / dates / conflicts
  - hook / stakes / escalation / reversals / payoff
  ↓
OpenRouter (chosen model)
  ↓
Script + fact audit + quality audit + publish validation
  ↓
In-app editor + .md export
```

## Project structure

```
ScriptIT/
├── electron/
│   ├── main.js          Main process, IPC handlers, encrypted store
│   └── preload.js       Context bridge → window.scriptit
├── src/
│   ├── research/
│   │   ├── store.js             SQLite corpus, backups, integrity, retention
│   │   ├── pipeline.js          Fast/deep jobs and resumable checkpoints
│   │   ├── resilience.js        Timeouts, retries, circuit breakers, health
│   │   ├── publishGuard.js      Technical publishing safety checks
│   │   └── sourcePolicies.js    Provider roles and retention policies
│   ├── agents/
│   │   ├── researcher.js       Parallel research orchestration with resilient lanes
│   │   ├── compressor.js       Heuristic context compression
│   │   ├── nicheDetector.js    Keyword-based niche inference
│   │   └── scriptWriter.js     Orchestrates research → prompt → OpenRouter
│   ├── providers/
│   │   ├── duckduckgo.js       Free HTML scrape
│   │   ├── bing.js             Free Bing HTML search (dated snippets)
│   │   ├── wikipedia.js        Wikipedia REST summary + plaintext extract
│   │   ├── hackernews.js       Hacker News Algolia API
│   │   ├── rss.js              Free RSS feeds (rss-parser)
│   │   ├── freeData.js         GDELT, OpenAlex, Crossref, and USGS adapters
│   │   ├── reddit.js           Reddit JSON → old.reddit RSS fallback
│   │   └── openrouter.js       Streaming chat completion
│   ├── cache/
│   │   └── cache.js            In-memory + disk, 30 min TTL
│   ├── prompts/
│   │   └── scriptPrompts.js    Per-format system + structure prompts
│   ├── utils/
│   │   ├── duration.js         Duration → word count
│   │   └── secretStore.js      Encrypted key wrapper
│   ├── config/
│   │   └── models.js           Model registry
│   └── ui/
│       ├── index.html
│       ├── styles.css
│       └── app.js              Renderer logic
├── start.bat                   Windows launcher
└── package.json
```

## Build a Windows installer

```bat
npm run build:win
```

Output goes to `dist/`. NSIS installer is one-click.

## Verification

Run the deterministic production checks:

```bat
npm test
```

Build the Windows application:

```bat
npm run build
```

The build produces `dist/ScriptIT Setup 1.0.0.exe` and `dist/win-unpacked/ScriptIT.exe`. The current build is unsigned because signing credentials are not configured; add a certificate/signing hook before distributing outside the internal team.

The production safety suite covers SQLite integrity, idempotent jobs, claim persistence, fast/deep checkpoints, provider timeout recovery, model outage fallback, partial output, publication blocking, and duplicate URL handling.

## Live test results (verified)

Tested with real network calls. The `test/live.js` script reproduces all of these:

| Step | Latency | Result |
|---|---|---|
| DuckDuckGo HTML scrape | 1.5–2.5s | 0–8 results (intermittent; rate limited) |
| Bing HTML search | 2–8s | 6–8 dated snippets with real URLs |
| Wikipedia REST + extract | 1–2s | Top-2 plaintext article extracts (2k–14k chars) |
| Hacker News Algolia | 0.5–1s | 6 dated stories with points |
| RSS (Google News topic + niche feeds) | 1–8s | 8–16 relevant results per query |
| Reddit | 0.5–2s | JSON 403 → old.reddit RSS fallback returns 6 results |
| Compressor | <10ms | facts + stats + events per topic (from all providers) |
| OpenRouter (Llama 3.1 8B) | 2–4s | Real completion, $0.0000013–0.0000026 per call |
| End-to-end (3-min script) | ~20–35s | Formatted script grounded in real dated evidence |
| Electron app launch | 6s smoke | No errors, window stayed open |

Multi-topic research verified: GTA VI → gaming (release date Nov 19 2026, "Extended Look" Aug 27 on Netflix, characters Jason & Lucia), Bitcoin 2026 → finance/explainer, Lionel Messi → sports, Nikola Tesla → biography, Mars rover → science.

## Known limits & notes

- **DuckDuckGo** HTML endpoint is unofficial and rate-limits aggressively. When it returns 0, Bing + Wikipedia + niche RSS still produce real results, so the pipeline stays useful.
- **Google News article links** are now client-side JS wrappers — the `?oc=5` server-redirect trick is dead, and the page no longer embeds the article body. `article.js` cannot extract those pages, so the researcher skips fetching them and relies on Bing/Wikipedia/HN/niche-RSS for full body text. Google News RSS titles are still collected for dated headline signal.
- **Reddit** JSON requires auth (403 as of 2024+). The provider falls back to the public `old.reddit.com` search RSS, which returns real, dated posts. To re-enable the JSON API: plug in a Reddit token in `src/providers/reddit.js`.
- **Free endpoints are rate-limited**: Bing/DuckDuckGo/Wikipedia and structured public APIs can intermittently abort. Each lane has timeout, retry, backoff, circuit-breaker, and health reporting; failures degrade per-provider and never blank the whole run.

## License

MIT
