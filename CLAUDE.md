# Big Bank Brain

A static web app that publishes a daily AI-written digest of what the big investment banks
and asset managers publish on their public websites: the main themes, which institutions
are saying what, and links out to the originals. It's a portfolio piece shared via a CV
link, not promoted publicly. The audience is non-technical hiring managers who will judge
it in ~30 seconds, often on a phone.

- Full spec: `docs/SPEC.md` (read the relevant section before planning any phase)
- Product decision log: `docs/DECISIONS.md` (feeds the "How it's built" page)
- Source research and approvals: `docs/SOURCES.md` (created in Phase 0)

## How we work

- Avi is the product owner, not a developer. He makes product calls. You make
  implementation calls and explain trade-offs in plain English, no jargon without a gloss.
- Start every phase in plan mode. Propose a plan that maps to the phase's acceptance
  criteria in the spec, and wait for approval before writing code.
- One phase at a time. Small commits with clear messages. Commit before risky changes.
- If the spec is ambiguous or a product trade-off comes up, stop and ask. Give 2-3 options
  and a recommendation. Never silently decide user-facing behaviour.
- When a product decision is made, append it to `docs/DECISIONS.md` (one line, one-line why).
- Never say something works without running it. Run typecheck, tests and build first.
- End each task with: what changed, how Avi can check it himself, what's next.
- Don't run the live pipeline (real API calls) without asking. Use `--dry-run` + fixtures.

## Commands

(Fill in as they're created in Phase 1. Keep this list accurate.)

- `npm run dev` - site locally
- `npm run build` - production build
- `npm test` - unit tests (no network, no API calls)
- `npm run typecheck`
- `npm run pipeline -- --dry-run` - full pipeline on fixtures, no network
- `npm run pipeline` - live run (costs money; ask first)

## Non-negotiable rules

Security
- API keys live only in `.env` (gitignored) locally and GitHub Secrets in CI.
  Never print, log or commit them. Keep `.env.example` up to date with names only.
- The website makes zero runtime API calls. No keys, no fetches to paid APIs, no refresh
  button. It only reads files produced by the pipeline.

Cost
- Every model and search call goes through the budget guard (`DAILY_BUDGET_USD`,
  `MAX_NEW_ITEMS_PER_RUN`, `MAX_SEARCHES_PER_RUN`). No unbounded loops or retries.
  One retry max per call, then skip the item.

Content and ethics
- Sources are text articles on institutions' public websites only. No podcasts, audio,
  video, logins, paywalls or client portals.
- Only fetch sources with `status: "approved"` in `config/sources.json`. A source whose
  terms explicitly prohibit reuse or AI processing of its content is never approved.
- Respect robots.txt. Identify honestly with our own User-Agent. One request at a time per
  host with a delay. Never use headless browsers, spoofed user agents, proxies, CAPTCHA
  solving, or retries designed to get round a block. On 401/403/429 or a challenge page,
  mark the source as skipped for this run and move on.
- Never commit raw article text or page HTML. Store only: headline, date, link, a summary
  in our own words (max 60 words), and structured claims. No verbatim quotes longer than
  10 words. No bank logos anywhere.

AI output integrity
- Every theme and claim cites article IDs. Code validates that the IDs exist and that the
  institutions named match the cited articles.
- The verify step checks each theme against its sources. Unsupported themes are dropped.
- There is no "where banks disagree" feature. Don't add one or frame themes as conflicts.

Reliability
- The site must never be blank or show an error. `data/latest.json` is only replaced after
  validation and verification pass. Any failure leaves the last good digest live.

## Stack

- TypeScript (strict) everywhere, current Node LTS.
- Site: Astro, static output, Tailwind. Minimal client JS (native `<details>` for
  expanders; one tiny inline script for "updated X hours ago" and the stale banner).
- Pipeline: plain TypeScript run with `tsx`. `@anthropic-ai/sdk`, `zod` for every
  model output and data file, `vitest` for tests.
- Scheduling: GitHub Actions in a private repo. Hosting: Vercel, auto-deploying on each
  data commit. Site is `noindex` (shared by link, not by search).
- Models are set in config, never hardcoded. Defaults: extract and verify on
  `claude-haiku-4-5`, synthesis on `claude-sonnet-5`.

## Conventions

- Mobile-first. Test layouts at 375px wide before desktop.
- Performance budget: Lighthouse mobile 95+ on performance and accessibility.
- Comments explain *why*, only where the logic isn't obvious.
- Tests are required for: dedup, budget guard, citation validation, robots handling,
  blocked-source handling, and "failure keeps last good digest".
- Model outputs are validated with zod. Invalid output = retry once, then drop the item.

## Gotchas

- GitHub cron runs in UTC and often starts late. Always display the *actual* run time,
  converted to Europe/London. Never hardcode "07:00". UK clocks change twice a year.
- The repo is private but the site isn't: anyone with the link can see it. Treat every
  page as published.
- The site is a static build: time-sensitive UI (staleness) must be computed in the browser.
