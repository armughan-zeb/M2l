# Vidnets Studio

Vidnets Studio is a local-first Electron application for producing narrated videos with scene-aware media retrieval, YouTube clip selection, web and stock fallbacks, captions, and FFmpeg rendering.

## Requirements

- Windows 10 or 11
- Node.js 20 LTS
- Python 3.11
- FFmpeg and FFprobe
- API credentials configured through the application or a local `.env`

Never commit `.env`, service-role credentials, desktop sessions, queues, generated media, or the private admin panel.

## Development

```powershell
npm ci
python -m pip install -r server/requirements.txt
Copy-Item .env.example .env
npm start
```

The desktop process starts the local Flask backend automatically. Runtime files are written to ignored `temp/`, `cache/`, `logs/`, and `output/` directories.

## Validation

```powershell
npm run security:scan
npm audit --omit=dev
python -m compileall -q server
python -m pytest -q
```

## Production Source

Create a fresh GitHub-ready repository outside the development checkout:

```powershell
python scripts/create-github-source.py
```

The default destination is the sibling directory `Vidzeo-App-Production`. The exporter uses an allowlist, removes private and generated files, checks exported text against local secret values without printing them, runs structural validation, and initializes a new `main` Git repository.

## Structure

- `src/`: Electron renderer and product UI
- `server/`: Flask backend and media pipeline
- `server/services/`: retrieval, scoring, audio, rendering, and auth services
- `tests/`: pipeline and API contract tests
- `supabase/migrations/`: versioned database schema and RLS changes
- `scripts/`: reproducible build, security, and export tooling
- `.github/`: CI and dependency update configuration

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [SECURITY.md](SECURITY.md) for system boundaries and production rules.
