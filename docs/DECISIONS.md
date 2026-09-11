# Product decisions

One line per decision, one line of why. This file feeds the "How it's built" page.
Claude Code appends new decisions here as they're made. Move items from Proposed to
Confirmed only when Avi signs off.

## Confirmed

| Date | Decision | Why |
|---|---|---|
| 2026-09-10 | Runs once a day on a schedule; visitors never trigger API calls | Costs stay fixed however many people click, and the page can't break mid-demo |
| 2026-09-10 | No public refresh button; manual re-runs are owner-only | A public button would recreate the cost and abuse risk |
| 2026-09-10 | Early-morning UK run | Catches the full previous US trading day before UK working hours |
| 2026-09-10 | API keys in GitHub Secrets only, never in site code | Keys in the browser would be stolen |
| 2026-09-10 | Hard daily spend cap in code | A bug should cost pennies, not pounds |
| 2026-09-10 | Only new pieces are processed each run (dedup) | No paying twice to read the same article |
| 2026-09-10 | Quiet days show the last digest with a note | An empty page looks broken |
| 2026-09-10 | A failed run leaves the last good digest live | Reliability matters more than freshness for a demo |
| 2026-09-10 | Text articles on public websites only; no podcasts, audio or video | Keeps v1 focused on one content type done well |
| 2026-09-10 | Respect robots.txt; no bypassing bot protection | Build on content we're allowed to reach, or don't use it |
| 2026-09-10 | Sources whose terms explicitly prohibit reuse or AI processing are excluded | A clear, checkable line on what we use |
| 2026-09-10 | Store headline, date, short summary and link; link out, never copy articles | Send readers to the source; respect the publisher's work |
| 2026-09-10 | Use RSS feeds where offered | The cleanest, most polite way to discover new content |
| 2026-09-10 | No "where banks disagree" section | Over short horizons the big houses are mostly aligned; a forced comparison would be thin or invented |
| 2026-09-10 | Broaden candidate sources to credible institutional financial organisations: investment banks, asset managers, hedge funds, private-market / private-equity firms and other established investment institutions. Central banks and major multilateral financial/economic institutions may be investigated as Phase 0 fallbacks; changing the primary editorial proposition remains Avi's decision | A broader universe lets the MVP select on credibility, analytical quality, publishing frequency, technical accessibility and acceptable usage terms, without forcing difficult-to-access prestigious banks into the product |
| 2026-09-10 | Avi approved BBVA Research, ABN AMRO Group Economics, Federal Reserve Bank of New York / Liberty Street Economics and BIS as the initial four-source MVP set; Bank of America Institute remains Candidate pending resolution of its applicable terms | Avi reviewed the Phase 0 feasibility findings; all documented caveats and terms classifications remain in force |
| 2026-09-10 | READ may transiently fetch full public article text where an approved source permits it and the page is cleanly accessible; otherwise use its public RSS/listing excerpt. Never archive or commit raw text | Improve input quality while respecting source access and reuse conditions |
| 2026-09-10 | Use a rolling 7-day synthesis window and identify material newly discovered since the previous daily run | Provide enough context for synthesis while showing what is new |
| 2026-09-10 | Start with one API provider and one capable, cost-conscious model across the AI steps; actual provider and model remain undecided | Prove quality and cost before adding separate cheap/strong model routing |
| 2026-09-10 | Retain a separate AI verification pass checking synthesis against extracted source claims, alongside deterministic code validation | Check source support as well as structural correctness before publication |
| 2026-09-10 | The first live AI-processing run considers only the latest 7 days, even when dedupe state is blank | Avoid processing an entire historical feed and incurring unnecessary cost |
| 2026-09-10 | Seven-day eligibility means today plus the previous six calendar dates in Europe/London, inclusive; exclude missing and future publication dates rather than estimating them | Date-only discovery metadata needs an explicit boundary without inventing publication times |
| 2026-09-11 | Use OpenAI Responses API with `gpt-5.6-terra` and strict structured outputs; initially retain this model family for extraction, later synthesis and separate verification | Avi selected the initial provider/model to prove article-level extraction without multi-model routing; installation and paid calls still require separate approval |
| 2026-09-11 | Use medium reasoning for the intended first live semantic-verification spike; offline request construction follows this setting, with no live call authorised yet | Verification needs sensitivity to subtle semantic mutations, conditional logic, attribution and support relationships; extraction settings remain unchanged |

## Proposed (awaiting Avi)

| Decision | Why |
|---|---|
| Discovery order: RSS feed, then listing page/sitemap, then search API | Free and reliable first; search adds cost, a key and attribution |
| Skip AI calls entirely on quiet days | No new input, no new cost |
| Themes ranked by how many institutions cover them | Shows at a glance where the market conversation is |
| Every theme cites its source articles, and code validates the citations | Trust, but verify in code rather than in the prompt |
| Private repo, `noindex` site | Shared by CV link, not found by search |
| No bank logos or brand colours | Avoid implying affiliation |
| Show each run's cost on the "How it's built" page | Makes cost-consciousness tangible |
