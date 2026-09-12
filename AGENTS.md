# Big Bank Brain: working instructions for Codex

> **Live-run and backend freeze:** See the non-negotiable rule in **Commands and verification**. Do not run live scripts or modify generated briefing data without Avi's specific approval for that run in that task.

Big Bank Brain is a static website with a daily AI-written digest of high-quality
public institutional financial analysis. It groups themes, attributes views and links
to originals. It is a CV portfolio project for non-technical hiring managers, often
viewed on a phone and judged within 30 seconds. Implementation is with Codex in VS Code.

## Project references and decision status

- Read the relevant sections of `docs/SPEC.md` before planning or implementing a task.
- Read `docs/DECISIONS.md` for confirmed product decisions and unresolved proposals.
- Read `docs/SOURCES.md` before source-related work. Current row statuses and dated
  approval notes supersede earlier pending-selection wording; retain the evidence.
- `CLAUDE.md` is historical/reference material. Do not change it unless Avi requests it,
  or treat its old stack and provider assumptions as current Codex requirements.
- Avi's explicit decisions govern product scope. Do not promote Proposed items to
  Confirmed or treat unreviewed implementation assumptions as approved architecture.
  An implementation detail appearing in `SPEC.md` or historical `CLAUDE.md` is not,
  by itself, evidence that Avi approved it. This includes providers, models, libraries,
  frameworks, hosting, retry design and performance implementation details.
- Codex is the coding agent, not a choice of model provider for the product. Claude
  model names, the Anthropic SDK/key, Claude Console and Claude-specific permission
  settings in older documents remain unreviewed. Do not adopt them or automatically
  replace them with OpenAI equivalents. Stack, libraries, models and hosting proposals
  require Avi's decision before dependent implementation.

## How we work

Follow: **product decision → small controlled implementation task → inspect changes →
test → continue**.

- Avi is the product owner and is not a developer. He owns product decisions. Make
  routine implementation choices within the agreed scope and explain choices and
  trade-offs in plain English; explain technical terms when needed.
- At the start of a phase, propose a short plan tied to the specification's acceptance
  criteria. Obtain Avi's agreement before implementing that phase. An explicitly
  authorised small task can proceed without asking for the same permission again.
- Work on one bounded task at a time. Keep changes small, inspect the diff, test, and
  give Avi a clear review point before proceeding to a new task or phase. Avoid unrelated
  refactors, new features and speculative scaffolding.
- If a specification ambiguity affects user-facing behaviour or creates a product
  trade-off, explain the options and recommend one; ask Avi before implementing that
  choice. Continue independent work already authorised where possible.
- Record Avi's new product decisions in `docs/DECISIONS.md` with a concise reason when
  the task permits that file to change. Do not infer decisions from agent suggestions.
- Respect each task's file and action limits. Do not initialise Git or commit without
  authorisation. When commits are authorised, keep them small and clearly described;
  agree a safe checkpoint before risky changes and preserve existing user work.
- End implementation tasks with what changed, what was tested and the result, how Avi
  can check it himself, and what comes next. State remaining limitations plainly.

## Commands and verification

No application commands have been established yet. The npm commands in `CLAUDE.md`
are placeholders. Check actual project scripts as they are introduced; document only
commands that exist. Do not install packages or create configuration merely to fill
this section or to validate a documentation-only change.

- Never claim something works without testing it. For implementation changes, run the
  relevant tests and available typecheck/build checks before reporting success. If a
  check cannot run, say why and do not call it a pass.
- For documentation-only tasks, inspect the text and diff, verify the requested scope
  and unchanged material, and report that validation rather than an application test.
- **NON-NEGOTIABLE: backend frozen and live runs prohibited by default.** Use fixtures
  and a verified offline dry-run path for development. Tests must not silently make
  network requests or paid calls. Never run `scripts/interpret_briefing.py`,
  `scripts/synthesise.py`, `scripts/verify_synthesis.py`, or any other script with a
  `--live` flag under any circumstances unless Avi has explicitly asked for that
  specific run in that specific task. `synthesis-result.json`, `interpretation.json`
  and `corrections.json` must never be modified or regenerated. A layout, styling or
  markup task never requires running any Python script.
- Required behaviour tests as those components are implemented: deduplication, budget
  cap enforcement, citation integrity, robots handling, blocked-source handling and
  preserving the last good digest on failure. Test the agreed behaviour; these test
  expectations do not select a guard architecture, verification step or test framework.

## Security and cost

- Never print, log or commit secrets, request credentials or raw request headers.
  Keep keys out of site code. GitHub Secrets is the confirmed location for CI API keys;
  this does not select hosting or a model provider. Local `.env` storage and an
  `.env.example` are implementation proposals, not instructions to create files;
  any example must contain names only, never secret values.
- The website makes zero runtime API calls and only reads pipeline-produced files.
  Visitors cannot trigger paid calls; no public refresh button.
- Enforce the confirmed hard daily spend cap; paid work must not escape cost control.
  Exact environment-variable names, numerical caps, item/search/input limits, budget
  accounting and guard architecture remain unresolved for later architecture review.
  The spec's one-retry rule, retry/failure routing and model-configuration mechanism
  are also proposals, not mandates. Any eventual retry design must respect the spend
  cap and no-bypass rules. Do not adopt unreviewed models or assume old prices are current.

## Sources, content and access

- Avi approved BBVA Research, ABN AMRO Group Economics, New York Fed / Liberty Street
  Economics and BIS for the initial MVP. Bank of America Institute remains Candidate.
  Only Avi changes source approvals. Approval preserves all recorded caveats and terms
  classifications; ambiguity alone is not exclusion, and explicit reuse/AI prohibitions
  must not be overridden by technical accessibility.
- Production collection must use only approved sources and reflect Avi's recorded
  approvals. The proposed `config/sources.json` registry, schema and enforcement
  mechanism await an authorised implementation task and architecture review; do not
  create them merely because they appear in the spec. Separately authorised Phase 0
  investigation of candidate sources is not production collection.
- Use public text articles only: no podcasts, audio, video, logins, paywalls, gated
  research or client portals. Attribute the institution and link directly to originals.
- Respect robots.txt and applicable path-specific restrictions, identify honestly and
  access sources politely. Honour publisher-imposed access conditions. The spec's
  three-second minimum delay, 15-second timeout, per-host concurrency and detailed
  robots error handling are unresolved implementation details, not approved defaults.
  Do not treat uncertainty about access permission as permission to bypass restrictions.
  Never use headless browsers, spoofed agents, proxies, CAPTCHA solving or retries to
  evade a block. If blocked or challenged, stop that access attempt and record why.
  Approval does not remove source-access caveats or prove a runner fetch test succeeded.
  Runner testing and the full-text-fetch mechanism await their implementation review.
- Raw article text and page HTML must not be archived, written to disk or committed.
  Hold permitted input temporarily in memory. Retain only the specified metadata,
  own-word summaries (maximum 60 words), structured claims and links. No verbatim quote
  longer than 10 words; no bank logos or implied affiliation.

## Output integrity, reliability and presentation

- Preserve citation integrity: published claims and themes must be supported by their
  sources and accurately attributed. Do not publish unsupported claims. Article-ID
  schemas, structured-output validation and the specific checking mechanism remain
  implementation proposals. In particular, the second AI verification pass / four-eyes
  model step is still Proposed; do not require or implement it without Avi's decision.
- No “where banks disagree” feature or framing themes as conflicts. Keep summaries
  accurate, in plain English, and free of investment advice or recommendations.
- Failure must leave the last good digest live; the site must never be blank or show
  an error. The publication checks, storage layout (including `data/latest.json`) and
  replacement mechanism await architecture review. Publishing is not contingent on
  adopting the Proposed second AI verification pass.
- Keep the experience usable, fast and accessible on phones. The spec's 375px test
  width, Lighthouse 95+ targets and performance techniques remain proposals for review,
  not newly confirmed requirements. Comments explain why where the reason is not obvious.
- Display freshness honestly using actual run times in Europe/London, accounting for
  daylight saving and scheduling delays; do not substitute a scheduled time for a real
  run time. Browser-side staleness calculations and other implementation techniques
  remain proposals. Treat every rendered page as public even if the code repository
  is private; this does not settle hosting, framework or repository-visibility choices.
