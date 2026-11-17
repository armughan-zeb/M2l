# YouTube Cookies: Adding Accounts, Rotation & Health

Operator doc for the multi-account cookie pool
(`vidnets/server/services/youtube_session.py`,
`vidnets/server/services/youtube_pipeline_v2.py`).
For how to **export** cookies from a browser, see
[`MUSE_COOKIE_GUIDE.md`](MUSE_COOKIE_GUIDE.md) §3 — this doc covers
what happens after the export: where to put files, what to set, how
the pool picks accounts, and how to verify it works.

> **Security.** Cookie files hold live YouTube sign-ins. They are
> gitignored and must stay out of git, logs, chat, and screenshots.
> Never paste cookie *values* anywhere — only file names and counts.
> The broker never logs a cookie value; health reports carry file
> names, statuses, and cookie counts only.

## 1. Where the pool looks

One shared pool feeds **every** YouTube request in the server —
search, probe, clip download, transcript fetch (yt-dlp captions,
youtube-transcript-api, and the fallback extractor), and chapter
metadata. There is no per-site cookie loading; every site resolves
its session through `_get_authenticated_youtube_session()`, which
reads from this pool.

| Source | How to set it | Notes |
| --- | --- | --- |
| `VIDZEO_YT_COOKIE_FILES` | List of files, separated by `:` (Linux) or `,` | e.g. `/home/hatch/workspace/M2l/cookies.json:/home/hatch/workspace/M2l/acct2.txt`. Max 16 files. |
| `VIDZEO_YT_COOKIE_DIR` | A directory path | Every `*.json` / `*.txt` file in it auto-registers, sorted by name. |
| `<repo>/cookies.d/` | Just create the folder | Same auto-register behaviour as `VIDZEO_YT_COOKIE_DIR`, no env needed. |
| `VIDZEO_YT_COOKIES_FILE` (singular, legacy) | One file | Still honoured by the browser-session broker as a fallback. Prefer the list/dir above. |

The runtime `.env` (`~/workspace/vidnets-runtime/.env`, **not** in
git) currently sets:

```bash
VIDZEO_YT_COOKIE_FILES=/home/hatch/workspace/M2l/cookies.json
```

## 2. How to add another account (exact steps)

1. **Export** the new account's cookies while signed in to that
   account on YouTube — JSON via Cookie-Editor, or Netscape via
   "Get cookies.txt LOCALLY" (see `MUSE_COOKIE_GUIDE.md` §3).
   Export from a dedicated browser profile and do not keep browsing
   YouTube in it afterwards: Google rotates the session token and
   the export goes stale.
2. **Drop the file** into the cookies directory, or add its path to
   the list:
   - Directory (recommended): copy it to the `VIDZEO_YT_COOKIE_DIR`
     folder (or `<repo>/cookies.d/`), e.g. `acct2-youtube.json`.
   - List: append `:<full path>` to `VIDZEO_YT_COOKIE_FILES` in the
     runtime `.env`.
3. **Permissions**: `chmod 600 <file>` (owner-only, like the existing
   `cookies.json`).
4. **No restart needed for the next job.** The pool is rebuilt
   whenever the file list or directory listing changes, so the next
   job picks the new account up. A job that is already running keeps
   the pool it started with.
5. **Verify** (see §4). A file that fails verification is skipped,
   never fatal.

To **remove** an account, delete its file (or remove it from the
list). It drops out of the pool on the next rebuild.

## 3. Rotation & health rules

Each configured file is one account. On first use the pool loads it
and probes YouTube (`/feed/you`) to confirm YouTube itself reports
the session signed in — cookie names alone never count as verified.

| Status | Meaning | Pool behaviour |
| --- | --- | --- |
| `verified` | YouTube confirmed signed-in | Eligible for search and downloads. Re-probed at most once per `VIDZEO_YT_POOL_VERIFY_TTL_SECONDS` (default 1800s). |
| `signed_out` | No auth cookies, or YouTube reported signed-out | Skipped. Re-checked at most once per verify TTL, so a stale export does not burn the search wall on every call. |
| `throttled` | A download/search failed with 429 / "too many requests" / bot-check on this account | Skipped until `VIDZEO_YT_POOL_THROTTLE_COOLDOWN_SECONDS` (default 900s) elapses, then retried. |
| `unverified` | The probe was inconclusive (network error, non-200) | Skipped briefly (`VIDZEO_YT_POOL_UNVERIFIED_RETRY_SECONDS`, default 120s), then retried. |
| `load_error` | File missing, unreadable, or not Netscape/JSON | Skipped; never kills the pool. |
| `unknown` | Not checked yet | Checked on first use. |

**Rotation.** Searches and downloads take the next healthy account
round-robin (`next_healthy()`), so work spreads across accounts
instead of hammering account 1. If a download fails with
"sign in to confirm your age" / "login required", the downloader
retries once with the next verified account, then gives up that
clip (the scene falls back to web media per the pipeline rules).

**Graceful degradation.** One bad file never kills the pool, and
zero healthy accounts never kills the job: every call site falls
back to public (anonymous) access. Set
`VIDZEO_YT_REQUIRE_SESSION=1` only if you want a hard failure
instead of that fallback.

**Stale cookies (current state, Nov 17).** The installed
`cookies.json` loads (16 cookies, 12 auth) but YouTube reports the
session signed out, so the pool marks it `signed_out` and the
pipeline runs on public access. The wiring is correct — fresh
cookies from the operator are what will make it signed-in. Do not
claim an account is authenticated until a live check shows
`verified` (see §4).

## 4. How to verify it works

From the repo, with the repo venv (prints counts and statuses only,
never values):

```bash
cd vidnets
.venv/bin/python -c "
import sys; sys.path.insert(0, 'server')
from services.youtube_session import get_configured_pool
pool = get_configured_pool()
print('accounts:', [p.name for p in pool.paths])
index, jar = pool.next_healthy()
print('healthy account:', index + 1 if jar else None,
      '| cookies:', sum(1 for _ in jar) if jar else 0)
for row in pool.health_snapshot():
    print(row['account'], row['file'], row['status'],
          'cookies=%d auth=%d' % (row['cookie_count'], row['auth_cookie_count']))
"
```

Expected with the current stale file: one account, status
`signed_out`, `healthy account: None` — and the pipeline still runs
(public fallback). Expected with fresh cookies: status `verified`
and a healthy account number.

Unit tests (no network, synthetic cookies only):

```bash
cd vidnets
VIDZEO_RENDER_GATE=off .venv/bin/python -m pytest \
  tests/test_youtube_cookie_pool.py tests/test_youtube_cookie_wiring.py -q
```

## 5. What is deliberately still anonymous

- The pool-search **timeout retry**: if an authenticated search
  fan-out times out with zero candidates, the pipeline retries once
  anonymously — an intentional escape from auth-side throttling,
  not a wiring gap (`_search_container_pools`).
- **Zero healthy accounts**: public access, by design (see §3).
- Web-image search and stock sources never use YouTube cookies.
