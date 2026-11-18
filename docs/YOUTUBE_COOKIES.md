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

## 6. Keeping the session alive (permanence)

The Nov 17 session died the same afternoon it was exported: valid
at noon, revoked by YouTube server-side by the afternoon. Nothing
in code can stop YouTube revoking a session. What the pipeline
does instead is make a good session last as long as possible,
notice death fast, and make re-export a 30-second file drop.

### 6.1 Export so the session lasts

1. Export from a **dedicated browser profile** that you do not
   keep browsing YouTube in afterwards. Every further YouTube
   visit on the PC can rotate the session token, and the rotation
   kills the exported copy — that is the most likely reason the
   Nov 17 export died within hours.
2. **Use the export promptly.** Drop it in (§6.2) as soon as it is
   exported; an export sitting on the PC for days is already old.
3. Do not export, then keep using YouTube on the PC in the same
   profile and re-export later expecting the first copy to still
   work — the first copy is already dead by then.

### 6.2 Where fresh cookies go (no restart)

- Drop the file into `<repo>/cookies.d/` (e.g.
  `cookies.d/youtube.json`), or replace `<repo>/cookies.json`
  in place, or set `VIDZEO_YT_COOKIE_DIR` / `VIDZEO_YT_COOKIE_FILES`.
- The pool picks a new file up on the next job, no restart.
  A file **overwritten in place** is also noticed: the pool
  re-stats every tracked file on refresh and resets a changed
  account to `unknown`, so the next call re-probes it instead of
  serving the stale verdict from the old export
  (`ConfiguredYouTubeSessions._note_file_change`, verified by
  `tests/test_youtube_pool_reload.py`, 5 passed).
- `chmod 600` the file, as in §2.

### 6.3 What the pipeline does to keep the session alive

- **Encrypted snapshot (durable store).** The live jar is kept in
  an OS-protected snapshot at
  `~/.local/share/vidnets/secure/youtube-session.dpapi` on this VM
  (Windows: the DPAPI path under `%APPDATA%` — see
  `_secure_store_candidates()` in `youtube_session.py`). On Linux
  it is encrypted with a machine-bound key (PBKDF2 over machine-id
  + a per-machine secret file), stored as a JSON container, file
  `0600`, directory `0700`. It survives server restarts and VM
  reboots, and `_load_persisted()` restores it on start. A
  corrupted snapshot fails closed: it is skipped, never crashes
  the server.
- **Write-back of rotated tokens.** The entry point is
  `refresh_encrypted_snapshot(jar) -> bool` in
  `vidnets/server/services/youtube_session.py`: it filters the jar
  to live YouTube/Google cookies, refuses an empty or
  unauthenticated jar (so a good snapshot is never overwritten
  with junk), and writes the encrypted snapshot with `0600`/`0700`
  perms. It is the one-line call the write-back path uses whenever
  cookies are written back or replaced.
  ⚠️ **Not yet wired (Nov 17):** the function exists and is
  unit-tested (`tests/test_youtube_snapshot.py`, 7 passed), but
  no caller invokes it yet — the write-back module that calls it
  after a token rotation is still being built. Until that lands,
  the snapshot is only written by the browser-import path
  (`_verify_and_install`), which is why no snapshot file exists
  on this VM right now even though the operator's `cookies.json`
  is installed: the configured-pool path has never persisted one.
- **Re-verify on file change.** See §6.2 — a replaced file is
  re-probed, not trusted on the old verdict.
- **Alert on death.** A watchdog (`services/youtube_session_watchdog.py`)
  re-verifies the pool on a slow tick (default once per pool
  verify TTL, 1800s) and, when an account flips to `signed_out`,
  logs one redacted line and surfaces a `needs_reexport` signal
  in `/api/status` (`youtube_session.watchdog`), so the operator
  is told to re-export **before** the next job fails, not after.
  It never runs during an active render. Verified by
  `tests/test_youtube_session_watchdog.py` — 7 passed, 1 failing
  (`test_api_status_youtube_session_reflects_pool_state`:
  `needs_reexport` came back `False` in the test; the watchdog
  module itself passes its unit checks — the status-merge case
  is still being fixed).

### 6.4 The honest limit

YouTube can still revoke a session server-side at any time — for
example because the cookies were issued to the operator's PC
browser and are used from this VM (different machine, different
IP), which YouTube can treat as a hijacked session. No code
prevents that. The design above makes a session last as long as
possible, detects death within one watchdog interval, and makes
recovery a 30-second drop of a fresh export (§6.1–6.2). If the
pool reports `signed_out`, only a fresh export restores signed-in
access — restarting the server or re-running the job will not.
