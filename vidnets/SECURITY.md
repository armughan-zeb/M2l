# Vidnets Studio Security Notes

Vidnets Studio is a SaaS desktop product. Treat every installed desktop app as user-controlled: users can inspect local files, network calls, bundled resources, and renderer code.

## Non-Negotiables

- Never ship `.env` or any Supabase service-role key in a distributable build.
- Rotate any credential that was pasted into chat, committed, zipped, logged, or shared during testing.
- Keep plan activation and admin-only writes on a trusted backend or Supabase service-role environment that users cannot access.
- The Electron renderer is not trusted. Backend routes must enforce auth and active-plan access server-side.
- Admin direct login is only for local operator builds. Do not expose it on a public host.

## Packaging Rules

- Production builds must exclude `.env`, logs, caches, temp files, generated media, tests, source zips, and debug scripts.
- `node_modules` should stay inside the app archive unless a native dependency explicitly requires unpacking.
- Generated output belongs in user data or a selected output directory, not in the install directory.

## Runtime Rules

- Flask must bind to `127.0.0.1` unless intentionally deployed behind a trusted reverse proxy.
- Admin APIs reject non-local requests and untrusted browser origins.
- Protected generation, upload, queue, gallery, and project endpoints must require active plan access.
- `/output` and `/temp` serving is local-only and path traversal guarded.

## Supabase Rules

- The anon key may be public, but RLS and RPC checks must assume hostile clients.
- The service-role key is a server secret. If a desktop build needs service-role behavior, move that behavior to a hosted backend before distribution.
- Plan status must be derived from database state, not renderer state.
