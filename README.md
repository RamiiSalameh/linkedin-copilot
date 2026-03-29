# LinkedIn Copilot

Local-first assistant for **LinkedIn job search**, **matching your CV**, and **guided Easy Apply** flows. Jobs and match scores live in a **SQLite** database on your machine; optional **Ollama** or **OpenAI** powers LLM features.

## Features

- **Web UI** — Search (with optional AI suggestions), Explore mode, and company **Careers** scraping (Greenhouse, Lever, Workday).
- **Job pipeline** — Discover listings, scrape descriptions, score against your profile/resume.
- **Deduplication** — Soft-deleted jobs stay in the database so the same listing is not re-imported on later searches.
- **Safety defaults** — Review-oriented automation; final submit and sensitive actions are gated by configuration.

## Requirements

- **Python 3.11+**
- **Playwright** browsers (`playwright install`)
- **Ollama** (local) and/or **OpenAI** API key, depending on `LLM_PROVIDER`
- Optional: **Tavily** API key for web-augmented search suggestions (see `.env.example`)

## Quick start

```bash
cd "Job Search"
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install
cp .env.example .env
# Edit .env: paths, LLM settings, HEADLESS, ALLOW_FINAL_SUBMIT, etc.
```

Run the web app (from repo root, with `src` on the path or editable install):

```bash
uvicorn linkedin_copilot.web:app --reload --app-dir src
```

Open the URL shown in the terminal (typically `http://127.0.0.1:8000`).

CLI entry point (after install):

```bash
linkedin_copilot --help
```

## Configuration

- **`.env`** — Copy from [`.env.example`](.env.example). Never commit `.env`.
- **`data/profiles/profile.json`** — Your profile template (custom copies stay local if you add other JSON files under `data/profiles/`; only `profile.json` is tracked by default).
- **`data/resumes/`** — Put your CV as plain text; `DEFAULT_RESUME_PATH` in `.env` should point to it.

## Tests

```bash
pytest
```

## Project layout

| Path | Purpose |
|------|--------|
| `src/linkedin_copilot/` | Application code (web, DB, LinkedIn automation, careers, explore, LLM) |
| `tests/` | Pytest suite |
| `linkedin_copilot/README.md` | Longer internal / detailed documentation |
| `docs/` | Design notes and specs |
| `.ai/knowlege-base/` | Maintainer-oriented context (handover, flows) |

## Repository

Source: [github.com/RamiiSalameh/linkedin-copilot](https://github.com/RamiiSalameh/linkedin-copilot)

## Publishing checklist

- [x] `.gitignore` excludes secrets, venv, caches, and local `data/` artifacts
- [x] Remote `origin` on GitHub and `main` pushed
- [ ] Add repository **Topics** on GitHub (e.g. `linkedin`, `job-search`, `fastapi`, `playwright`)
- [ ] In **Settings → Secrets**, do not store `.env`; use GitHub Secrets only for CI if you add workflows later

## Legal & ethics

Automation may conflict with **LinkedIn’s Terms of Service**. Use at your own risk, keep login and captchas human-driven, and respect rate limits and site rules. This project is for personal productivity and learning.

## License

MIT — see [LICENSE](LICENSE).
