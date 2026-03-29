# Getting Started Page

The **Getting started** page is the secondary, guide-style page that explains how LinkedIn Copilot works. It is not the main landing page.

## Role

- **Home** (`/`) is the **pipeline dashboard** — the main landing with stage counts, next action, and top jobs.
- **Getting started** (`/getting-started`) is the **how-it-works guide** — for new users or anyone who wants a step-by-step overview.

## Content

- **Hero**: "Guide" badge, title "Getting started", subtitle "How LinkedIn Copilot works…".
- **How it works**: Five steps with icons and left accent bar:
  1. Set up your profile → link to Profile
  2. Discover jobs → Search, Careers
  3. Review pulled jobs → Review pulled
  4. Match & track → Jobs list
  5. Apply → Go to Jobs
- **CTA strip**: "Go to Home" (primary), "Set up profile", "Search jobs" (secondary).

## Navigation

- Sidebar: **Home** → `/`, **Getting started** → `/getting-started`.
- `/dashboard` redirects to `/` so old links and bookmarks still work.

## Template

- `src/linkedin_copilot/templates/getting_started.html` — extends base, uses design tokens, responsive.
- Legacy `home.html` remains in the repo but is unused; routing uses `getting_started.html` for `GET /getting-started`.

## When to update

- If you add or reorder product steps, update the steps list and CTAs in `getting_started.html`.
- If you rename "Getting started" or add another guide page, update this doc and HANDOVER.md.
