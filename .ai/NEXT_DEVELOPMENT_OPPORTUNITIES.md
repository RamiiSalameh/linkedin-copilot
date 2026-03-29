# Next Development Opportunities — LinkedIn Copilot

**Role:** Senior Product Manager, Software Architect, UX Expert  
**Date:** March 16, 2026  
**Sources:** HANDOVER.md, CAREERS_FLOW.md, SEARCH_EXPERIENCE.md, codebase review

---

## 1️⃣ Current System Summary

### Product capabilities

- **Job discovery**
  - **LinkedIn:** Quick search (keyword + location), Smart Search (profile keywords × locations), AI-generated queries, Explore (continuous AI discovery), anonymous/guest search.
  - **Career sites:** Track companies by URL; auto-detect ATS (Greenhouse, Lever, Workday); scrape jobs with optional location filter; review-and-approve staging before jobs enter the main pipeline.
- **Pipeline:** Search/Careers → jobs in DB → **pending_scrape** → **pending_match** → **matched** (LLM score 0–100, Apply/Consider/Skip) → user can mark **applied**.
- **Application support**
  - **LinkedIn Easy Apply only:** In-app Apply Assistant (Playwright, live screenshot, form detection, AI-suggested values, fill-all/next-step/submit with confirmation). “Apply with Assistant” only shown for Easy Apply jobs.
  - **All other jobs:** “Open on LinkedIn” or open job URL; user confirms “I applied” to set status.
- **UX:** Home (how it works), Profile (LinkedIn + CV), Search (unified tabs + Careers), Jobs (paginated list, column filters, source filter, modal, Process All Pending), Review pulled (`/careers/review`), Apply page (`/apply/{job_id}`). Sidebar badges (jobs pending, review pending) and **next-best-action** strip (Process All Pending / Review pulled).
- **Data:** SQLite (jobs, match_results, companies, scrape_runs, scraped_jobs_staging, exploration_sessions, search_history, apply_sessions). LLM: Ollama or OpenAI. No auth; single-user.

### Main user flows

1. **Setup:** Connect LinkedIn (or guest), upload CV, set profile → Profile page.
2. **Discover:** Run search (Quick/Smart/AI) or Explore, or add companies and scrape from Careers → jobs in staging (Careers) or directly in pipeline (LinkedIn).
3. **Review (Careers only):** Go to Review pulled → select jobs → Approve → jobs enter pipeline as `pending_scrape`.
4. **Process:** Jobs list → “Process All Pending” (scrape + match) or work list with filters.
5. **Apply:** From job modal, “Apply with Assistant” (Easy Apply) or “Open on LinkedIn” / open URL, then mark applied.
6. **Track:** Filter by status/source/recommendation; hide applied; column filters; URL state for bookmarking.

### Architecture (summary)

- **Backend:** FastAPI, Uvicorn; `web.py` (routes + handlers), `db.py` (SQLite), `models.py` (Pydantic).
- **Modules:** `linkedin/` (auth, search, extract, apply_session, form_detector), `careers/` (base, greenhouse, lever, workday, detector, registry), `scoring/matcher.py`, `explore/` (engine, strategies, intelligence), `llm.py`, `prompts.py`.
- **Frontend:** Jinja2 + vanilla JS, design tokens (CSS variables), responsive sidebar (drawer on small viewports).
- **State:** In-memory `_progress_status` for Process All Pending; badges/notifications from `GET /api/badges`; no server-side session store beyond DB.

### Current limitations and weaknesses

- **Apply coverage:** In-app Apply Assistant only for LinkedIn Easy Apply. Career-site jobs (Greenhouse, Lever, Workday) and non–Easy Apply LinkedIn jobs only get “open URL” + manual “I applied.”
- **No pipeline overview:** User must open Jobs and use summary bar + filters to see pipeline state; no single dashboard showing stages, high-match queue, and suggested next actions.
- **No export/reporting:** Only single match-result JSON export; no bulk export (CSV/Excel) of jobs or pipeline for sharing/analysis.
- **No reminders/follow-ups:** No application deadline, “follow up in X days,” or reminder list for applied jobs.
- **No saved searches/alerts:** Cannot save a search configuration or get “new jobs” notifications.
- **Fragile surfaces:** Workday tenant-specific; anonymous LinkedIn heavily limited; LinkedIn session can expire.
- **No analytics dashboard:** Explore has effectiveness insights; no dedicated page for search/match performance or source breakdown.
- **Single-user only:** No multi-user or auth; all data in one SQLite file.

---

## 2️⃣ Improvement Opportunities

### User experience

- **Pipeline clarity:** One place to see “what’s pending,” “how many high-match,” and “what to do next” without opening Jobs and applying filters.
- **Apply parity:** Same “apply from app” feel for career-site applications where possible (at least open in-app browser and optionally assist with ATS forms).
- **Unified apply entry:** One clear “Apply” path for every job (Easy Apply → Assistant; others → open URL + “Mark applied” or future career-site flow).
- **Reduced context switching:** In-app browser for career-site apply; optional “today’s focus” list (e.g. top 5 Apply/Consider jobs).

### Feature gaps

- **Bulk export:** CSV/Excel of jobs (with status, score, source, company, URL) and optional match summary for reporting and offline use.
- **Application follow-up:** Optional reminder date per job; “Follow up” list and optional in-app reminder strip.
- **Saved searches:** Save search config (keywords, locations, filters); re-run or “notify when new jobs” (even if notification is in-app only at first).
- **Career-site apply:** Use job URL from Greenhouse/Lever (and later Workday) in an in-app browser with form detection and AI assist where structure allows.

### Performance and scalability

- **Process All Pending:** Already background; could add per-phase progress (scrape vs match) and optional parallelism limits to avoid overload.
- **Careers scrape-all:** Background task; could add queue and per-company concurrency control.
- **SQLite:** Adequate for single-user; path to multi-user would require auth and possibly different storage (out of scope for near-term).

### Monetization and engagement

- **Engagement:** Dashboard and “today’s focus” increase daily return; follow-up reminders bring users back.
- **Retention:** Saved searches and alerts create habit; export and reports add tangible value for serious job seekers.
- **Monetization:** Not in scope for current product; future options could be premium features (e.g. more alerts, advanced analytics) if product grows.

### Product growth and maintainability

- **Growth:** Pipeline dashboard and export make the product more “complete” for power users; career-site apply differentiates from LinkedIn-only tools.
- **Maintainability:** Dashboard and analytics can be built on existing APIs and DB; new ATS apply flows should reuse `apply_session` patterns and form detection where possible.
- **Competitive advantage:** Multi-source (LinkedIn + career sites), review-and-approve, LLM matching, and in-app apply (with future career-site support) are differentiators; dashboard and export support “serious job seeker” positioning.

---

## 3️⃣ Feature Proposals

### P1 — Job Search Pipeline Dashboard

- **Name:** Job Search Pipeline Dashboard  
- **Problem:** Users must open the Jobs page and rely on summary bar and filters to understand pipeline state and what to do next.  
- **Value:** Single view of pipeline health, high-match queue, and clear next actions increases clarity and daily engagement.  
- **Impact:** High — central place for “what’s pending,” “how many Apply/Consider,” and links to Process All Pending, Review pulled, and filtered Jobs.  
- **Complexity:** Medium (new route + template + optional API for counts/summaries; reuse existing badge/count logic).

---

### P2 — Bulk Export and Simple Reporting

- **Name:** Bulk Export & Reporting  
- **Problem:** No way to export the job pipeline (e.g. to CSV/Excel) for sharing, offline analysis, or reporting.  
- **Value:** Users can export jobs (and optionally match data) for spreadsheets, applications tracking outside the app, or simple reports.  
- **Impact:** High for power users and “serious job seeker” positioning; low friction.  
- **Complexity:** Low (new endpoint(s) and optional UI: export current list or full pipeline; CSV/Excel; reuse existing filters).

---

### P3 — Apply Assistant for Career-Site Jobs

- **Name:** In-App Apply for Career Sites (Greenhouse / Lever first)  
- **Problem:** Apply Assistant only works for LinkedIn Easy Apply; career-site jobs only get “open URL” and manual “I applied.”  
- **Value:** Same in-app apply experience for Greenhouse/Lever (and later Workday) where forms are structured; increases completion and differentiation.  
- **Impact:** High for users who rely on career-site jobs; differentiator.  
- **Complexity:** High (new flow by source: open job URL in Playwright, detect ATS form fields, map to profile, reuse fill/submit patterns; ATS-specific selectors and safety).

---

### P4 — Application Follow-up and Reminders

- **Name:** Application Follow-up & Reminders  
- **Problem:** No way to schedule a follow-up or be reminded about applied jobs.  
- **Value:** “Follow up in 7 days” or “remind me to check status” keeps pipeline active and brings users back.  
- **Impact:** Medium — improves retention and completeness of application tracking.  
- **Complexity:** Medium (DB: optional `reminder_at` / `follow_up_at` on jobs or linked table; “Follow up” list view; optional in-app reminder strip; no email/push required for v1).

---

### P5 — Saved Searches and In-App Alerts

- **Name:** Saved Searches & In-App Alerts  
- **Problem:** Users cannot save a search configuration or get notified when new jobs appear.  
- **Value:** Re-run saved searches with one click; “new jobs since last run” or “N new jobs” in-app alert.  
- **Impact:** Medium — habit formation and reduced repetition.  
- **Complexity:** Medium–High (DB: saved_search config; run and diff vs last run; store “last_run_at” and optional “new_count”; UI: save/load search, alert strip or badge).

---

### P6 — Unified “Apply” Entry and Mark-Applied Flow

- **Name:** Unified Apply Entry & Mark Applied  
- **Problem:** Apply entry differs by job type (Easy Apply vs others; career-site vs LinkedIn); “Mark applied” is easy to forget after opening URL.  
- **Value:** One clear “Apply” action: Easy Apply → Assistant; others → open URL in new tab (or in-app browser) with prominent “I applied” button and optional reminder to mark applied.  
- **Impact:** Medium — clearer UX and higher “applied” accuracy.  
- **Complexity:** Low (UI: single Apply button; logic: Easy Apply → `/apply/{id}`; else open URL + show “Mark as applied” in modal or toast; optional “Don’t forget to mark applied” copy).

---

### P7 — Search and Match Performance Analytics

- **Name:** Search & Match Performance Analytics  
- **Problem:** Explore has effectiveness insights but no dedicated place to see “which queries/sources perform best” and match distribution.  
- **Value:** Dedicated page: top queries by jobs found and match score, breakdown by source, match score distribution.  
- **Impact:** Medium — helps users tune searches and understand pipeline quality.  
- **Complexity:** Low–Medium (new page + API aggregating search_history + match_results + jobs by source; charts or tables).

---

## 4️⃣ Prioritized Roadmap (Impact vs Effort)

| Feature                         | Impact | Effort  | Priority |
|---------------------------------|--------|---------|----------|
| **P1 Pipeline Dashboard**       | High   | Medium  | **1 (Top)** |
| **P2 Bulk Export & Reporting** | High   | Low     | 2        |
| **P6 Unified Apply Entry**     | Medium | Low     | 3        |
| **P7 Search/Match Analytics**  | Medium | Low–Med | 4        |
| **P4 Follow-up & Reminders**   | Medium | Medium  | 5        |
| **P5 Saved Searches & Alerts**  | Medium | Medium–High | 6   |
| **P3 Career-Site Apply**       | High   | High    | 7        |

**Top recommendation: P1 — Job Search Pipeline Dashboard**

- **Why:** Highest impact for daily use: one screen that answers “what’s the state of my pipeline?” and “what should I do next?” without opening Jobs and configuring filters. Builds on existing badges and next-best-action logic and improves engagement and perceived completeness.
- **Quick wins:** P2 (Bulk Export) and P6 (Unified Apply Entry) are high-impact and low effort; can be done in parallel or immediately after P1.

---

## 5️⃣ Implementation Direction for Top Feature (P1 — Pipeline Dashboard)

### Goal

A dedicated **Pipeline Dashboard** page that shows pipeline stage counts, high-match queue, and clear next actions with links to Jobs, Review pulled, and Process All Pending.

### High-level architecture

- **New route:** `GET /dashboard` (or `/pipeline`) → render a new template `dashboard.html` (or `pipeline.html`).
- **Data:** Reuse and extend existing DB/API:
  - Counts: `get_pending_jobs_count()`, `get_pending_scrape_count()`, `get_pending_match_count()`, `get_total_staging_jobs_count()`, plus total matched and total applied.
  - Optional: `get_high_match_jobs(limit=10, min_score=70)` for “Top jobs to consider” (or reuse `get_jobs_paginated` with `recommendation=Apply` and `per_page=10`).
  - Next action: reuse `_build_suggested_action()` logic (or call `GET /api/badges` from frontend).
- **No new tables.** Optional: cache counts for 30–60s if dashboard is hit often (not required for v1).

### Affected modules

- **`web.py`:** New `GET /dashboard` handler; pass counts and optional “top jobs” to template; optionally new `GET /api/dashboard` returning JSON for SPA or future reuse.
- **`db.py`:** Optional helpers: e.g. `get_matched_count()`, `get_applied_count()`, `get_high_match_jobs(limit, min_score)` if not already derivable from `get_jobs_paginated` + existing filters.
- **`templates/base.html`:** Add “Dashboard” (or “Pipeline”) to sidebar; active state when on `/dashboard`.
- **New:** `templates/dashboard.html` (or `pipeline.html`).

### API / data changes

- **Optional new endpoint:** `GET /api/dashboard`  
  - Returns: `pending_scrape`, `pending_match`, `review_pending`, `matched_count`, `applied_count`, `suggested_action`, optional `top_jobs` (e.g. 5–10 Apply/Consider jobs with id, title, company, score, url).
- **Existing:** `GET /api/badges` already has `jobs_pending`, `review_pending`, `pending_scrape`, `pending_match`, `suggested_action`. Dashboard can call this and add one more call for matched/applied counts and top jobs, or a single `/api/dashboard` that aggregates.

### UX flow

1. User clicks **Dashboard** (or **Pipeline**) in sidebar.
2. Page loads with:
   - **Pipeline stages:** Cards or bars for Pending Scrape, Pending Match, Matched, Applied (with counts).
   - **Next best action:** Same strip as sidebar (e.g. “5 jobs pending review” + “Review pulled” button; or “12 jobs pending processing” + “Process All Pending”).
   - **Top jobs (optional):** List of 5–10 high-match jobs (Apply/Consider) with link to job modal or `/jobs?recommendation=Apply`.
   - **Shortcuts:** Buttons/links to “Process All Pending” (→ Jobs with Process All), “Review pulled” (→ `/careers/review`), “View all jobs” (→ `/jobs`).
3. Counts update on load; optional: “Refresh” button or periodic refresh every 60s when tab visible.
4. Accessible: same focus and skip-link patterns as rest of app; headings and structure for screen readers.

### Implementation steps (concise)

1. Add `get_matched_count()` and `get_applied_count()` in `db.py` (if not already available via existing queries).
2. Add `GET /api/dashboard` in `web.py` returning counts + `suggested_action` + optional `top_jobs` (reuse `get_jobs_paginated` with recommendation filter and limit).
3. Add `GET /dashboard` in `web.py` rendering `dashboard.html` with server-side counts and suggested_action (or have template fetch `/api/dashboard` for dynamic refresh).
4. Create `templates/dashboard.html`: extend `base.html`, pipeline stage cards, next-action strip, top jobs list, shortcut buttons.
5. Add “Dashboard” to sidebar in `base.html` with correct active state.
6. Add tests: API returns correct counts and suggested_action; dashboard page returns 200 and shows expected sections.

---

*This document can be updated as priorities or architecture change. For implementation details, wire to HANDOVER.md and existing epics (e.g. NOTIFICATIONS_NEXT_BEST_ACTION.md).*
