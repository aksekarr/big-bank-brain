# Big Bank Brain

Live site: [https://aksekarr.github.io/big-bank-brain/](https://aksekarr.github.io/big-bank-brain/)

Big Bank Brain is a site that presents an AI-generated briefing of public institutional financial research. It links readers to the publisher's original articles and shows the extracted claims behind briefing points. It is not investment advice.

## Briefing process

The pipeline starts with permitted public research, then uses AI to extract structured claims and source references. It synthesises those claims into a briefing, sends the result to an independent factual verification guardrail, and leaves it for human review. This separation is intended to make the evidence and the checks inspectable rather than treating generated text as self-validating.

The current briefing did not pass automated verification. The guardrail failed closed: it did not mark the briefing as verified, and the site labels the automated-verification stage **Held**. A verification pass would still require human review; it would not automatically approve publication.

## BBB's read

BBB's read is a separate AI interpretation layer built from the briefing's extracted claims. It is always labelled as an AI interpretation and is never attributed to an institution. It is distinct from the institution-attributed briefing content.

## Sources and corrections

There are four source adapters: BBVA Research, ABN AMRO Group Economics, New York Fed / Liberty Street Economics, and the Bank for International Settlements (BIS). The project uses only text articles from publicly permitted, approved sources; it does not use podcasts, audio, video, logins, paywalls, gated research or client portals.

Human-review corrections are recorded in `corrections.json`. At render time, the site applies those corrections to the displayed text and marks corrected content. It never changes the stored briefing or interpretation data files.

The illustrations on the site are AI-generated.

## Repository layout

- `index.html`, `styles.css`, `app.js`, and `site-config.js` render and configure the static site; `site-config.js` holds the presentation review status.
- `synthesis-result.json` stores the structured briefing; `interpretation.json` stores BBB's read; `corrections.json` stores display-time human-review corrections.
- `scripts/` contains source discovery, reading, extraction, synthesis, verification, and interpretation tools.
- `tests/` contains offline tests and fixtures.
- `docs/` contains the specification, product decisions, source record, and operating documentation.

For the controlled process for producing a new briefing, see [docs/WEEKLY-REFRESH.md](docs/WEEKLY-REFRESH.md). It specifies the required preflights, explicit approval for live AI calls, verification, and human review.
