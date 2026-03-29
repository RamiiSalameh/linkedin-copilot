# Unified Search Engine Design

Date: 2026-03-24
Status: Approved

## Goal

Replace fragmented search modes with one unified search engine where AI suggestions are built in, selectable, and combinable with manual queries. Keep the Careers flow standalone.

## Scope

- Search tab redesign in `index.html` only
- New backend suggestion engine using:
  - CV
  - applied jobs
  - search history effectiveness
  - web context (Tavily)
- New search APIs for suggestions/autocomplete/batch run
- Backward-compatible proxy behavior for legacy endpoints
- Tests for new engine, web search client, and new batch API
- Knowledge base updates

## Out of Scope

- Careers flow implementation and UI in `careers/`
- Explore engine behavior and strategy internals
- Matching/scoring pipeline logic
- CLI search command behavior

## UX Design (Approved)

- One unified query pool:
  - user-added chips (`you`)
  - AI-suggested chips (`✨`)
- Selected chips are active; deselected chips remain visible
- `Refresh AI suggestions` regenerates with variation
- One primary CTA: `Run N searches`
- Optional quick single-search link remains available
- Location pills support multi-location batch targeting
- Filters remain available behind collapsed `⚙ Filters`
- Keywords autocomplete is static (profile + history), not AI

## Architecture

### New module

- `src/linkedin_copilot/search/web_search.py`
  - Async Tavily wrapper
  - Returns snippet list
  - Graceful fallback to `[]`
- `src/linkedin_copilot/search/suggestion_engine.py`
  - Context builder + LLM invocation + diversity controls + cache

### Prompt strategy

- Add `SUGGESTION_ENGINE_PROMPT` in `prompts.py`
- Enforce category mix quotas
- Inject `random_seed` for refresh variation
- Inject `banned_queries` to reduce repetition

### API design

- `GET /api/search/suggestions`
  - Returns cached suggestions if warm
  - Generates when cache is stale/missing
- `POST /api/search/suggestions/refresh`
  - Invalidates cache and regenerates
- `GET /api/search/autocomplete`
  - Returns static keyword suggestions
- `POST /api/search/run-batch`
  - Accepts JSON `{ queries, locations, filters, anonymous_search }`
  - Runs background batch search using selected queries

Legacy endpoints remain available and proxy into new behavior.

## Data Sources for Suggestions

1. CV text from uploaded resume path in web state
2. Applied jobs from `jobs` table (`status = applied`)
3. Search history effectiveness from existing exploration intelligence
4. Web snippets from Tavily searches

If any source fails, generation still proceeds with available sources.

## Configuration

- `TAVILY_API_KEY` (optional)
- `SUGGESTION_CACHE_TTL_MINUTES` (default 30)
- `SUGGESTION_COUNT` (default 14)

## Dependency

- `tavily-python`

## Verification Plan

1. Unit test `web_search.py`:
   - success path
   - missing API key
   - exception fallback
2. Unit test `suggestion_engine.py`:
   - context assembly
   - cache behavior
   - LLM output normalization
   - fallback to static strategies on LLM failure
3. API test `POST /api/search/run-batch`:
   - validation
   - accepted payload
   - status tracking
4. Extend explore-related coverage for fallback compatibility
5. Manual browser test:
   - suggestion load
   - refresh variation
   - add/remove chips
   - batch run + status updates

## Risks and Mitigations

- LLM repeated suggestions:
  - use `random_seed`, `banned_queries`, and category quotas
- Web search latency/cost:
  - low result count, cache, optional key fallback
- Endpoint compatibility:
  - keep old routes and proxy behavior
- UI complexity:
  - single pool, single CTA, progressive disclosure for filters

