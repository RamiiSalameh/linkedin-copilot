## Search Experience v3 – Unified Engine

The Search page now uses one unified engine for LinkedIn discovery:

- User-entered queries and AI suggestions live in one selectable pool.
- The final batch is built from: user queries + selected AI suggestions.
- One primary run action executes the combined batch across selected locations.
- Careers remains a separate standalone flow (`CAREERS_FLOW.md`).

## Core UX Model

1. **Single search surface**
   - `Keywords` input (with static autocomplete).
   - `Location` input (comma-separated locations supported).
   - Shared filters (Easy Apply, date posted, experience, remote, job type).

2. **Unified query pool**
   - User can add manual queries from the keyword input.
   - AI suggestions are loaded inline and pre-selected.
   - Clicking AI chips toggles include/exclude state.
   - Refresh regenerates suggestion set with variation.

3. **Batch execution**
   - CTA runs selected queries across selected locations.
   - Backend uses one JSON endpoint for unified batch execution.

## Suggestion Sources

AI suggestions are generated from multiple inputs:

- CV/resume text
- Applied jobs (titles from DB)
- Search history effectiveness
- Optional web-market snippets (Tavily)

Fallback is supported when web or LLM data is unavailable.

## API Mapping

- `GET /api/search/suggestions`
  - Returns cached suggestions.
- `POST /api/search/suggestions/refresh`
  - Forces regeneration with variation.
- `GET /api/search/autocomplete`
  - Static suggestions from profile + search history.
- `POST /api/search/run-batch`
  - Unified JSON payload:
    - `queries: string[]`
    - `locations: string[]`
    - `filters: object`
    - `anonymous_search: boolean`

Legacy routes remain for compatibility:

- `/api/generate-searches` proxies to unified suggestions.
- `/batch-search-generated` proxies into unified batch behavior.

## Notes

- Explore mode (`/api/explore/*`) stays available and unchanged.
- Careers scraping and review pipeline stays unchanged and separate.

