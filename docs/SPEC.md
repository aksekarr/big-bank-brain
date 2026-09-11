# Big Bank Brain: MVP Specification

Owner: Avi (product owner). Builder: Codex in VS Code (AI coding agent).
Status: v1.1 draft. Items marked **[Proposed]** are recommendations awaiting Avi's sign-off.

Changes in v1.1: text articles from public websites only (no podcasts); "where banks
disagree" removed; private repo, Vercel hosting and `noindex`.

---

## 1. Summary

A daily AI-generated briefing on high-quality public analysis from credible institutional
financial organisations, including investment banks, asset managers, hedge funds,
private-market / private-equity firms and other established investment institutions.
It groups the material into themes, shows which institutions are covering each theme,
and links out to the originals.

### The 30-second test (primary success criterion)

A non-technical hiring manager opens the link on a phone and, within 30 seconds:

1. Understands what it is ("AI reads the banks' research so you don't").
2. Sees synthesis, not a list of links: a headline read of the week, and themes showing
   which institutions are saying what.
3. Sees it's live and current (freshness stamp).
4. Finds "How it's built" and understands a product person built it with an AI coding agent.

### Secondary success criteria

- Runs unattended for 30+ days with no manual fixes.
- Costs under £5/month.
- Never shows a blank page or an error.
- Holds up to a curious interviewer: accurate summaries, honest claims, sensible decisions.

---

## 2. Users

| User | Needs | Where they get it |
|---|---|---|
| Hiring manager (primary) | Instant "wow", credibility, loads fast on mobile | Main page, top banner |
| Interviewer (deep-dive) | How it works, why decisions were made, what Avi did vs the agent | "How it's built" page |
| Avi (owner) | Low maintenance, cost control, knows when it breaks | GitHub Actions, email alerts, run log |

---

## 3. Scope

### In the MVP

- 4 approved initial sources (4.1): text articles on public websites, collected daily
- AI extraction, synthesis and verification
- Main page (digest) and "How it's built" page
- Scheduled daily run, owner-only manual re-run
- All edge states: quiet day, partial sources, stale, first run
- Budget guard, dedup, last-good-digest fallback

### Out of the MVP

Podcasts, audio and video; any comparison of where institutions disagree; Notion push;
email newsletter; accounts; search; archive browsing; charts over time; any feature that
makes live API calls from the site. See backlog, section 15.

---

## 4. Sources

### 4.1 Initial MVP selection and candidate universe

The candidate universe includes investment banks, asset managers, hedge funds,
private-market / private-equity firms and other established investment institutions.
Record source statuses and research findings in `docs/SOURCES.md`.
Select the MVP sources based on credibility, analytical quality, publishing frequency,
technical accessibility and acceptable usage terms, rather than requiring particular
prestigious banks. The terms-of-use gate in 4.3 applies to every candidate.

Avi has reviewed the Phase 0 findings and approved the initial four-source MVP set:
BBVA Research, ABN AMRO Group Economics, Federal Reserve Bank of New York / Liberty
Street Economics, and Bank for International Settlements (BIS). Bank of America Institute
remains Candidate while its applicable terms are unresolved. Approval preserves all
feasibility evidence, terms classifications and caveats recorded in `docs/SOURCES.md`.

Liberty Street Economics and BIS, initially investigated as fallbacks, are now selected
for the MVP. Other central banks and major multilateral financial/economic institutions
remain fallback candidates; any broader editorial change remains Avi's decision.

**[Proposed]** Include at least one non-US institution for a broader range of themes.

### 4.2 Discovery method, in order of preference

Text articles on the institution's public website only.

1. **Site RSS/Atom feed** for articles, where one exists.
2. **The public insights listing page or sitemap.xml**, where robots.txt allows it:
   read it for new article links and dates.
3. **Search API (Brave)**, restricted to the institution's insights section, for sources
   where 1 and 2 don't work.

If no approved source needs search, leave it out of v1 (one fewer key, no attribution
requirement, no card on file).

### 4.3 Terms-of-use gate (mandatory)

Rule: if a source's terms **explicitly** prohibit reuse or AI processing of its content,
it's excluded. Before any source is approved, Avi reviews its website terms and robots.txt
and records the verdict in `docs/SOURCES.md`.

J.P. Morgan is the one to check closely: its research podcasts carry an explicit clause
against using its research material in third-party AI systems. Podcasts are out of scope,
so what matters is whether the website's own terms say the same.

### 4.4 Source registry: `config/sources.json`

```json
{
  "id": "gs-insights",
  "institution": "Goldman Sachs",
  "shortName": "GS",
  "name": "Goldman Sachs Insights",
  "type": "rss",
  "discoveryUrl": "https://...",
  "homepage": "https://...",
  "status": "candidate",
  "fetchFullText": false,
  "maxItemsPerRun": 10,
  "terms": { "reviewedOn": "2026-09-12", "verdict": "ok", "note": "" },
  "robots": { "checkedOn": "2026-09-12", "verdict": "allowed", "note": "" }
}
```

`type`: `rss | listing | sitemap | search`. `status`: `candidate | approved | excluded`.
READ may fetch full public article text transiently where an approved source permits
it, robots.txt allows access and the page is cleanly accessible. Otherwise use the
public RSS/listing excerpt. Raw text must never be archived or committed. The
`fetchFullText` registry field remains an implementation proposal; this approval does
not settle the final registry or extraction mechanism. The GitHub runner access test
remains pending separately from this local spike.

---

## 5. Pipeline

Runs once a day in GitHub Actions. Also runnable locally (`--dry-run` uses fixtures).

```
COLLECT -> DEDUPE -> READ (extract) -> SYNTHESISE -> VERIFY -> VALIDATE -> PUBLISH
```

### 5.1 Collect

- For each approved source: discover items published in the last 7 days (4.2).
- Polite fetcher: honest User-Agent (`BigBankBrain/1.0 (+<site>/how-its-built)`),
  robots.txt checked per host per run (our UA, then `*`), Crawl-delay honoured,
  minimum 3s between requests to the same host, 15s timeout.
- robots.txt 404 = allowed. robots.txt unreachable (5xx/timeout) = treat as disallowed
  for this run.
- 401/403/429/challenge page = source marked `skipped` for this run, reason logged.
  No evasion of any kind.
- READ uses permitted, cleanly accessible public article text transiently, with public
  feed/listing excerpts as fallback (4.4). Raw HTML and article text stay in memory
  and are never archived or committed. Extraction library and input limits remain
  implementation proposals.

### 5.2 Dedupe

- Canonicalise URLs (lowercase host, strip tracking params and trailing slash).
- Article ID = `a_` + first 12 chars of SHA-1 of the canonical URL.
- `data/seen.json` maps ID to `firstSeenAt`. Items already seen are skipped.
- If an item has no publish date, use `firstSeenAt` and set `dateEstimated: true`.

### 5.3 Rolling window **[Confirmed]**

A single day usually brings only a handful of pieces, which is too little to build
themes from. So:

- **"New since yesterday"** = material newly discovered since the previous daily run.
- **Synthesis** runs over the rolling **last 7 days** of items and flags what's new.
- **Initial live AI run:** consider only publications from the latest 7 days, not
  every item discovered with blank dedupe state.
- **[Proposed] Quiet day** (no new items): skip the model calls entirely, keep the current digest,
  update only `checkedAt`. Cost that day: £0.

---

## 6. AI design

Three separate AI steps, initially using **one API provider and one capable,
cost-conscious model**: Avi approved **OpenAI Responses API, `gpt-5.6-terra`,
with strict structured outputs**. Initially use this model family for extraction,
later synthesis and later verification; no multi-model routing. A separate AI verification
pass and deterministic code validation are confirmed. JSON schemas, validation
library (including the earlier zod proposal) and detailed retry/drop rules remain
implementation proposals.

### 6.1 Read (per article)

Input: permitted article text and metadata (full public HTML text or feed/listing
text where appropriate). Keep deterministic title, source, URL and publication date
outside the generated semantic extraction. The article-level spike returns `summary`,
`topics`, `claims`, `geographies`, `markets_or_asset_classes` and nullable `time_horizon`
through strict structured outputs. Unsupported lists stay empty and unstated horizons
stay null. This proves extraction, not a final ontology or semantic verification.

The earlier detailed output/topic proposal below remains a future design reference:

```json
{
  "summary": "Own words, max 60 words, no quotes over 10 words.",
  "topics": ["rates-central-banks", "equities-us"],
  "claims": [
    { "topic": "rates-central-banks",
      "statement": "Expects the Fed to cut twice before year end.",
      "horizon": "6-12m" }
  ]
}
```

**Controlled topic list** (lets code group claims across institutions):
`rates-central-banks, inflation, growth-recession, equities-us, equities-europe,
equities-em-asia, bonds-credit, fx, commodities-energy, ai-tech, geopolitics-trade,
private-markets, real-estate, digital-assets, other`.

### 6.2 Synthesise (over the confirmed 7-day window)

Use the approved `gpt-5.6-terra` model family, as for READ and VERIFY.

Input: all extracted items in the window (summaries + claims, not raw text), with
`isNew` flags. Code pre-groups claims by topic before the call. Output:

```json
{
  "headline": "Max 12 words: the week's big picture.",
  "dek": "One or two sentences expanding the headline.",
  "themes": [
    { "id": "t1", "title": "...", "summary": "Max 50 words.",
      "institutions": ["GS", "MS", "BLK"], "citations": ["a_..."] }
  ]
}
```

Prompt rules: attribute views to institutions ("Goldman expects..."); plain English;
3-5 themes, ordered by how many institutions cover them; no investment advice or
recommendations; don't frame themes as conflicts between institutions.

### 6.3 Verify (separate AI pass, confirmed)

Check candidate synthesis against extracted source claims using the evaluated
presupposition-aware implementation in `scripts/verify_presuppositions.py`.
`scripts/verify_candidate.py` is the canonical manual MVP entry point;
`scripts/verify_synthesis.py` remains unchanged as a historical comparator.
Preserve per-target issues and `presupposition_audit` for review.

A verifier FAIL blocks candidate progression toward publication. A PASS makes the
candidate eligible for human review only; it is not publication approval. Invalid
responses and failures preserve the previous passing review candidate. Human approval
and publication are not implemented yet; no unattended publication gate is implied.

This is the banking "four-eyes" principle applied to AI output. Say so on the
"How it's built" page.

### 6.4 Validate (code, no AI)

- Every citation ID exists in the window.
- Institutions listed on a theme match the institutions of its citations.
- Word limits respected. Topics are from the controlled list.
- Fail = retry the model step once, then drop the item (or keep last good digest if the
  whole synthesis fails).

---

## 7. Data files

The repo is private, but anything rendered on the site is visible to anyone with the link.

```
data/
  seen.json                   dedup index: id -> firstSeenAt
  items/YYYY-MM-DD.json       extracted items first seen that day
  digests/YYYY-MM-DD.json     digest published that day
  latest.json                 the digest the site renders (replaced only on success)
  runs.jsonl                  one line per run: timings, sources ok/skipped, items, tokens, cost, outcome
```

`latest.json` shape:

```json
{
  "digestDate": "2026-09-12",
  "generatedAt": "2026-09-12T06:19:44Z",
  "checkedAt": "2026-09-12T06:19:44Z",
  "window": { "from": "2026-09-05", "to": "2026-09-12" },
  "stats": { "institutions": 5, "itemsInWindow": 23, "newSinceLastRun": 4,
             "sourcesChecked": 5, "sourcesSkipped": 0 },
  "headline": "...", "dek": "...",
  "themes": [],
  "newItemIds": [],
  "items": { "a_...": { "institution": "GS", "title": "...", "url": "...",
                        "publishedAt": "...", "summary": "...", "topics": [] } },
  "run": { "models": { "read": "...", "synthesise": "...", "verify": "..." },
           "costUsd": 0.06 }
}
```

The git history of `data/` doubles as an audit trail of every published digest.

---

## 8. Website

### 8.1 Main page (mobile order, top to bottom)

1. **Portfolio strip** (thin, dismissible): "A portfolio project by Avi Ravisekara: an AI
   pipeline that reads the banks so you don't. How it's built →"
2. **Header**: "Big Bank Brain" + "How it's built" link.
3. **Freshness line**: "Updated 07:19, Sat 12 Sep · 5 institutions · 23 pieces this week".
   Local Europe/London time, computed from `generatedAt`.
4. **Headline + dek**: the AI's read of the week.
5. **Themes**: 3-5 cards. Title, coverage label ("4 of 5 institutions"), institution
   chips, summary, expandable "Sources" list linking to the originals.
6. **New since yesterday**: headline, institution, date, one-line summary, external link.
7. **Footer**: disclaimers (8.5), list of sources with links, "Built by Avi" with LinkedIn,
   search attribution if Brave is used.

### 8.2 "How it's built" page

1. The problem, in two sentences.
2. Pipeline diagram (static SVG): Collect → Read → Synthesise → Check → Publish,
   one plain-English line under each.
3. **Today's run**: sources checked/skipped, pieces read, models used, cost of the run
   ("Today's run cost 6p"). Pulled from `latest.json`.
4. Product decisions: one line each + one-line why (from `docs/DECISIONS.md`).
5. What I deliberately left out, and why.
6. What's next.
7. How I built it: Avi wrote the requirements, made the product calls, reviewed and
   tested; Codex (an AI coding agent) wrote the code. Honest and specific.

Copy guidance: call it "an automated AI pipeline with a built-in checker". Don't call it
an "autonomous agent". An interviewer who knows the difference will respect the precision.

### 8.3 States (all must be designed and viewable in dev)

| State | Trigger | Behaviour |
|---|---|---|
| Normal | Successful run with new items | Full page |
| Quiet day | Run succeeded, 0 new items | Banner: "No new publications since [day]. Here's the week so far." |
| Partial | 1+ sources skipped | Small note: "4 of 5 sources checked today." |
| Stale | `checkedAt` > 36h old (computed in browser) | Banner: "Updates paused. Showing the digest from [date]." |
| First run | No `latest.json` yet | Never ships: launch requires a 7-day backfill (Phase 5) |

Build a dev-only page (excluded from production) that renders every state from fixture files.

### 8.4 Visual direction **[Proposed]**

"Morning briefing" editorial style: reads like a serious financial newsletter, with
restrained data accents.

- Warm off-white background, near-black text, one accent colour. Dark mode via
  `prefers-color-scheme`.
- Serif display face for headlines, clean sans for body, tabular numerals for stats.
  Self-hosted, max 2 weights each.
- No stock imagery, no bank logos, no bank brand colours. Institution chips are neutral
  with text labels.
- Generous spacing, 16px+ body text, tap targets 44px+.

### 8.5 Disclaimers (footer, every page)

"Independent project, not affiliated with or endorsed by any institution named.
AI-generated summaries of publicly available material; they may contain errors.
Not investment advice. Always read the original." Plus a contact address for
removal requests.

### 8.6 Performance, discoverability and technical

- Lighthouse mobile 95+ performance and accessibility; total page weight under 200KB.
- `noindex, nofollow` meta tag and a `robots.txt` disallowing all crawlers: the site is
  shared by link, not found by search.
- Open Graph meta tags with a static branded share image, so the link previews well when
  pasted into email, Teams or LinkedIn messages.
- Favicon and a sensible `<title>`.

---

## 9. Automation and operations

### 9.1 Workflows (private GitHub repo)

`daily.yml`
- `schedule: cron: "17 6 * * *"` (07:17 BST in summer, 06:17 GMT in winter; odd minute
  because top-of-hour slots are congested and start late).
- `workflow_dispatch` with a `dry_run` input: the owner-only manual re-run.
- `concurrency` group so two runs can never overlap. `timeout-minutes: 15`.
- Steps: checkout → install → run pipeline → if data changed, commit `data/`.
  The commit triggers Vercel to rebuild and deploy the site.
- Least-privilege `permissions` (contents write only).

`ci.yml`: on push/PR run typecheck, tests, build. No secrets, no API calls.

Hosting: Vercel's free tier, connected to the private repo. Cloudflare Pages is an
equivalent alternative. (GitHub Pages on a free account needs the repo to be public.)

### 9.2 Failure handling

- Pipeline step fails → workflow fails → GitHub emails Avi → no data commit → no deploy →
  the previous site stays live unchanged. The browser-side stale banner covers
  prolonged outages.
- Each source fails independently. One blocked site never fails the run.

---

## 10. Cost and budget

The hard daily spend cap remains confirmed. OpenAI Responses and `gpt-5.6-terra`
are approved; current pricing and explicit per-spike limits must be reviewed before
separately authorising live AI calls. Earlier Haiku/Sonnet routing and
price estimates are superseded by the single-provider, single-model starting point;
a reliable cost estimate remains pending. Initial AI backfill covers only 7 days.

Search (if used) stays inside Brave's monthly free credit.
GitHub Actions minutes for a private repo sit well inside the free allowance
(one ~5-minute run a day). Vercel free tier: £0.

Guards (env/config):
- `DAILY_BUDGET_USD=0.50`: estimate before each call, record actual after. When the cap
  is hit, stop processing new items and publish with what's done.
- `MAX_NEW_ITEMS_PER_RUN=25`, `MAX_SEARCHES_PER_RUN=10`, `MAX_INPUT_CHARS_PER_ARTICLE=24000`.
- **[Proposed]** A provider-side monthly spend limit as an outer backstop, if supported
  by the provider Avi selects.

---

## 11. Security

- Secrets: `OPENAI_API_KEY` for the approved OpenAI provider,
  `BRAVE_API_KEY` (only if search is used). GitHub Secrets
  in CI, gitignored `.env` locally, `.env.example` committed with names only.
- Private repo. Never log request headers or keys. Scrub errors before logging.
- Pin GitHub Actions to major versions at minimum.
- Recommended Claude Code guardrail, enforced rather than advisory: add deny rules in
  `.claude/settings.json` so the agent can't read `.env` (check the current permissions
  syntax in the Claude Code docs).

---

## 12. Content and compliance posture

A sensible posture for a portfolio project, not legal advice:

- Text articles on public websites only. No logins, paywalls, client portals, gated
  research, podcasts, audio or video.
- Sources whose terms explicitly prohibit reuse or AI processing are excluded (4.3).
- Short summaries in our own words, attribution, and a prominent link to the original.
- No full text stored or published. No logos. No implied affiliation.
- Removal-request contact on every page, honoured promptly.
- Data minimisation: store only what the page needs.

---

## 13. Delivery plan

Each phase ends with Avi's review. Codex plans first, then builds.

### Phase 0: Source feasibility (research, minimal code)
- For every candidate logged in `docs/SOURCES.md` under the scope in 4.1: find the discovery route (4.2), record the robots.txt verdict
  for relevant paths, summarise the terms of use (flag any explicit reuse/AI clause for
  Avi's decision), and note how often it published text articles in the last 14 days.
- Fetch test **from a GitHub Actions runner** via a manual workflow (runner IPs are
  treated differently from home broadband). Record HTTP status only; save no content.
- Output: `docs/SOURCES.md` table and `config/sources.json` with statuses.
- **Exit:** Avi approves 4-5 sources producing 10+ articles/week combined.
- **Source selection confirmed:** Avi has approved the four sources in 4.1; their
  recorded 14-day counts indicate at least 33 pieces combined (over 10/week on average).
  This records source selection, not completion of the remaining Phase 0 work. The
  GitHub Actions runner fetch test and `config/sources.json` remain pending.

### Phase 1: Collect and store (no AI)
- Project scaffold, discovery adapters, polite fetcher, dedupe, run log, fixtures, `--dry-run`.
- **Accept:** second consecutive run finds 0 new items; a simulated 403 source is skipped
  without failing the run; nothing raw is written to disk; tests pass.

### Phase 2: AI layer
- Read, synthesise, verify, validate, budget guard. Tests use recorded fixture responses,
  never live calls.
- **Accept:** valid digest produced from fixtures; bad citations rejected; budget cap stops
  processing mid-run; one live run costs under $0.25; Avi spot-checks 5 summaries against
  the originals and signs off on quality.

### Phase 3: Website
- Main page, "How it's built", all states (8.3), dev states page, disclaimers, meta tags.
- **Accept:** Lighthouse mobile 95+ perf and a11y; looks right at 375px; every state
  renders from fixtures; passes Avi's 30-second test on his own phone.

### Phase 4: Automation and deploy
- `daily.yml`, `ci.yml`, Vercel connection, manual run, concurrency, failure behaviour.
- **Accept:** 3 consecutive scheduled runs succeed; a forced failure leaves the live site
  unchanged; stale banner appears when simulated.

### Phase 5: Polish and launch
- 7-day backfill so the first visit is full; share image; copy review; README;
  optional custom domain and cookieless analytics.
- **Launch checklist:** 7 clean days in a row · tested on iPhone and Android ·
  links all open originals · disclaimers present · `noindex` confirmed · no secrets in
  repo history · Console spend limit set · "How it's built" reviewed by Avi ·
  CV link updated.

---

## 14. Open decisions: recommendations

| Decision | Recommendation | Status |
|---|---|---|
| Stack | TypeScript end to end: Astro + Tailwind site, tsx pipeline | [Proposed] |
| Hosting | Private GitHub repo, Vercel free tier, `noindex` | [Proposed] |
| Visual direction | Editorial "morning briefing" (8.4) | [Proposed] |
| Notion push | v1.1, via Notion's REST API reusing your existing API layer (MCP is for agents calling tools interactively, not a scheduled job) | [Proposed] |
| Search API | Fallback only, for sources without a feed or readable listing page | [Proposed] |
| Provider and model | OpenAI Responses API, `gpt-5.6-terra`, strict structured outputs; same model family initially across extraction, synthesis and verification | Confirmed |
| Digest window | Rolling 7 days, new material since the previous daily run identified; first live AI run limited to 7 days | Confirmed |
| Custom domain | Optional, ~£10/year, looks better on a CV | Avi to decide |
| Analytics | Optional cookieless (e.g. Vercel Web Analytics or GoatCounter): tells you if the CV link gets clicked | Avi to decide |

---

## 15. Backlog (post-MVP)

- Push each digest to Notion
- Daily auto-generated share image with the headline
- Archive pages for past digests
- Topic pages ("What the banks think about rates, over time")
- Podcast show notes as an extra source type
- Weekly email version
- More institutions
- Explicitly not planned: any "ask the digest" chat on the site. It would reintroduce live,
  visitor-triggered API costs.
