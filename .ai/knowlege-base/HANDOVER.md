# LinkedIn Copilot - Agent Handover Documentation

## Project Overview

**LinkedIn Copilot** is a local Python browser automation application for LinkedIn job search and application tracking. It scrapes job listings, matches them against a user's CV/profile using LLM analysis, and provides a web UI to manage the job search pipeline.

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, Uvicorn
- **Browser Automation**: Playwright (async)
- **Database**: SQLite
- **LLM Providers**: Ollama (local) or OpenAI API (cloud)
- **Frontend**: Jinja2 templates, vanilla JavaScript, CSS
- **Data Validation**: Pydantic

## Project Structure

```
Job Search/
├── src/linkedin_copilot/
│   ├── cli.py                 # Command-line interface
│   ├── config.py              # Settings (env + YAML)
│   ├── db.py                  # SQLite database operations
│   ├── models.py              # Pydantic models (JobRecord, JobStatus, Company, etc.)
│   ├── web.py                 # FastAPI application (main entry point)
│   ├── logging_setup.py       # Loguru configuration
│   ├── utils.py               # Helper utilities
│   ├── llm/                   # LLM provider abstraction
│   ├── linkedin/
│   │   ├── auth.py            # LinkedIn session management
│   │   ├── search.py          # Job search scraping
│   │   ├── extract.py         # Job detail/logo scraping
│   │   ├── apply.py           # Application automation (legacy)
│   │   ├── apply_session.py   # In-app application engine
│   │   ├── form_detector.py   # Form field detection
│   │   ├── forms.py           # Form field utilities
│   │   ├── safety.py          # Safety guards
│   │   └── google_jobs.py     # Alternative job source
│   ├── careers/               # Career site scraping (NEW)
│   │   ├── __init__.py        # Module exports
│   │   ├── base.py            # Abstract JobSourceBase class
│   │   ├── greenhouse.py      # Greenhouse ATS scraper
│   │   ├── lever.py           # Lever ATS scraper
│   │   ├── workday.py         # Workday ATS scraper (browser-based)
│   │   ├── detector.py        # ATS auto-detection from URL
│   │   └── registry.py        # Scraper registry
│   ├── scoring/
│   │   └── matcher.py         # LLM-based job matching
│   ├── explore/
│   │   ├── __init__.py        # Module exports
│   │   ├── engine.py          # Exploration orchestrator
│   │   ├── strategies.py      # Query generation strategies
│   │   └── intelligence.py    # Search effectiveness analysis
│   ├── templates/
│   │   ├── base.html          # Base template (side menu, layout, favicon)
│   │   ├── home.html          # Legacy; see getting_started.html
│   │   ├── getting_started.html  # Getting started / How it works guide
│   │   ├── index.html         # Search page (unified search tabs + Careers)
│   │   ├── profile.html       # Profile page (session + CV upload)
│   │   ├── jobs.html          # Jobs list with modal + source filter
│   │   └── apply.html         # In-app application page
│   └── static/                # Static assets
├── data/
│   ├── linkedin_copilot.sqlite3  # Main database
│   ├── linkedin_session.json     # Saved LinkedIn cookies
│   ├── logos/                    # Downloaded company logos
│   ├── resumes/                  # User CV files
│   └── profiles/                 # User profile JSON
├── config/
│   └── settings.yaml          # Application settings
├── .env                       # Environment variables
└── tests/                     # Test files
```

## How to Run

```bash
cd "/Users/rami_salameh/Job Search"
source .venv/bin/activate
python -m uvicorn linkedin_copilot.web:app --reload --port 8000
```

Access at: http://127.0.0.1:8000

## Key Features

### 1. Job Search Pipeline
- **Search** → **Scrape Details** → **LLM Match** → **Track Applications**
- Three statuses: `pending_scrape` → `pending_match` → `matched`
- New `applied` status for user-confirmed applications

### 2. Search Types
- **Unified Search Engine** (primary): one query pool with user queries + AI suggestions
- **Suggestion Sources**: CV + applied jobs + search history + optional web snippets
- **Unified Batch API**: `POST /api/search/run-batch` (JSON queries/locations/filters)
- **Autocomplete API**: `GET /api/search/autocomplete` (profile + search history)
- **Explore Mode**: Continuous AI-powered job discovery with learning (unchanged)
- **Anonymous Search**: Supported via filters payload

### 3. Job Matching
- Uses Ollama (`qwen2.5-coder:7b`) or OpenAI (`gpt-4o-mini`)
- Scores jobs 0-100 with recommendations: Apply/Consider/Skip
- Provides detailed match analysis with reasons

### 3a. Search Suggestion APIs (new)
- `GET /api/search/suggestions`: cached AI suggestions for unified search
- `POST /api/search/suggestions/refresh`: force regeneration with variation
- `POST /api/generate-searches`: legacy proxy to unified suggestions endpoint
- `/batch-search-generated`: legacy form endpoint proxies into unified batch runner

### 4. Application Tracking
- "Open on LinkedIn" shows confirmation dialog
- User confirms if they applied → job marked as "Applied"
- Visual green badge in status column

### 5. Jobs Page Pagination & Column Filters
- Server-side pagination with LIMIT/OFFSET queries
- Filters: status, search text, recommendation level, hide applied, **source**
- **Column filters**: Click the funnel icon on Company, **Role/Title**, Location, Status, or Recommendation to open a dropdown; select one or more values (checkboxes), then Apply or Clear filter. Multi-value filters are combined with existing search/status/recommendation.
- Sortable columns: score, company, title, location, dates
- URL state for bookmarkable filtered views

## Recent Changes (This Session)

### 0. Home = Dashboard; Getting Started Guide
The **home page** at `/` is the **pipeline dashboard** (stage counts, next action, top jobs). A separate **Getting started** guide at `/getting-started` explains how the app works.

- **Routing** ([web.py](src/linkedin_copilot/web.py)): `GET /` with no `tab` or invalid `tab` renders the **dashboard** ([dashboard.html](src/linkedin_copilot/templates/dashboard.html)) as the main home. With `?tab=search` or `?tab=careers` (or legacy tabs), renders the Search page (index.html). `GET /getting-started` renders the guide ([getting_started.html](src/linkedin_copilot/templates/getting_started.html)). `GET /dashboard` redirects to `/` (302). `GET /search` redirects to `/?tab=search`.
- **Nav** ([base.html](src/linkedin_copilot/templates/base.html)): "Home" links to `/` (dashboard); active when dashboard is shown. "Getting started" links to `/getting-started`; active when on that page. Search/Careers active only when on Search page with that tab.
- **Getting started content**: Guide badge, "Getting started" title, five steps with icons and left accent bar; CTA "Go to Home" plus Set up profile / Search jobs. Legacy [home.html](src/linkedin_copilot/templates/home.html) remains in repo but is unused; guide uses getting_started.html.
- **Tests**: [test_web_nav.py](tests/test_web_nav.py) — `/` returns dashboard, invalid tab → dashboard, `/getting-started` returns guide, `/dashboard` redirects to `/`. [test_dashboard.py](tests/test_dashboard.py) — dashboard at `/`, redirect at `/dashboard`.

### 0a. Side Menu Navigation
Navigation moved from top horizontal bar to a **left side menu** with three main sections and sub-items:

- **Layout** ([base.html](src/linkedin_copilot/templates/base.html)):
  - Fixed left sidebar (260px) with logo and nav tree; main content to the right.
  - On viewports &lt; 768px, sidebar becomes a drawer toggled by hamburger; overlay and Escape close it.

- **Nav structure**:
  - **Home**: `/` — pipeline dashboard (main landing). **Getting started**: `/getting-started` — how-it-works guide.
  - **Profile**: LinkedIn Connection (`/profile?section=linkedin`), CV / Resume (`/profile?section=cv`). Same page; query param drives active state and optional scroll-to-section.
  - **Search**: Quick Search (`/?tab=quick`), Smart Search (`/?tab=smart`), AI Suggestions (`/?tab=ai`), Explore (`/?tab=explore`), Careers (`/?tab=careers`). Tab is passed as `?tab=`; server validates and passes `search_tab` to the template; client keeps URL in sync via `history.replaceState` when switching tabs.
  - **Jobs**: Jobs List (`/jobs`), Review pulled (`/careers/review`).

- **Backend** ([web.py](src/linkedin_copilot/web.py)):
  - `GET /` with no `tab` or invalid `tab` shows the **dashboard** ([dashboard.html](src/linkedin_copilot/templates/dashboard.html)) as home; with `tab=search`, `tab=careers`, or legacy (quick, smart, ai, explore) renders the Search page ([index.html](src/linkedin_copilot/templates/index.html)) with `search_tab` for the active panel.
  - `GET /getting-started` renders the guide ([getting_started.html](src/linkedin_copilot/templates/getting_started.html)). `GET /dashboard` redirects to `/` (302).
  - `GET /profile` accepts optional `section` (linkedin | cv); template receives `profile_section`.
  - `GET /search` redirects to `/?tab=search` (302) for a canonical Search URL.

- **Deep-linking**: Search sub-pages use `?tab=...`; Profile sub-sections use `?section=linkedin` or `?section=cv`. Profile page has `id="section-linkedin"` and `id="section-cv"` for scroll-to-section on load (hash or query).

- **Apply**: Reached only from the Jobs modal ("Apply with Assistant"); no sidebar entry.

- **Tests**: `tests/test_web_nav.py` — index with `tab` param, invalid tab defaults to quick, `/search` redirect, profile with `section` param.

### 1. Career Sites Job Scraping (NEW)
Complete feature to pull jobs directly from company career sites using a pluggable ATS scraper architecture:

- **New Module** (`src/linkedin_copilot/careers/`):
  - `base.py`: Abstract `JobSourceBase` class with rate limiting, retry logic
  - `greenhouse.py`: Greenhouse ATS scraper using public JSON API
  - `lever.py`: Lever ATS scraper using public JSON API
  - `workday.py`: Workday ATS scraper (browser-based via Playwright)
  - `detector.py`: ATS auto-detection from careers URLs; URL resolution for branded portals
  - `registry.py`: Scraper registry for dynamic dispatch

- **New Models** (`src/linkedin_copilot/models.py`):
  - `JobSource`: Enum (linkedin, greenhouse, lever, workday, custom)
  - `ATSType`: Enum for ATS platform types
  - `Company`: Model for tracked companies with careers URL, ATS type, board token

- **Database Changes** (`src/linkedin_copilot/db.py`):
  - New `companies` table for career site tracking
  - Added `source` column to jobs table (defaults to 'linkedin')
  - Added `company_id` column to link jobs to tracked companies
  - Added `external_job_id` column for non-LinkedIn job IDs
  - CRUD operations for companies

- **API Endpoints** (Career Sites):
  | Method | Endpoint | Description |
  |--------|----------|-------------|
  | GET | `/api/companies` | List tracked companies |
  | POST | `/api/companies` | Add company by URL (auto-detect ATS) |
  | GET | `/api/companies/{id}` | Get single company |
  | DELETE | `/api/companies/{id}` | Remove company |
  | POST | `/api/companies/{id}/toggle` | Enable/disable company |
  | POST | `/api/companies/{id}/scrape` | Scrape single company (optional body: `location_filters`) |
  | POST | `/api/careers/scrape-all` | Scrape all enabled companies (optional body: `location_filters`) |
  | GET | `/api/careers/status` | Get scraping progress |
  | POST | `/api/careers/stop` | Stop current scrape |
  | POST | `/api/careers/validate-url` | Validate careers URL |

- **UI** (`src/linkedin_copilot/templates/index.html`):
  - New "Careers" tab in search page alongside Quick/Smart/AI/Explore
  - Add company form with URL validation and auto-detection
  - Company list with ATS badges (Greenhouse, Lever, Workday)
  - Individual and bulk scrape controls
  - Real-time scraping progress display

- **Jobs Page Updates** (`src/linkedin_copilot/templates/jobs.html`):
  - New "Source" filter dropdown (LinkedIn, Greenhouse, Lever, Workday)
  - Source badges on job cards showing job origin
  - API support for source filtering

- **Supported ATS Platforms**:
  - **Greenhouse**: Public JSON API (`boards-api.greenhouse.io`)
  - **Lever**: Public JSON API (`api.lever.co`)
  - **Workday**: Browser-based scraping (Playwright). Supports:
    - Direct Workday URLs (e.g. `company.wd3.myworkdayjobs.com`)
    - Branded career URLs that redirect to Workday (e.g. `careers.philips.com`). URLs are resolved before detection.
  - Job list: multiple fallback selectors; pagination via "Show more". Job **description** (for rescrape / Process All Pending): `fetch_job_details` uses several tenant selectors plus a JS fallback (largest text block in main) so Philips and similar tenants work.
  - Workday is slower and more fragile than Greenhouse/Lever (tenant-specific page structure, rate limits).
  - **Ashby**: Detected but not yet supported (future)

- **Rate Limiting**:
  - Per-domain rate limiting (configurable, default 10s between requests)
  - Exponential backoff on 429/5xx responses
  - Request retry with jitter

- **Tests**: 65 unit tests in `tests/test_careers.py`:
  - ATS detection and board token extraction
  - URL resolution (`resolve_careers_url`) and Workday validation
  - Company model and serialization
  - Job normalization for each scraper (Greenhouse, Lever, Workday)
  - Workday scraper with mocked Playwright
  - Rate limiter functionality
  - Registry and scraper lookup

### 2. Careers Location Filter (incl. filter at source)
Location-based filtering when pulling jobs from company career sites. When possible, the filter is applied **on the career site** so only matching jobs are fetched (more efficient).

- **Filter semantics**: Optional `location_filters` = list of strings (e.g. `["Israel", "Remote", "Tel Aviv"]`). A job is **included** only if its `location` (case-insensitive) contains **any** of the terms. Empty or missing list = no filter (all jobs).
- **Base layer** (`src/linkedin_copilot/careers/base.py`):
  - `normalize_location_filters(location_filters)` — strips and drops empty terms; returns `None` if empty.
  - `job_matches_location_filter(job_location, location_filters)` — returns True if no filter or job location matches any term.
- **Workday — filter at source** (`src/linkedin_copilot/careers/workday.py`):
  - **URL query params preserved**: When building the job list URL from `company.careers_url`, any query string (e.g. `?locationHierarchy1=...`) is preserved. If the user pastes a Workday URL after selecting a location (e.g. Israel) on the site, that filter is used and only matching jobs are fetched.
  - **In-page location facet**: When `location_filters` is provided and the URL has no location facet param, after loading the page the scraper tries to open the Location facet (e.g. button/link "Location" or "Locations"), select the option matching the first filter term (e.g. "Israel"), wait for the list to refresh, then scrape. If the facet cannot be found or applied (tenant-specific UI), it falls back to scraping the full list. Post-filter and dedup still apply.
- **Greenhouse / Lever**: No server-side location filter in their APIs; `location_filters` is applied **after** fetch so only matching jobs are added to the result and staging.
- **API**:
  - `POST /api/companies/{company_id}/scrape` — optional JSON body: `{"location_filters": ["Israel", "Remote"]}`. Filters are applied at source (Workday) or after fetch (Greenhouse/Lever).
  - `POST /api/careers/scrape-all` — optional JSON body: `{"location_filters": ["Israel", "Remote"]}`. Background task passes filters to each company scrape.
- **UI** (`index.html` — Careers tab):
  - "Location filter (optional)" input with placeholder "e.g. Israel, Remote, Tel Aviv"; pre-filled from profile `preferred_locations` when available.
  - Helper text: For Workday, the filter is applied on the career site when possible; for other ATS, only matching jobs are saved. Users can also paste a Workday job list URL after selecting a location on the site. Leave empty to save all.
  - Single-company "Scrape" and "Scrape All" send current location filter value (comma-split, trimmed) in request body.
- **Tests** (`tests/test_careers.py`): Workday `_job_list_url` preserves query params; `fetch_jobs` with `location_filters` (mocked Playwright); `TestCareersScrapeAPI` confirms scrape endpoints accept `location_filters` in body.

### 3. Review and Approve Pulled Jobs (Careers)
User can review and approve (including partial selection) jobs pulled from tracked company career sites before they enter the main jobs pipeline.

- **Flow**: Scrape (single or scrape-all) → jobs go to **staging** only → user opens **Review pulled jobs** → selects runs and jobs → **Approve selected** or **Approve all** → selected jobs are inserted into `jobs` (status `pending_scrape`) and removed from staging. **Discard run** removes all staging jobs for that run.
- **New tables** (`src/linkedin_copilot/db.py`):
  - `scrape_runs`: One row per scrape (company_id, scraped_at, total_found, new_count, duplicates_count, errors, created_at).
  - `scraped_jobs_staging`: Staging rows (run_id, job fields: title, company, location, url, external_job_id, source, company_id, date_found, etc.). On approve, rows are moved into `jobs` then deleted from staging.
- **New model** (`src/linkedin_copilot/models.py`): `ScrapeRun` (id, company_id, scraped_at, total_found, new_count, duplicates_count, errors, created_at, pending_count).
- **DB helpers** (`db.py`): `create_scrape_run`, `insert_staging_job`, `get_runs`, `get_staging_jobs`, `get_run_by_id`, `approve_staging_jobs`, `discard_run`, `get_staging_count_by_company`. Approve deduplicates by `external_job_id` + source (skips if job already in `jobs`).
- **Scrape behavior change**: `POST /api/companies/{id}/scrape` and background scrape-all now write to staging only (no direct insert into `jobs`). Response includes `run_id` and `pending_review_count`. Company `total_jobs` is updated only when jobs are approved (so company card shows approved count).
- **New API endpoints**:
  | Method | Endpoint | Description |
  |--------|----------|-------------|
  | GET | `/api/careers/runs` | List runs with pending staging (query: `company_id`, `limit`, `pending_only`). Returns runs with `company_name`, `pending_count`. |
  | GET | `/api/careers/runs/{run_id}/jobs` | List staging jobs for a run (for review UI). |
  | POST | `/api/careers/runs/{run_id}/approve` | Approve selected or all: body `{"job_ids": [1,2,3]}` or `{"approve_all": true}`. Returns `{ "approved", "skipped_duplicates" }`. |
  | POST | `/api/careers/runs/{run_id}/discard` | Discard all staging jobs for the run. |
- **GET /api/companies**: Response now includes `pending_review_count` per company (staging jobs for that company).
- **Review UI** (`/careers/review`, `src/linkedin_copilot/templates/review_pulled.html`):
  - Dedicated page listing runs with pending counts (company name, scraped time, pending count). Click a run to load its staging jobs.
  - Table: checkboxes, Title, Company, Location, Source, Date found. Toolbar: Select all, Clear selection, Approve selected (N), Discard run.
  - Partial approval: select a subset and "Approve selected"; only those are added to Jobs. Empty state when no runs with pending jobs.
- **Careers tab** (`index.html`): After single-company scrape, success message includes link "Review pulled jobs". After scrape-all complete, message includes "Review pulled jobs". Company card shows "X jobs" and, when applicable, "Y pending review" link to `/careers/review`. "Review pulled jobs" link always visible in Tracked Companies header.
- **Tests**: 24 tests in `tests/test_careers_staging.py` (DB: create run, insert staging, get_runs, get_staging_jobs, approve full/partial, dedupe on approve, discard, get_staging_count_by_company; API: GET runs empty/with data, GET run jobs 200/404, POST approve with job_ids/approve_all/400, POST discard 200/404).

### 3a. Careers: Filter at source (Workday)
Location filter is now applied **on the career site** when possible for efficiency. For **Workday**: (1) The job list URL preserves query params (e.g. `?locationHierarchy1=...`), so pasting a URL after selecting a location on the site uses that filter. (2) When `location_filters` is set and the URL has no location param, the scraper tries to apply the location facet in the browser (open Location filter, select e.g. Israel, wait for refresh) before scraping; if the facet cannot be found (tenant-specific UI), it scrapes the full list. Greenhouse and Lever have no server-side location filter; filters are applied after fetch. Scrape APIs again accept optional `location_filters` in the request body. See **§2. Careers Location Filter** for full detail.

### 4. Single-company scrape in-row progress bar
When the user clicks the per-company scrape button (refresh icon in a company row) on the Careers tab, an in-row progress indicator is shown for that company until the request completes.

- **Behavior**: Clicking "Scrape now" on a company card shows (1) the existing button spinner and disabled state, and (2) a thin indeterminate progress bar and "Scraping..." label in the same row, below the main card content. When the scrape finishes (success or error), the progress bar is hidden and the button is restored. On success, `loadCompanies()` re-renders the list. Single-company scrape remains synchronous (no backend change); the progress bar is indeterminate only.
- **Implementation** ([src/linkedin_copilot/templates/index.html](src/linkedin_copilot/templates/index.html)):
  - Company cards now have a wrapper `company-card-row` and a sibling `company-row-progress` container (hidden by default, shown via `aria-busy="true"`) containing a label and an indeterminate progress bar. CSS: `.company-row-progress`, `.company-row-progress-bar.indeterminate` with a sliding animation.
  - `scrapeCompany(companyId)` sets `aria-busy="true"` on the row’s progress element before the fetch and sets `aria-busy="false"` in `finally`, and restores the scrape button in `finally`. Progress container has `aria-label="Scraping in progress"` for accessibility.
- **No backend changes**; no new tests required (existing scrape API tests remain sufficient).

### 5. Add Company UX Fix (Careers Tab)
Fixed two issues when adding a company from the Careers tab:

- **"Addition failed" despite success**: The client could throw in the success path (e.g. if `data.company` was missing or `data.company.name` undefined), causing the catch block to show "Failed to add company" even when the server had returned 200 and inserted the company. **Fix**: Treat success only when `resp.ok && data.success && data.company`; use `data.company.name || 'Company'` for the notification; parse `resp.json()` in a try/catch so invalid response body does not throw; show a clear error when response is not OK or body is invalid.
- **List not updating without refresh**: `loadCompanies()` was only called inside the success branch, so any throw (or non-success response) meant the list was never refreshed. **Fix**: Call `await loadCompanies()` in a `finally` block so the company list always refreshes after an add attempt (whether success or failure), so if the add succeeded the new company appears without a page refresh.

- **Tests** (`tests/test_careers.py`): New `TestAddCompanyAPI` — success returns 200 with `success: true` and `company` (including `name`); missing/invalid `careers_url` returns 400; unsupported ATS returns 400. Total careers tests: 82.

### 6. In-App Application Assistant
Complete in-app job application feature with live browser view and AI-assisted form filling:

- **New Module** (`src/linkedin_copilot/linkedin/apply_session.py`):
  - `ApplySessionEngine`: Core session management with Playwright browser
  - Screenshot streaming via WebSocket (5 FPS)
  - Form field detection and auto-filling
  - Action execution with user confirmation
  - Safety guards (no auto-submit)

- **Form Detection** (`src/linkedin_copilot/linkedin/form_detector.py`):
  - Detects LinkedIn Easy Apply form fields (text, select, radio, checkbox, file)
  - Extracts labels, requirements, and current values
  - Maps fields to profile data for auto-suggestions
  - Pattern matching for common field types (email, phone, experience, work auth)

- **New Models** (`src/linkedin_copilot/models.py`):
  - `ApplySession`: Session state, job info, detected fields, progress
  - `ApplySessionStatus`: Enum (idle, navigating, form_ready, filling, reviewing, submitted, etc.)
  - `FormField`: Field metadata with suggested values and sources
  - `ApplicationAction`: Action tracking for audit trail
  - `WebSocketMessage`: Real-time communication format

- **Database Additions** (`src/linkedin_copilot/db.py`):
  - `apply_sessions` table for session persistence
  - `session_actions` table for action audit trail
  - CRUD operations for sessions and actions

- **API Endpoints** (Apply Session):
  | Method | Endpoint | Description |
  |--------|----------|-------------|
  | POST | `/api/apply/start/{job_id}` | Start application session |
  | GET | `/api/apply/session/{id}` | Get session status |
  | POST | `/api/apply/session/{id}/fill-field` | Fill a form field |
  | POST | `/api/apply/session/{id}/fill-all` | Fill all suggested |
  | POST | `/api/apply/session/{id}/next-step` | Go to next page |
  | POST | `/api/apply/session/{id}/submit` | Submit (requires confirmation) |
  | POST | `/api/apply/session/{id}/cancel` | Cancel session |
  | WS | `/ws/apply/{session_id}` | Live screenshot stream |

- **UI** (`src/linkedin_copilot/templates/apply.html`):
  - Split-panel layout: Live browser view + Form fields panel
  - Real-time screenshot updates via WebSocket
  - AI-suggested values with "[AI]" badge
  - Fill All / Next Step / Submit buttons
  - Submit confirmation modal with countdown timer
  - Session status indicator with colored badges

- **Jobs Page Integration** (`jobs.html`):
  - "Apply with Assistant" button in job modal (for Easy Apply jobs)
  - Opens `/apply/{job_id}` page
  - Purple gradient styling to distinguish from regular apply

- **LLM Integration** (`src/linkedin_copilot/prompts.py`):
  - `FORM_FIELD_ANSWER_PROMPT`: Generates answers for form fields
  - Uses profile + resume context
  - Returns confidence level (high/medium/low)

- **Safety Mechanisms**:
  - Submit requires explicit `{"confirmed": true}` in request
  - 3-second countdown before submit button enables
  - All actions logged to database for audit
  - Session timeout after 10 minutes of inactivity

- **In-App View & Easy Apply Fixes** (`apply_session.py`):
  - **Screenshot streaming from start**: Screenshot loop now starts immediately after browser launch (before clicking Easy Apply), so the in-app browser panel shows the LinkedIn page as soon as it loads, even when Easy Apply fails.
  - **Diagnostic screenshot on error**: Before reporting "Easy Apply button not found" (or any error), a final screenshot is broadcast so the user can see what the page looked like.
  - **Error UX**: After an error, streaming continues for 3 seconds so the user can see the page state before the session stops.
  - **Page load**: Navigation waits for `networkidle` (with timeout fallback) plus a short delay so dynamic content (e.g. Easy Apply button) has time to render.
  - **Easy Apply button detection**: Extended selector list (e.g. `button.jobs-apply-button`, aria-labels, text "Easy Apply", container-based selectors, Artdeco primary apply). Retries after scroll-to-top; verifies modal opens after click; improved logging (page title, URL) when button is not found.
  - **Browser mode**: Apply session always runs headless; screenshots are streamed to the UI (no separate browser window).

- **Tests**: 35+ unit tests in `tests/test_apply_session.py`:
  - Model creation and serialization
  - Form field pattern matching
  - Profile data mapping
  - Database operations
  - Safety guards

### 8. Navigation Badges for Pending Items
Visual badges in the sidebar indicate when there are items waiting to be handled in the Jobs List or Review pulled views.

- **Backend** (`src/linkedin_copilot/db.py`, `src/linkedin_copilot/web.py`):
  - Added `get_pending_jobs_count()` to count jobs in the main pipeline with statuses `pending_scrape` or `pending_match` (excluding matched/applied).
  - Added `get_pending_scrape_count()` and `get_pending_match_count()` for split counts (used by notifications).
  - Added `get_total_staging_jobs_count()` to count all rows in `scraped_jobs_staging` (pulled jobs pending review across all companies).
  - Endpoint `GET /api/badges` returns a JSON payload:
    - `jobs_pending`: number of pending jobs in the main pipeline.
    - `review_pending`: total number of staging jobs awaiting review.
    - `pending_scrape`: count of jobs in `PENDING_SCRAPE` only.
    - `pending_match`: count of jobs in `PENDING_MATCH` only.
    - `suggested_action`: when non-null, `{ "action", "label", "url", "message" }` for the next-best-action notification strip. Rule: prefer `review_pulled` when `review_pending > 0`, else `process_pending` when `jobs_pending > 0`, else null.

- **UI** (`src/linkedin_copilot/templates/base.html`):
  - Sidebar `Jobs` group now displays small numeric badges next to:
    - `Jobs List` — shows `jobs_pending`.
    - `Review pulled` — shows `review_pending`.
  - Badges are hidden when the respective count is zero and capped visually at `99+` for large counts.
  - A global `window.refreshNavBadges()` helper fetches `/api/badges` and updates the badges and the notification strip; it runs automatically on initial page load.

- **Notification strip (next best action)** (`src/linkedin_copilot/templates/base.html`):
  - A strip in the sidebar (below the logo, above Profile) appears when there is pending work. It shows a short message (e.g. "5 jobs pending review") and one primary CTA: "Review pulled" (links to `/careers/review`) or "Process All Pending" (links to `/jobs`). The backend recommends a single action via `suggested_action`; the strip is hidden when `suggested_action` is null. Uses `aria-live="polite"` and is keyboard-focusable.

- **Integration Hooks** (`jobs.html`, `review_pulled.html`):
  - After marking a job as applied from the job modal, `refreshNavBadges()` is called so the Jobs badge reflects the updated pending count.
  - After approving staging jobs into the main pipeline or discarding a run on the Review pulled page, `refreshNavBadges()` is called so both badges stay in sync with the database.

- **Tests** (`tests/test_badges.py`):
  - Unit tests for `get_pending_jobs_count()`, `get_pending_scrape_count()`, `get_pending_match_count()`, and `get_total_staging_jobs_count()` against a temporary SQLite database.
  - API test for `GET /api/badges` to verify `jobs_pending`, `review_pending`, `pending_scrape`, `pending_match`, and `suggested_action` reflect seeded data.
  - Tests for the next-best-action rule: `suggested_action` null when both counts zero; `process_pending` when only jobs pending; `review_pulled` when only review pending; `review_pulled` preferred when both non-zero.

### 9. Pipeline Dashboard (Home)
The **dashboard** is the main **home page** at `/`. Pipeline overview and next actions.

- **Backend** (`src/linkedin_copilot/db.py`, `src/linkedin_copilot/web.py`):
  - `get_matched_count()` and `get_applied_count()` in db.py for pipeline stage counts.
  - `_dashboard_data()` builds counts, suggested_action (reuses `_build_suggested_action`), and top high-match jobs (Apply/Consider, up to 10, sorted by score).
  - `GET /`: With no `tab` (or invalid tab) renders `dashboard.html` as home (same content as former `/dashboard`).
  - `GET /dashboard`: Redirects to `/` (302) so bookmarks and links still work.
  - `GET /api/dashboard`: Returns JSON with `pending_scrape`, `pending_match`, `jobs_pending`, `review_pending`, `matched_count`, `applied_count`, `suggested_action`, and `top_jobs` (id, title, company, location, url, match_score, recommendation).

- **UI** (`src/linkedin_copilot/templates/dashboard.html`):
  - Pipeline stage cards: Pending scrape, Pending match, Matched, Applied (with counts and left accent bar).
  - Next best action strip (same logic as sidebar): message + primary CTA (Review pulled or Process All Pending); or "You're all set" when no pending work.
  - Shortcuts: View all jobs, Process All Pending, Review pulled.
  - Top jobs to consider: list of up to 10 high-match jobs (Apply/Consider) with title, company, score; link to Jobs list; empty state when none.
  - Refresh button: fetches `/api/dashboard` and updates counts and action strip; calls `refreshNavBadges()`; shows loading spinner.

- **Navigation** (`src/linkedin_copilot/templates/base.html`):
  - "Home" links to `/` and is active when dashboard is shown (`is_home`). "Getting started" links to `/getting-started`.

- **Tests** (`tests/test_dashboard.py`):
  - `get_matched_count()` and `get_applied_count()` with empty and seeded DB.
  - `GET /api/dashboard`: structure (all keys), suggested_action when pending, top_jobs populated when matched jobs with match results exist.
  - `GET /`: 200, HTML contains "Pipeline dashboard", stage labels, "View all jobs", "Top jobs". `GET /dashboard`: 302 to `/`.

### 10. Careers Pull/Scrape Duplication Hardening (NEW)
Two root causes were addressed:

- **Race on scrape-all start**
  - `POST /api/careers/scrape-all` used to check `_careers_scrape_status["running"]` before creating the background task, but status was set inside the background function.
  - Fast repeated clicks could schedule two background tasks, creating duplicate runs/cards per company.
  - **Fix:** status is now set atomically before scheduling background work (lock-protected), and single-company scrape uses the same guard.

- **Re-scrape duplicated pending-review jobs**
  - Dedupe during scrape used only `jobs` table (`external_job_id + source`), ignoring pending rows in `scraped_jobs_staging`.
  - Re-scraping before approval/discard re-inserted the same jobs into staging.
  - **Fix:** added `careers_job_exists_in_jobs_or_staging(job)` and applied it before staging inserts. `duplicates_count` now includes these staging duplicates too.

- **Regression tests added**
  - `tests/test_careers.py::TestCareersScrapeAPI::test_scrape_all_rejects_second_start_while_running`
  - `tests/test_careers_staging.py::TestCareersScrapeDedupAcrossStaging::test_rescrape_does_not_restage_same_external_job`

### 7. Frontend and UI/UX polish
Template- and CSS-only improvements for accessibility, consistency, clarity, and visual polish (no backend or data model changes):

- **Accessibility** ([base.html](src/linkedin_copilot/templates/base.html)):
  - **Focus**: `:focus-visible` rings for nav links, sidebar toggle, logo, and buttons (accent-colored outline) so keyboard users always see focus.
  - **Skip link**: "Skip to main content" at top of page; main content has `id="main-content"` and `tabindex="-1"` for focus target.
  - **Sidebar drawer**: Focus trap when drawer is open (focus moves to first nav link; Shift+Tab from first wraps to last); focus returns to toggle on close; `aria-expanded` on toggle; overlay gets `aria-hidden` when closed.

- **Reduced motion** ([base.html](src/linkedin_copilot/templates/base.html)):
  - `@media (prefers-reduced-motion: reduce)` disables sidebar transform transition and `.animate-fade-in` / `.animate-slide-up` animations.

- **Sidebar polish** ([base.html](src/linkedin_copilot/templates/base.html)):
  - Small SVG icons next to "PROFILE", "SEARCH", and "JOBS" group titles for scanability.

- **Button hierarchy** ([profile.html](src/linkedin_copilot/templates/profile.html)):
  - "Replace CV" (when CV is present) and "Clear Session" use `btn-secondary` for consistency; primary actions (e.g. Connect LinkedIn) remain primary.

- **Tooltips and badges**:
  - Search tabs ([index.html](src/linkedin_copilot/templates/index.html)): Tab badges have `title` (e.g. "Terms from profile", "Tracked companies", "Active filters").
  - Review pulled ([review_pulled.html](src/linkedin_copilot/templates/review_pulled.html)): Run card "X pending" badge has `title="X jobs pending review"`.
  - Jobs list ([jobs.html](src/linkedin_copilot/templates/jobs.html)): LLM badge has smaller size and `title="LLM provider for job matching"`.

- **Truncation and scroll**:
  - **Profile**: Guest toggle section allows text to wrap (no overflow hidden); CV preview area has `max-height: 50vh` and `overflow-y: auto` so long CVs scroll.
  - **Search**: Smart Search keyword/title preview has `max-height` and scroll; AI tab shows helper text when no generated queries ("Generated queries will appear here").
  - **Jobs**: Table body rows have hover style; loading overlay during fetch; empty state when filters yield zero jobs ("No jobs match your current filters" with link to clear column filters via `clearAllColumnFilters()`).

- **Visibility and grouping**:
  - Review pulled: "Back to Search" link has `position: relative; z-index: 1` on header so it stays visible.
  - Search page: "Will run multiple searches" helper is grouped with the Search button (flex wrapper, gap).

- **Conventions**: Primary actions use `btn` / `btn-gradient`; secondary use `btn-secondary`. Icon-only or numeric badges use `title` or `aria-label` where meaning isn’t obvious. No new CSS classes beyond existing design tokens (`--accent-*`, `--bg-*`, etc.).

### 2. LinkedIn Profile Name Display
Added display of the connected LinkedIn profile name in the UI:

- **Backend Changes** (`src/linkedin_copilot/linkedin/auth.py`):
  - `extract_profile_name()`: New function to extract user's name from LinkedIn profile page
  - `save_session()`: Now accepts and stores `profile_name` alongside cookies
  - `get_session_profile_name()`: Retrieves stored profile name from session file
  - `validate_session()`: Returns `(is_valid, profile_name)` tuple

- **API Update** (`/api/session/status`):
  - Now returns `profile_name` field in response

- **UI Updates**:
  - **Profile Page**: Shows "Connected as [Name]" instead of generic "Connected to LinkedIn"
  - **Search Page**: Shows profile name in header indicator badge (e.g., "Rami Salameh")

### 2. Explore Jobs Feature
New AI-powered continuous job discovery mode:

- **New Module** (`src/linkedin_copilot/explore/`):
  - `engine.py`: Exploration orchestrator with session management
  - `strategies.py`: Query generators (profile, skill combo, domain, technology)
  - `intelligence.py`: Search effectiveness analysis and learning

- **Query Generation Strategies**:
  - Profile-based: Uses keywords, titles, skills from user profile
  - Skill combinations: Permutes top skills with role words
  - Domain expansion: Expands to adjacent industries
  - Technology adjacency: Maps technologies to related tools/roles
  - AI learning: Optimizes queries based on search effectiveness

- **Database Additions**:
  - `exploration_sessions` table for session tracking
  - Enhanced `search_history` with effectiveness metrics (`avg_match_score`, `high_matches`, `strategy_source`)

- **API Endpoints**:
  - `POST /api/explore/start` - Start exploration with config
  - `POST /api/explore/stop` - Stop current session
  - `POST /api/explore/pause` - Pause (resumable)
  - `POST /api/explore/resume` - Resume paused session
  - `GET /api/explore/status` - Get progress
  - `GET /api/explore/insights` - Get effectiveness analysis
  - `GET /api/explore/sessions` - List past sessions

- **UI** (Search page → Explore tab):
  - Start/Stop/Pause controls
  - Real-time progress dashboard
  - Current search indicator
  - Search insights panel with top queries
  - Configurable: intensity, max searches, strategies, filters

- **LLM Integration**:
  - New `EXPLORE_QUERIES_PROMPT` for context-aware query generation
  - `generate_exploration_queries()` method uses search history and job patterns

- **Tests**: 32 unit tests in `tests/test_explore.py`

### 3. Jobs Page Pagination & Filtering
- **Server-side pagination** with configurable page size (10, 25, 50, 100)
- **Database layer** (`db.py`):
  - Added `get_jobs_paginated()` with LIMIT/OFFSET, filters, and sorting
  - Added `get_match_results_for_jobs()` for batch match lookups
  - **Multi-value filters**: `status_filters`, `recommendation_filters`, `company_filters`, `title_filters`, `location_filters` (lists); company exact match, title/location substring (case-insensitive)
  - Added `get_jobs_facets(column, ...)` to return distinct values for a column (for filter dropdowns), respecting current filters
- **API enhancements** (`/api/jobs`):
  - Query params: `page`, `per_page`, `status`, `search`, `recommendation`, `hide_applied`, `sort_by`, `sort_dir`, `source`
  - **Column filter params** (multi): `status[]`, `recommendation[]`, `company[]`, `title[]`, `location[]`
  - Returns pagination metadata (`total_items`, `total_pages`, `has_next`, `has_prev`)
- **Facets API** (`GET /api/jobs/facets`):
  - Query: `column` (company | title | location | status), optional `limit`, plus same filter params as `/api/jobs` to scope facets
  - Returns `{ "column": "...", "values": ["...", ...] }` for populating column filter dropdowns
- **Frontend** (`jobs.html`):
  - Pagination controls (First/Prev/Next/Last, page numbers, per-page selector)
  - Server-side filtering with 300ms debounce
  - **Column filter dropdowns**: Funnel icon on Company, Role/Title, Location, Status, Recommendation; open panel with search (where applicable), checkboxes, Select all, Apply, Clear filter; badge on icon shows count when filter is active
  - Role/Title column labeled "Role / Title" for filtering by job role (e.g. Senior Full Stack, UX)
  - URL state management for bookmarkable pages
  - Loading state during page transitions
  - **Hide Applied toggle** - filters out jobs you've already applied to
  - Added "Applied" option to status filter dropdown
- **Tests**: 20 unit tests in `tests/test_pagination.py`

## Search UX v2 – Documentation

- The current **Search + Careers** experience is documented in:
  - `.ai/knowlege-base/SEARCH_EXPERIENCE.md` – unified Search tab (manual + AI assist + advanced).
  - `.ai/knowlege-base/CAREERS_FLOW.md` – full Careers flow, staging, and review.
- When updating search-related behavior or flows, update these docs first, then adjust this handover summary as needed.

## Previous Session Changes

### 1. Profile Page Consolidation & Navigation Reorder
- **Reordered navigation**: Profile (1) → Search (2) → Jobs (3) with step number badges
- **Consolidated Profile page** (`profile.html`):
  - LinkedIn Session card with loading overlay while checking status
  - "Search as guest" toggle option
  - CV upload form and preview (moved from index.html)
- **Simplified Search page** (`index.html`):
  - Removed CV upload section
  - Removed LinkedIn session card
  - Kept only unified search (Quick/Smart/AI tabs)
  - Session indicator is clickable and links to Profile page

### 2. Logo & Favicon Enhancements
- Made header logo clickable (links to main page `/`)
- Added logo icon with search symbol
- Added inline SVG favicon (gradient blue-to-purple)

### 3. Unified Search Experience
- Consolidated 3 separate search sections into tabbed interface:
  - **Quick Search**: Manual keyword + location
  - **Smart Search**: Profile-based keywords with term count badges
  - **AI Suggestions**: LLM-generated queries from CV
- Shared collapsible filters section
- Active filter pills display
- Smart button states and validation

### 4. UX Polish & Micro-interactions
- Pill-style tabs with SVG icons
- Loading skeletons for AI generation
- Enhanced empty states with icons and CTAs
- Hover effects and transitions throughout
- Real-time input validation

### 5. Track Applied Jobs Feature
- Added `APPLIED` status to `JobStatus` enum
- Added `/api/job/{job_id}/applied` endpoint
- Created confirmation dialog after "Open on LinkedIn" click
- Added green "Applied" badge styling

### 6. UI Polish (Job Modal)
- Redesigned Overview tab with centered layout
- Score card with color-coded backgrounds (green/yellow/red)
- Cleaner meta info grid
- Improved tabs and description styling

### 7. Anonymous Search Fix
- Improved handling when LinkedIn redirects to authwall
- Added guest page selectors
- Better logging for anonymous mode

### 8. Logo Scraping Unification
- Fixed placeholder URL detection (`static.licdn.com/aero-v1/`)
- Updated `get_jobs_missing_logos()` to catch placeholders
- Added `_is_valid_local_logo()` helper
- Logos download locally to `data/logos/`

## Database Schema

Key table: `jobs`
```sql
id, title, company, location, url, linkedin_job_id, external_job_id,
date_found, date_posted, easy_apply, description_snippet, 
company_logo_url, status, full_description, source, company_id
```

Match results in: `match_results`
```sql
job_id, match_score, recommendation, reasons, missing, 
inferred, suggestions, timestamp
```

Companies for career site tracking in: `companies`
```sql
id, name, careers_url, ats_type, board_token, logo_url,
enabled, last_scraped, total_jobs, created_at
```

Scrape runs (review-and-approve) in: `scrape_runs`
```sql
id, company_id, scraped_at, total_found, new_count, duplicates_count, errors, created_at
```

Staging jobs (pending review) in: `scraped_jobs_staging`
```sql
id, run_id, title, company, location, url, linkedin_job_id, external_job_id,
date_found, date_posted, easy_apply, description_snippet, company_logo_url, source, company_id, created_at
```

Exploration sessions in: `exploration_sessions`
```sql
id, started_at, ended_at, status, total_searches,
completed_searches, total_jobs_found, unique_jobs,
duplicates, config (JSON), insights (JSON), explored_keywords (JSON)
```

Search history in: `search_history`
```sql
keywords, location, search_time, jobs_found, filters (JSON),
avg_match_score, high_matches, strategy_source
```

Apply sessions in: `apply_sessions`
```sql
id (UUID), job_id, job_title, company, job_url, status,
current_step, total_steps, detected_fields (JSON),
started_at, updated_at, ended_at, error_message, screenshots_dir
```

Session actions in: `session_actions`
```sql
id, session_id, action_type, target_field_id, target_selector,
value, status, error_message, created_at, executed_at
```

### Persistent Pipeline Worker (Task Queue)

The system now persists long-running scrape/match work in SQLite so progress survives server reloads and tasks can be retried/cancelled.

- Table: `pipeline_tasks`
  - `task_group_id`: groups tasks for a single “run” (e.g. Process All Pending)
  - `task_type`: `scrape_job_description` | `match_job`
  - `status`: `queued` | `running` | `succeeded` | `failed` | `cancelled` | `retry`
  - `payload_json`: currently `{ "job_id": <int> }`
- Worker: started on FastAPI startup and processes tasks with configurable concurrency (`WORKER_CONCURRENCY`) and LLM parallelism (`LLM_MAX_CONCURRENT`).
- UI progress: `/api/progress` prefers task-group progress (from `active_task_group_id` in `web_state.json`) so the Jobs page banner works across reloads.

## Environment Configuration

`.env` file:
```
LLM_PROVIDER=openai  # or "ollama"
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
OLLAMA_MODEL=qwen2.5-coder:7b
HEADLESS=true
```

## API Endpoints (Key)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Home = pipeline dashboard when no `tab`; Search page when `tab=search`, `tab=careers`, or legacy quick/smart/ai/explore |
| GET | `/search` | Redirects to `/?tab=search` |
| GET | `/profile` | Profile page (session + CV; query `section`: linkedin, cv) |
| GET | `/getting-started` | Getting started / How it works guide |
| GET | `/dashboard` | Redirects to `/` (302) |
| GET | `/jobs` | Jobs list page |
| GET | `/api/dashboard` | Dashboard data (counts, suggested_action, top_jobs) as JSON |
| POST | `/upload-cv` | Upload CV file |
| POST | `/run-search` | Run quick search |
| POST | `/batch-search` | Run smart/batch search |
| POST | `/batch-search-generated` | Run AI-generated searches |
| GET | `/api/session/status` | Check LinkedIn session status |
| POST | `/api/session/login` | Start LinkedIn login flow |
| POST | `/api/session/clear` | Clear LinkedIn session |
| POST | `/api/generate-searches` | Generate AI search queries |
| POST | `/api/process-all` | Process pending jobs |
| GET | `/api/jobs` | Get jobs JSON (paginated; filters: status[], recommendation[], company[], title[], location[]) |
| GET | `/api/jobs/facets` | Get distinct values for a column (company, title, location, status) for filter dropdowns |
| GET | `/api/job/{id}` | Get job detail |
| POST | `/api/job/{id}/applied` | Mark job as applied |
| POST | `/api/jobs/rescrape` | Re-scrape selected jobs |
| POST | `/api/jobs/rematch` | Re-match selected jobs |
| GET | `/api/progress` | Get processing status |
| POST | `/api/explore/start` | Start exploration session |
| POST | `/api/explore/stop` | Stop exploration session |
| POST | `/api/explore/pause` | Pause exploration (resumable) |
| POST | `/api/explore/resume` | Resume paused exploration |
| GET | `/api/explore/status` | Get exploration progress |
| GET | `/api/explore/insights` | Get search effectiveness data |
| GET | `/api/explore/sessions` | List past exploration sessions |
| GET | `/apply/{job_id}` | In-app application page |
| POST | `/api/apply/start/{job_id}` | Start application session |
| GET | `/api/apply/session/{id}` | Get session status |
| POST | `/api/apply/session/{id}/fill-field` | Fill a form field |
| POST | `/api/apply/session/{id}/fill-all` | Fill all suggested values |
| POST | `/api/apply/session/{id}/next-step` | Navigate to next form page |
| POST | `/api/apply/session/{id}/submit` | Submit application (requires confirmation) |
| POST | `/api/apply/session/{id}/cancel` | Cancel and cleanup session |
| WS | `/ws/apply/{session_id}` | Live screenshot WebSocket stream |
| GET | `/api/companies` | List tracked companies for career sites |
| POST | `/api/companies` | Add company by careers URL (auto-detect ATS) |
| GET | `/api/companies/{id}` | Get single company details |
| DELETE | `/api/companies/{id}` | Remove company from tracking |
| POST | `/api/companies/{id}/toggle` | Enable/disable company |
| POST | `/api/companies/{id}/scrape` | Scrape jobs from single company (writes to staging; optional body: `location_filters`) |
| POST | `/api/careers/scrape-all` | Scrape all enabled companies (writes to staging; optional body: `location_filters`) |
| GET | `/api/careers/status` | Get career scraping progress |
| POST | `/api/careers/stop` | Stop current scrape operation |
| GET | `/api/careers/runs` | List scrape runs with pending staging (query: company_id, limit, pending_only) |
| GET | `/api/careers/runs/{run_id}/jobs` | List staging jobs for a run |
| POST | `/api/careers/runs/{run_id}/approve` | Approve selected or all staging jobs (body: job_ids or approve_all) |
| POST | `/api/careers/runs/{run_id}/discard` | Discard all staging jobs for run |
| POST | `/api/careers/validate-url` | Validate and detect ATS from URL |

## Career sites: company count vs Jobs page (root cause)

**Symptom:** Company card shows e.g. "14 jobs" but the Jobs page (with source = Workday) shows only 5.

**How the numbers are produced**

1. **Scrape:** The scraper collects job cards (e.g. 14), dedupes by URL in memory, then for each card checks `job_exists_by_external_id(external_id, source)`. If the job already exists → `result.add_duplicate()` (no insert). If new → `result.add_job(job)` and we insert it. So `result.total_found` = all considered (new + duplicates); `result.jobs` = only new jobs to insert.
2. **Before fix:** We called `update_company_last_scraped(company_id, result.total_found)`, so the company card showed "how many we saw this run" (e.g. 14), not how many are actually in the DB.
3. **Jobs page:** The list and total are from `get_jobs_paginated(..., source_filter=source)` — i.e. real rows in the DB with that source.

**Two possible explanations (no bug in insert/list logic)**

- **A) Status filter:** There are 14 Workday jobs in the DB, but the Jobs page had a status filter (e.g. "Pending Scrape"). If only 5 of those 14 are `pending_scrape` and the rest are `pending_match` or `matched`, the list correctly shows 5. The summary bar used to count all jobs (no source/status filter), so it was misleading.
- **B) Duplicates on first run:** First scrape finds 14 cards; 9 already exist in the DB (same `external_job_id` + source from an earlier run or another company). So we insert 5, and we had been setting company `total_jobs = 14` (total_found). Then the list shows 5 until the next scrape — after the fix we set `total_jobs = get_job_count_by_company(company_id)`, so the card shows the real DB count (e.g. 5 or 14).

**Fixes applied**

- Company card now shows **actual job count** for that company (from DB), not `result.total_found`.
- Status counts (summary bar) use the **same filters** as the list (source, status, etc.), so "X pending scrape" matches the current view.
- The `/jobs` route accepts a `source` query param so server-rendered and client state stay in sync.

**Bug fix (Workday external_id):** Workday job URLs look like `.../job/[location]/[job-title]_[id]-[suffix]`. We previously extracted only the first segment after `/job/` (e.g. `"Israel"`) as the external ID, so many jobs in the same location shared the same ID and were wrongly treated as duplicates (only one inserted per location). We now use the **full path** after `/job/` (e.g. `Israel/Clinical-Application-Specialist_12345-1`) so each job has a unique ID and all 14 are inserted.

**How to verify**

- Jobs page: set **Source = Workday**, **Status = All Statuses**. The list total and the company card should align with the number of Workday jobs in the DB for that company.
- After a scrape, the company card should show the same number as "Jobs" filtered by that source (for that company), minus any jobs from other companies with the same source.
- Re-scrape Philips (or any Workday company); you should now see all found jobs (e.g. 14) in the list, not just 5.

## Known Issues / Pending Work

1. **Logo scraping**: Some jobs still show placeholders; may need manual rescrape
2. **Anonymous search**: LinkedIn heavily limits guest access; results may be sparse
3. **Session expiry**: LinkedIn sessions may expire; user needs to re-login
4. **Workday**: Some tenants may use different page structure; selectors are best-effort. Rate limiting (2–5s between pages) is applied to avoid blocks.
5. **Workday description rescrape**: Rescrape / "Process All Pending" uses `WorkdayScraper.fetch_job_details` for Workday jobs (not LinkedIn extract). Description extraction was improved: `wait_until="load"`, longer wait for main content, multiple tenant selectors (`jobPostingDescription`, `jobPostingBody`, `compositeContainer`, etc.), and a JS fallback that returns the largest text block in `main` so Philips and other tenants are covered. Tests: `tests/test_workday_rescrape.py`, `TestWorkdayScraper::test_fetch_job_details_returns_description` in `test_careers.py`.

## Testing

About **220+ tests** across careers, pagination, explore, apply session, matcher, web nav, dashboard, badges, and other modules.

```bash
cd "/Users/rami_salameh/Job Search"
source .venv/bin/activate
python -m pytest tests/
```

## Terminal Status

The server runs in terminal 9 with auto-reload enabled. Check:
```bash
tail -f "/Users/rami_salameh/.cursor/projects/Users-rami-salameh-Job-Search/terminals/9.txt"
```

## Agent Transcripts

Past conversation history at:
`/Users/rami_salameh/.cursor/projects/Users-rami-salameh-Job-Search/agent-transcripts/`

## Contact Points

- **Database**: `data/linkedin_copilot.sqlite3` (SQLite)
- **Session**: `data/linkedin_session.json` (LinkedIn cookies)
- **Logs**: Terminal output via Loguru

---

*Handover prepared: March 14, 2026*
*Last updated: March 16, 2026 — Home = Dashboard: GET / shows pipeline dashboard as main home; GET /dashboard redirects to /. Getting started: GET /getting-started shows how-it-works guide (getting_started.html) with Guide badge, step icons, and "Go to Home" CTA. Nav: Home → /, Getting started → /getting-started. Tests: test_web_nav, test_dashboard updated.*
