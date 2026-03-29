# Epic: Notifications + Next Best Action

This epic defines an in-app notification area and a single "next best action" so users see what to do next without opening the Jobs or Review pulled pages. It extends the existing badges system and reuses the same refresh flow.

---

## 1️⃣ Context Summary

### Current architecture

The app is a FastAPI backend ([src/linkedin_copilot/web.py](src/linkedin_copilot/web.py)) serving Jinja2 templates with vanilla JavaScript. Layout is a fixed left sidebar ([src/linkedin_copilot/templates/base.html](src/linkedin_copilot/templates/base.html)) plus main content. Data is stored in SQLite via [src/linkedin_copilot/db.py](src/linkedin_copilot/db.py).

### Relevant services and modules

- **Badges**: The backend exposes `GET /api/badges`, which returns `jobs_pending` and `review_pending`. In [db.py](src/linkedin_copilot/db.py), `get_pending_jobs_count()` counts jobs in `PENDING_SCRAPE` or `PENDING_MATCH`; `get_total_staging_jobs_count()` counts rows in `scraped_jobs_staging`.
- **Frontend**: [base.html](src/linkedin_copilot/templates/base.html) defines `fetchNavBadges()` (exposed as `window.refreshNavBadges`). It runs on page load and updates the elements `#nav-badge-jobs` and `#nav-badge-review`. The Jobs list and Review pulled pages call `refreshNavBadges()` after apply, approve, or discard so counts stay in sync.

### Existing APIs and components

- `GET /api/badges` returns `{ "jobs_pending": int, "review_pending": int }`. There is no split between pending_scrape and pending_match, and no suggested action or notification list.

### Constraints and dependencies

- No new backend process or cron; behavior is in-app only. The product is single-user with no auth. Implementation must reuse existing design tokens and sidebar structure and must not introduce a new CSS framework.

---

## 2️⃣ Feature Design

### User problem

Users do not see "what to do next" without opening the Jobs or Review pulled pages. The sidebar badges show counts but do not provide a prominent, actionable prompt.

### Product behavior

- **Notification area**: A dedicated strip (or compact dropdown) in the app, for example below the sidebar header and above the "Profile" group. It appears only when there is pending work and shows:
  - Short copy such as "12 jobs pending processing" and/or "5 jobs pending review."
  - One primary CTA: "Process All Pending" (links to `/jobs`, with optional scroll/focus to the pending banner) or "Review pulled" (links to `/careers/review`).

- **Next best action**: The backend recommends a single action (`process_pending` or `review_pulled`). When both pipeline pending and review pending are non-zero, the rule is to prefer `review_pulled` when `review_pending > 0`, else `process_pending`. The frontend shows one main button from this suggestion.

- **Optional (later)**: A "N new high-match jobs" line (e.g. jobs matched with score ≥ 70 since last visit) with a link to Jobs filtered by recommendation. This is deferred to a follow-up story.

### Expected UX flow

1. User opens any page; the existing `refreshNavBadges()` (or an extended "refresh notifications") runs.
2. The API returns counts and a suggested action.
3. If any count is greater than zero, the notification area is visible with message and primary button.
4. User clicks the button and navigates to `/jobs` or `/careers/review`. After they act, badges and notifications refresh via existing hooks.
5. If both counts are zero, the notification area is hidden or shows a neutral empty state (e.g. "You're all set").

---

## 3️⃣ Architecture Plan

### Backend

- Extend `GET /api/badges` to also return:
  - **pending_scrape** and **pending_match** (optional split), derived from the database via two small COUNT queries or one query returning both.
  - **suggested_action**: an object or null. When non-null: `{ "action": "process_pending" | "review_pulled", "label": string, "url": string }`. Rule: if `review_pending > 0` then `review_pulled`; else if `jobs_pending > 0` then `process_pending`; else null.

### Frontend

- In [base.html](src/linkedin_copilot/templates/base.html), add a **notification strip** in the sidebar (e.g. under the logo, above "Profile") with:
  - A container for message text and the primary button.
  - Visibility controlled by `suggested_action`: hidden when null, visible otherwise.
- Reuse the existing badge fetch (or rename to e.g. `refreshNavBadgesAndNotifications()`): call the same endpoint, then update both the nav badges and the notification area (message and button href/text from `suggested_action`).

### Data model

- No new tables for the minimal version. Optionally, a later iteration could store `last_visit` or "notifications dismissed at" in `web_state.json` (see `_load_web_state` / `_save_web_state` in [web.py](src/linkedin_copilot/web.py)) for "new high matches" or "don't show again today"; this is omitted in the first slice.

### API

- **Option A (recommended)**: Extend `GET /api/badges` with `pending_scrape`, `pending_match`, and `suggested_action`. One endpoint remains; backward compatible, since existing clients can ignore the new keys.
- **Option B**: Add a new `GET /api/notifications` that returns the same data plus existing badge counts. Use only if the badges response must stay minimal. Recommendation: Option A for fewer endpoints and a single refresh path.

### Services and modules

- [web.py](src/linkedin_copilot/web.py): extend the badge handler; optional helper for suggested_action logic.
- [db.py](src/linkedin_copilot/db.py): optional `get_pending_scrape_count()` and `get_pending_match_count()` if the split is desired; otherwise keep a single `get_pending_jobs_count()` or one query returning both.
- [base.html](src/linkedin_copilot/templates/base.html): notification strip HTML/CSS and JS to update it from the badge/notification payload.

### Integration

- Pages that already call `refreshNavBadges()` ([jobs.html](src/linkedin_copilot/templates/jobs.html), [review_pulled.html](src/linkedin_copilot/templates/review_pulled.html)) require no change: the same fetch will update both badges and the notification area.

---

## 4️⃣ Edge Cases

- **Invalid or empty response**: If `GET /api/badges` fails or returns malformed JSON, fail silently: hide or leave the notification area empty and do not block the layout.
- **Network failure**: Same as today for badges: no retry required; the notification area remains hidden or in its previous state.
- **Concurrency**: No server-side state for this feature; counts are read-only, so there is no new race beyond normal DB reads.
- **Performance**: Any extra COUNT queries (e.g. for pending_scrape vs pending_match) are cheap; suggested_action is derived in memory. No N+1.
- **Both actions available**: The rule (prefer review_pulled when review_pending > 0, else process_pending) makes suggested_action deterministic.
- **Accessibility**: The notification area must be keyboard-focusable; the primary button must be in the tab order. Use `aria-live="polite"` if the content updates after load.

---

## 5️⃣ Implementation Steps

1. **Backend**
   - Optionally add `get_pending_scrape_count()` and `get_pending_match_count()` in [db.py](src/linkedin_copilot/db.py), or a single query returning both; otherwise keep using `get_pending_jobs_count()`.
   - In [web.py](src/linkedin_copilot/web.py), extend the `GET /api/badges` response with `pending_scrape`, `pending_match` (optional), and `suggested_action` (object or null). Implement the suggestion rule: review_pulled if review_pending > 0, else process_pending if jobs_pending > 0, else null.

2. **Database**
   - No schema change. Only new or reused COUNT queries.

3. **API**
   - Document the extended `GET /api/badges` response. No breaking change to existing keys. If a later story adds "new high matches," document any new query params (e.g. `?since=`).

4. **Frontend**
   - In [base.html](src/linkedin_copilot/templates/base.html): add HTML for the notification strip (message + primary button container), CSS using design tokens (strip hidden when no action). In the existing badge fetch callback, parse `suggested_action`; if present, set message, button label, and URL and show the strip; otherwise hide it. Ensure one primary CTA and accessible markup.

5. **Integration**
   - Ensure the refresh function runs on load and after relevant actions. Verify that the Jobs and Review pulled pages still trigger a refresh so the notification updates when the user returns to home or another page.

6. **Testing**
   - **Unit**: Badge endpoint returns `suggested_action` when jobs_pending > 0 or review_pending > 0; returns null when both are 0. Unit tests for the suggestion rule (review_pulled preferred over process_pending when both non-zero).
   - **API**: GET /api/badges with a seeded DB (pending jobs and/or staging rows) returns expected counts and suggested_action.
   - **Manual**: Load the app with pending work, confirm the notification appears; click the CTA and confirm navigation; after processing, confirm the notification disappears or updates.

---

## 6️⃣ Risks

- **Technical**: None significant. The feature builds on the existing badge endpoint and refresh pattern. An optional "new high matches" feature would require a definition of "since when" (e.g. last_visit) and can be added in a later story.
- **Scalability**: Not applicable for the current single-user design; COUNT queries remain cheap.
- **Maintainability**: Keep the suggestion rule in one place (backend); the frontend only renders what the API returns. Document the rule (review_pulled vs process_pending) in this epic and in code comments.
