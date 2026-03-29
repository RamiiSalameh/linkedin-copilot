## Careers Flow – Direct Company Sites

The **Careers** experience focuses on pulling jobs directly from **company career sites** (ATS platforms like Greenhouse, Lever, Workday) and feeding them into the same pipeline as LinkedIn jobs, with a **review step in between**.

It is intentionally separate from the main **Search** tab so users have two clear concepts:

- **Search** → Discover jobs primarily via LinkedIn (manual + AI/advanced).
- **Careers** → Discover jobs from **specific target companies** via their career sites.

---

## High-Level Flow

1. **Track companies**
   - User adds companies on the **Careers** tab by pasting a careers URL.
   - Backend auto-detects ATS type and board token, validates the URL, and stores a `Company` record.

2. **Scrape jobs**
   - From the Careers tab, the user can:
     - Scrape a **single company**.
     - Scrape **all enabled companies** in the background.
   - Jobs are **not** inserted into the main `jobs` table immediately.
   - Instead, they are written to a staging table (`scraped_jobs_staging`) and grouped by `scrape_run`.

3. **Review pulled jobs**
   - The **Review pulled** page lists runs with pending staging jobs.
   - For each run, the user can:
     - See all scraped jobs (title, company, location, source, date found).
     - Select **some or all** jobs to approve.
     - Approve or discard an entire run.

4. **Approve into pipeline**
   - When the user approves jobs from a run:
     - Approved rows are moved from `scraped_jobs_staging` into the main `jobs` table.
     - Jobs enter the normal pipeline with status `pending_scrape`.
     - Company `total_jobs` counts are updated.

5. **Work the jobs**
   - From this point, Careers jobs behave like LinkedIn jobs:
     - They can be scraped for descriptions and matched.
     - They surface in the Jobs list with a **Source** badge and filter (e.g. Workday, Greenhouse, Lever).

---

## Key UI Elements (Careers Tab)

### 1. Tracked companies

- Card list of tracked companies with:
  - Company name + logo (or placeholder).
  - ATS badge (Greenhouse, Lever, Workday, etc.).
  - Jobs count and “pending review” indicator.
  - Last scraped timestamp.
  - Per-company actions:
    - Enable / disable tracking.
    - Scrape now (with in-row progress bar).
    - Remove company from tracking.

### 2. Add company form

- Inputs:
  - Careers URL (can be branded URL, e.g. `careers.philips.com`).
  - Optional explicit company name (auto-derived if omitted).
- Behavior:
  - Resolves redirects before ATS detection.
  - Validates and auto-detects ATS type and board token.
  - Shows clear success or error messaging.

### 3. Location filters

- Optional **location filter** input on the Careers tab:
  - Example: `Israel, Remote, Tel Aviv`.
  - For Workday:
    - Applied **at source** when possible (URL query params / in-page Location facet).
  - For Greenhouse / Lever:
    - Applied **after fetch**, filtering out non-matching jobs before staging.

### 4. Scrape progress

- **Global scrape-all progress**:
  - Shows current company, how many companies completed, jobs found, and duplicates.
- **Per-company scrape progress**:
  - In-row indeterminate progress bar and label (“Scraping…”) while a company scrape is running.

---

## Review Pulled Jobs Page

Route: `/careers/review`

- **Runs list**
  - Shows recent scrape runs with:
    - Company name.
    - Scraped time.
    - Total found, new count, duplicates.
    - Pending count (jobs not yet approved/discarded).

- **Run detail**
  - When selecting a run:
    - Table of staging jobs with checkboxes.
    - Columns: Title, Company, Location, Source, Date Found.
    - Toolbar:
      - Select all / Clear selection.
      - Approve selected.
      - Approve all.
      - Discard run.

- **Post-approval behavior**
  - Approved jobs move into `jobs` with `pending_scrape`.
  - Discard removes all staging rows for that run.
  - Navigation badges are refreshed so Jobs / Review pulled counts stay accurate.

---

## Backend & Data Model (Summary)

- **Key models / tables**
  - `Company` / `companies` – tracked companies and ATS metadata.
  - `ScrapeRun` / `scrape_runs` – one per scrape operation.
  - `scraped_jobs_staging` – staging area for pulled jobs pending review.
  - `jobs` – main job pipeline table (includes `source` and `company_id`).

- **Primary endpoints**
  - Companies:
    - `GET /api/companies`
    - `POST /api/companies`
    - `GET /api/companies/{id}`
    - `DELETE /api/companies/{id}`
    - `POST /api/companies/{id}/toggle`
    - `POST /api/companies/{id}/scrape`
  - Careers scrape-all:
    - `POST /api/careers/scrape-all`
    - `GET /api/careers/status`
    - `POST /api/careers/stop`
  - Review & approve:
    - `GET /api/careers/runs`
    - `GET /api/careers/runs/{run_id}/jobs`
    - `POST /api/careers/runs/{run_id}/approve`
    - `POST /api/careers/runs/{run_id}/discard`

For deeper implementation details (schema, ATS-specific scrapers, tests), see `HANDOVER.md`.

---

## Duplication Handling (March 2026 hardening)

Two production issues were fixed in the pull/scrape flow:

1. **Duplicate runs from double-start race**
   - Root cause: `POST /api/careers/scrape-all` could be triggered twice before the background task flipped the runtime status to `running=true`.
   - Fix: startup status is now set atomically before scheduling the background task, guarded by a lock.
   - Result: second start request now returns `409` while a scrape is active (same guard also applies to single-company scrape).

2. **Re-scrape re-added the same positions to review**
   - Root cause: dedupe during scrape checked only the main `jobs` table. Jobs already in `scraped_jobs_staging` (pending review) were not considered duplicates.
   - Fix: dedupe now checks **both** `jobs` and `scraped_jobs_staging` before staging.
   - Result: re-scraping without approve/discard does not create duplicate review items.

### Metrics behavior after fix

- `new_count` counts only truly stageable jobs.
- `duplicates_count` includes:
  - already-known jobs in `jobs`, and
  - already-pending jobs in `scraped_jobs_staging`.

