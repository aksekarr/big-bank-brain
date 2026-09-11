# BBVA discovery spike

From the project folder, run:

```sh
python3 scripts/discover_bbva.py
```

Requires only the existing Python 3 and `curl`; no package installation. Each run
checks BBVA's robots.txt and fetches the initial English publications listing afresh.
It overwrites root-level `bbva-discovery.json` only after successful parsing.

The output contains the institution, fetch timestamp, listing URL, scope and unique
publication records with `title`, `publication_date` and `url`. `listing_summary` is
included only when the card contains a standfirst. It is publisher-provided discovery
metadata, not an AI-generated summary. URLs are public permalinks supplied by the
listing; article-level canonical tags are not fetched. Raw HTML stays in memory.

Recent means the cards visible in the initial response, sorted by publication date.
There is no date-window filter or assumption that the page always contains ten items.
No articles, pagination, other sources, models, paid APIs, scheduling or cross-day
deduplication are implemented. This Python/curl spike does not choose the MVP stack.

For this spike, requests have a 20-second timeout, a 2 MB response limit and at least
three seconds between robots and listing requests, extended by publisher crawl rules.
These are local implementation choices, not confirmed pipeline architecture. Non-200
responses (including redirects or missing robots.txt), denied access, invalid cards
and empty results stop the run without replacing the previous output. No retries,
proxies or access-control bypasses are used. If BBVA changes its HTML, review the
parser rather than interpreting a failure as a quiet publishing day.

Offline checks (synthetic HTML and mocked network access):

```sh
python3 -B -m unittest discover -s tests -p 'test_discover_bbva.py' -v
```

The generated JSON is review output, not a source registry or published digest. Do
not confuse listing standfirsts with the product's future own-word summaries.

## Additional approved-source discovery spikes

Run each independently from the project folder:

```sh
python3 scripts/discover_abn_amro.py
python3 scripts/discover_nyfed_lse.py
python3 scripts/discover_bis.py
```

| Collector | Official discovery endpoint | Local output |
|---|---|---|
| ABN AMRO Group Economics | https://www.abnamro.com/research/en/overview/our-research | `abn-amro-discovery.json` |
| Liberty Street Economics | https://libertystreeteconomics.newyorkfed.org/feed/ | `nyfed-lse-discovery.json` |
| BIS and FSI publications | https://www.bis.org/doclist/bis_fsi_publs.rss | `bis-discovery.json` |

These use existing Python 3 standard-library modules and curl. No installation is
needed. `discovery_common.py` shares request/access checks, basic metadata checks
and atomic JSON writing; the three source parsers remain separate. The BBVA script
is unchanged. This is a discovery spike, not a decision about the product stack.

Each command first checks that host's robots.txt, then retrieves only the named
listing/feed. ABN's HTML parser reads its publication cards, headings, displayed
absolute dates and standfirst paragraphs; it never calls the prohibited APIs.
LSE's selective XML parser reads RSS title/link/pubDate/description only. Its feed
also transports full-content fields: their text is ignored, never extracted or
saved, and never used as an excerpt fallback. BIS reads RSS 1.0 fields with their
namespaces and Dublin Core dates, ignoring additional nested links to documents.
No article, image, PDF or pagination links are fetched. Raw responses stay in memory.

Each JSON item records `institution`, `source_name`, `title`, `url` and a UTC
`discovered_at` timestamp, plus `publication_date` (YYYY-MM-DD) and `excerpt` when
supplied. Absent optional fields are omitted, not guessed. Dates retain the calendar
day supplied by the publisher. Excerpts are publisher discovery metadata, with HTML
markup removed; they are not generated summaries or material approved for website
publication. URLs come from the listing/feed, with fragments removed; article-level
canonical tags are not fetched. Duplicate URLs collapse within the run; conflicting
duplicate metadata fails validation.

Recent means the initial ABN listing or all entries in the current RSS response.
There is no seven-day filter, pagination, editorial filtering, cross-day deduplication,
scheduling, article processing or model call. The feed window can extend well beyond
two weeks. Approval and the existing terms caveats in `docs/SOURCES.md` are unchanged.

For these three scripts, local spike limits are 30 seconds and 8 MB per response
(the LSE feed includes unused content fields), with at least three seconds between
robots and discovery requests, extended by robots crawl-delay/request-rate rules.
Only HTTP 200 is accepted; there are no redirects, retries, proxies or browser
impersonation. Missing/unrecognisable robots rules, access failures, wrong response
types, empty/malformed results, unexpected URLs and invalid supplied dates stop the
run. Relative ABN dates are rejected rather than inferred. Publisher format changes
need review; a failed parse is not treated as a quiet day.

JSON is written to a temporary file and atomically replaces the previous snapshot
only after successful collection/validation/serialization. Failed runs exit nonzero
and preserve the previous successful file. All four live snapshots and Python cache
files are ignored by Git. There is no raw-response archive.

Run all offline checks, including BBVA:

```sh
python3 -B -m unittest discover -s tests -v
```

The additional tests use invented HTML/XML and mocked access to check parsing,
missing optional metadata, invalid data, duplicates/conflicts, access denials,
request scope, ignored content fields and preservation of previous output.

## Local DEDUPE spike

After the four discovery snapshots exist, run:

```sh
python3 scripts/dedupe.py
```

This command makes no network or model calls. It reads all four root-level discovery
JSON files, validates their structure, counts, source attribution, URLs, timestamps
and supplied publication dates, then compares their URLs with `dedupe-seen.json`.
A missing discovery input or malformed existing seen-state fails the entire run;
corrupt state is never silently reset. Missing optional dates remain missing.

`dedupe-new.json` contains only this run's new publications, with institution, source,
title, normalised URL, discovery timestamp and supplied date/excerpt. BBVA's listing
summary is mapped to `excerpt`; no new summary is generated. Duplicate URLs retain
the first encountered metadata. An updated headline at an already-seen URL is not
considered a new publication.

URL identity lowercases the host, removes the default HTTPS port, fragments, `utm_*`
parameters and recognised click/email tracking keys (`gclid`, `dclid`, `fbclid`,
`msclkid`, `mc_cid`, `mc_eid`). Other query components retain their original encoding
and order. Path case and trailing slashes are preserved because equivalence is not
established. There are no redirect lookups, article canonical-tag requests, hashes,
article IDs or content similarity matching. Distinct URLs for the same article can
therefore remain distinct unless they differ only by the recognised URL noise.

On the first successful run, all current publications are new. Seen-state maps each
normalised URL to its first-seen timestamp. An unchanged second run writes an empty
new-item list. It does not erase the seen URLs. Both files are generated, local and
Git-ignored; discovery snapshots are never modified. The current output is replaced
each run, so after the second-run check it intentionally contains zero items.

All inputs are validated and both JSON files fully prepared before replacement.
New-item output is replaced first; seen-state is atomically replaced last. If writing
output or committing state fails, the old seen-state remains intact (or absent on
first run). A failure between the two replacements can leave new output alongside
old state; rerunning emits those items again rather than losing them. This is not a
two-file database transaction or a power-loss durability guarantee. A non-blocking
local directory lock rejects overlapping dedupe runs; this standard-library locking
choice supports the current macOS/Unix environment, not Windows.

Here “seen” means successfully emitted by this discovery/dedupe spike, **not** read,
summarised or published. Downstream acknowledgements/retries, production persistence,
scheduling, state expiry, extraction, AI processing and deployment remain unbuilt.
No production model, framework or hosting choice is implied.

Run the complete offline suite with the command above:
`python3 -B -m unittest discover -s tests -v`.
The synthetic dedupe tests cover first/second runs, one new item, URL equivalence,
malformed inputs/state, write failures, retry recovery and overlapping runs.

## BBVA READ feasibility (no AI)

```sh
python3 -B scripts/read_bbva.py
```

Reads the existing `bbva-discovery.json`, validates it and selects up to three distinct
URLs in descending publication-date order. It does not rediscover the listing, change
dedupe state or implement the future AI seven-day backfill filter. If the snapshot is
old, the selected articles are simply the newest available in that snapshot.

Checks BBVA robots.txt before requesting article paths. Requests use an honest READ
feasibility user agent, a 20-second timeout, a 2 MB response limit and at least three
seconds between requests, extended by publisher crawl rules. Only HTTP 200 HTML is
accepted. No redirects, retries, PDFs, images, scripts or other linked assets are fetched.
Maximum three article requests per invocation. A fetch/access failure or recognised
challenge stops remaining article requests; denied paths are not fetched. Existing
listing excerpts provide fallback without a new request. Missing/invalid robots also
means excerpt-only fallback. Nothing bypasses access controls.

The standard-library parser selects `article_publicacionDetalle` → `detalle_tabs_publi`
and extracts only `detalle_text_intro` and `lista_puntosClave` content. Navigation,
authors, tags, downloads, menus, hidden content and footer text are excluded. It
requires a closed article and well-formed selected content, tolerating unclosed outer
layout divs observed in BBVA's page. Minimal word/character checks require meaningful
key-point text beyond the introduction; failures use the listing excerpt where present.
These are spike heuristics, not semantic verification or a completeness guarantee.

**The tested HTML pages supply a summary and key points, not the complete linked PDF
report.** Diagnostics label that distinction. A general-purpose extraction library
would not recover text absent from the HTML. PDF/report extraction remains unbuilt.

Only safe JSON diagnostics are printed: discovery title/URL, success flags, text
source, character/approximate word counts and fixed failure reasons. Counts describe
the extracted HTML text, or the listing excerpt when fallback is flagged. No raw HTML,
article text or generated summaries are printed or written. Everything is held in
memory and released when the process exits; this is not a secure memory-erasure claim.
The script creates no output file. Use `-B` as shown to suppress Python bytecode caches.
Exit 0 means all selected pages passed extraction; exit 1 means invalid input or at
least one fallback/failure, even if a usable listing excerpt exists.

Live acceptance on 2026-09-10 used exactly three article requests: Argentina inflation
(775 characters / approximately 124 words), Europe ECB (635 / 106), Türkiye CBRT
(1,496 / 235). All three isolated HTML summary/key-point text without listing fallback.
The first response was inspected and reused within one in-memory Python session;
the other two were fetched through the spike. The session then exited, discarding
its responses. No extra request was made to repeat the first page.

Offline tests use invented HTML to cover extraction boundaries, malformed/empty input,
listing fallback, access failures, the request cap and absence of raw output/file I/O.
Run the complete suite with `python3 -B -m unittest discover -s tests -v`.

Before any AI integration: select a provider/model with Avi, implement cost control,
seven-day input eligibility and processing-state handling, and assess extraction
coverage and retained source-use caveats. The new product decisions are documented
in `docs/DECISIONS.md` and `docs/SPEC.md`; older contrary proposal wording in historical
instructions does not override Avi's explicit approvals. Other sources' READ adapters,
production schemas, scheduling and deployment remain outside this spike.

## Eligibility and successful-processing state

Build a local ready list without fetching anything or calling AI:

```sh
python3 -B scripts/processing.py prepare
```

This uses the four current discovery snapshots and the existing dedupe validator and
URL normalisation. It deliberately revisits discovered-but-unprocessed items even
when `dedupe-new.json` is empty. Discovery's seen-state is not a record of successful
READ/AI processing. `ready-for-processing.json` holds the selected metadata, preserving
source attribution and URLs; `processed-state.json` maps successfully processed URLs
to their first successful-processing timestamps. Both generated files are ignored.

Only after a downstream task has actually succeeded, acknowledge its URL:

```sh
python3 -B scripts/processing.py record --url 'PUBLICATION_URL' --status succeeded
```

Use `--status failed` or `--status incomplete` for unsuccessful attempts; these never
mark an item as processed. This command records a caller's result, not evidence that
AI work happened. No real items were marked successful during this spike. Successful
acknowledgements require membership in the ready output; repeated acknowledgements
are harmless. Run `prepare` again to refresh the ready file after acknowledgements.

State replacement is atomic, and a failed state write preserves its previous contents.
Malformed state is rejected, never reset. The current macOS/Unix directory lock prevents
overlapping commands; this is local state, not a production queue or database. Only
successfully processed URLs are recorded; attempts, retries, model versions, production
storage, AI processing and scheduling remain unbuilt. Items that disappear from all
current discovery snapshots are not retained in a separate backlog.

The eligibility window is **today plus the previous six calendar dates in
Europe/London**, inclusive. On 10 September it includes 4–10 September. The clock
uses the actual run time and London daylight-saving rules. Older, future and missing
dates are excluded and counted in the output; malformed supplied dates fail the run
before output/state replacement. Publication dates are never estimated. Preparing a
list does not mark anything processed; an unchanged second preparation returns the
same items until success is explicitly acknowledged. First preparation creates an
empty processed-state file. Existing processed state is never changed by preparation.

## BBVA article-level AI extraction (SDK installed; live approval pending)

Safe, dependency-free metadata plan (does not fetch articles or call OpenAI):

```sh
python3 -B scripts/extract_bbva.py
```

**After Avi separately approves paid execution**, the live command will be:

```sh
.venv/bin/python -B scripts/extract_bbva.py --live --limit 3
```

Use `--limit 2` for two articles. It refreshes eligibility through the existing
processing workflow, takes the newest eligible/unprocessed BBVA items only, and reuses
`read_bbva.run` with an in-memory text consumer. Existing READ diagnostics remain the
default; no second HTTP fetcher or HTML parser was introduced. READ failure or listing
fallback causes no AI request and no processed acknowledgement in this proof. HTML
introduction/key-points coverage remains distinct from a complete report.

The official `openai` Python SDK **2.48.0** is installed in the Git-ignored `.venv`
using the existing Python 3.9.6. `requirements-ai.txt` pins the SDK and its resolved
transitive dependencies; no new dependency-management framework was added. To recreate
this environment on a compatible Python installation:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-ai.txt
```

Run the complete offline suite (including installed-SDK tests):

```sh
.venv/bin/python -B -m unittest discover -s tests -v
```

The SDK's documented
`client.responses.create` with `text.format.type=json_schema` and `strict=True` sends
the schema; the local validator checks the same small schema using the standard library.
No standalone schema-validation package or Pydantic-specific model layer is needed.
Installed-SDK request serialization and response/refusal parsing pass tests using an
in-memory HTTP transport with socket access blocked. Real API acceptance, account
access and model output quality remain untested; paid execution still needs approval.

Configure the key in **your own VS Code zsh terminal**, never in Codex chat or a source
file. Obtain your API key privately from the OpenAI platform's API-key settings, then:

```sh
read -rs 'OPENAI_API_KEY?OpenAI API key: '
export OPENAI_API_KEY
```

Paste at the hidden prompt and press Enter. The key itself is not part of shell
command history and applies to this terminal session and its child processes. Do not
use `echo`, `env` or debugging output to display it. After running, use
`unset OPENAI_API_KEY`. No `.env` file or dotenv loader is needed. Existing `.env*`
ignore rules remain in place. SDK debug logging (`OPENAI_LOG`) must be unset.

The reviewed-for-installation configuration is `gpt-5.6-terra`, reasoning `low`,
standard (`default`) service tier, no tools, no retries, a 60-second SDK timeout,
`max_output_tokens=2000`, and `store=False`. The fixed API endpoint is
`https://api.openai.com/v1`; environment proxies, HTTP redirects and SDK debug logging
are disabled. Unsupported model/configuration errors stop; no alternative is selected.
The model sees only supplied article-page text and useful title/institution/date context.
Known metadata is retained by code, outside the model's semantic response.

Semantic fields are `summary` (at most 60 words), `topics`, `claims`, `geographies`,
`markets_or_asset_classes`, and nullable `time_horizon`. Lists may be empty where
unsupported; at least one substantive claim is required for a successful proof.
The prompt requires supported values, paraphrasing and no invented horizon. Local
checks reject extra/missing fields, wrong types, bounds violations, overlong summaries
and more than ten consecutive source words. These checks do not prove factual support;
separate semantic verification and broader deterministic publication validation are
still later tasks. Nothing here publishes model output.

Only after READ, a completed/non-refused response, schema validation and safe output
persistence succeed is `processing.acknowledge(..., 'succeeded', ...)` called. Failed
articles stay unprocessed. Validated semantic output goes to ignored
`bbva-ai-extractions.json`, alongside code-supplied metadata. No raw HTML, article text,
request payload, response envelope or reasoning is saved or printed. An output-save
success followed by a state-write failure leaves a saved extraction but an unprocessed
item; another attempt may consume another allowance slot. Recovery beyond this tiny
spike requires review, not an automatic retry loop.

An ignored `bbva-ai-spike-ledger.json` reserves each attempt **before** the paid call.
The maximum is **three actual model calls across invocations**. HTTP 401 authentication
and HTTP 429 quota/rate-limit rejections without a model result release their slot and
budget reservation, while remaining in the attempt audit history. A returned Responses
API result consumes a slot even if refused, incomplete or later rejected by validation.
Timeouts, unknown failures and interrupted requests retain a pending reservation until
review because model execution is uncertain. There are no automatic retries.
The ledger has no automatic reset. Do not delete it to get more calls without Avi's
review. A directory lock serializes this command with local processing-state changes.
The local reservation budget is $0.15 for the entire spike, stricter than a resettable
daily allowance; three maximum-size reservations total $0.13968. This is a bounded
spike guard, not the production cost-accounting implementation.

Pricing checked 2026-09-11: [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
lists $2/M standard input and $12/M output tokens, with cache writes at 1.25x uncached
input pricing. Reserve at $2.50/M input to cover that possibility. Each serialized
request is limited to 8,000 UTF-8 bytes; use one input token per byte plus a 1,024-token
framing allowance as a conservative estimate, and assume all 2,000 output tokens are
used (including reasoning). That gives $0.04656 per call: **$0.09312 for two or
$0.13968 for three**, before tax. This is an estimate, not a measured token count or
a billing guarantee; the framing allowance and current pricing should be rechecked
if the API changes. Oversized input is rejected, not silently truncated.

The current plan selects the previously tested Argentina inflation, Europe ECB and
Türkiye CBRT pages. Their last observed body lengths were 775, 635 and 1,496 characters.
Using those lengths gives approximately 3,026, 2,864 and 3,734 serialized request bytes
(including prompt/schema/metadata, approximating source characters as ASCII), or about
$0.068 for two/$0.104 for three under the conservative calculation above. Fresh body
sizes have not been fetched this task; live size checks enforce the larger bound.

References: [strict structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Responses request limits](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
[official SDK](https://developers.openai.com/api/docs/libraries).
`store=False` disables stored Responses application state; it is not a promise of
zero provider retention. OpenAI's applicable [data controls](https://developers.openai.com/api/docs/guides/your-data)
still apply, as do the recorded BBVA source-use caveats.

Tests use only fakes and synthetic HTML. SDK installation was subsequently approved
and completed; no publisher fetch or real OpenAI call was made during this step. Synthesis, verification, frontend,
scheduling and other-source AI processing remain unbuilt.

## ABN AMRO READ feasibility (no AI execution)

Run the source-specific, memory-only reader:

```sh
.venv/bin/python -B scripts/read_abn_amro.py --limit 3
```

It validates local discovery metadata, selects up to three eligible unprocessed ABN
items using the confirmed London calendar window, checks robots, then fetches only
public article HTML. It does not rewrite eligibility or processing state. The observed
ABN article panels supply introduction and body paragraphs; navigation, author cards,
related articles, scripts, figures and tables are excluded. No PDF, chart image,
restricted API, browser, redirect or retry is used. A failed access attempt stops
remaining article requests. Unavailable or insufficient HTML uses the existing
listing excerpt, labelled explicitly; this cannot recover information from charts
or linked reports. Existing ABN terms caveats in SOURCES.md remain applicable.

The optional `run(items, on_text=...)` callback receives deterministic metadata,
transient text and its provenance. The command prints only diagnostics and never
calls an AI model, saves raw content or marks items processed. No new dependencies.

Live READ inspection on 2026-09-11:
- Global economic forecasts as of 7 Sept 2026: insufficient recognised article body;
  listing fallback, 219 characters / approximately 32 words.
- Spotlight - US Midterms: HTML introduction/body, 3,739 characters / 585 words.
- Spotlight - A perfect storm for food inflation?: HTML introduction/body,
  7,283 characters / 1,120 words.

The existing semantic schema remains suitable; no second schema was created.
The callback is now connected by the separate ABN extraction CLI described below.
The existing 8,000-byte complete-request cap remains in force; longer ABN prose can
exceed it once prompt/schema overhead is included. Such input stops before a paid
request rather than being truncated or silently replaced with a listing excerpt.

Offline tests use synthetic HTML and mocked transport. Run the complete suite with
`.venv/bin/python -B -m unittest discover -s tests -v`.


## ABN AMRO article extraction (offline-tested; paid approval pending)

Metadata-only preview (no network, no generated-file writes):

```sh
.venv/bin/python -B scripts/extract_abn_amro.py --limit 1
```

After separate approval, exactly one eligible/unprocessed article can be attempted:

```sh
.venv/bin/python -B scripts/extract_abn_amro.py --live --limit 1
```

Only limit 1 is accepted. This reuses the ABN reader, BBVA's exact six-field schema,
validator, Responses client and corrected call accounting. Small keyword parameters
let ABN supply source-specific instructions and its own ledger filename; BBVA defaults
remain unchanged. No new package, provider, schema, synthesis or verification step.

Successful output is written to ignored `abn-amro-ai-extractions.json`, keyed by URL.
Each record keeps institution, source name, title, URL, publication date, model and
extraction time outside `extraction`. A deterministic `read_metadata` object records
`text_source`, `listing_fallback_used`, character/approximate word counts and that
charts, tables and linked reports were not extracted. The model receives source-depth
context, but does not regenerate these metadata fields. Thin listing excerpts remain
eligible; extraction can still fail if no supported valid result is returned.

Only validated, successfully saved semantics are acknowledged in processed state.
Refusal, invalid structure, oversized input and persistence failures leave the item
unprocessed. No raw article text, HTML or request/response envelope is persisted.
Live preparation refreshes the shared ready-list; the default preview does not.

The fresh, separate `abn-amro-ai-spike-ledger.json` allows at most three actual model
calls across invocations. An absent ledger represents zero attempts, not an error.
401/429 rejections remain audited but release reservations; returned results consume
a slot even if validation fails; uncertain outcomes retain pending reservations.
There are no retries or resets. The completed BBVA ledger is never read or changed
by ABN call accounting. ABN retains the existing $0.15 reservation budget, $0.04656
per held call, 8,000-byte request cap and 2,000-output-token cap. These are conservative
reservations, not measured billing. Current local state: no ABN paid calls made.

The current first selection is the forecasts item dated 8 September. Its previous
READ test used a 219-character / approximately 32-word listing fallback; this is
historical diagnostic evidence, not a fresh page fetch by the metadata preview.

For a deliberate one-item quality test, add the exact discovered URL. It is matched
against all currently eligible, unprocessed ABN items before limiting selection;
unknown, old, future, missing-date or already-processed items cannot bypass eligibility.
Without this option the normal newest-item selection is unchanged, including thin
listing excerpts.

```sh
.venv/bin/python -B scripts/extract_abn_amro.py --live --limit 1 --url 'https://www.abnamro.com/research/en/our-research/spotlight-us-midterms-trump-can-veto-democrats-cannot'
```

Omit `--live` for a metadata-only preview. The option does not alter READ, validation,
storage, successful-processing acknowledgement or ABN's separate three-call budget.
For this article, the previously measured 3,739 characters give an estimated
6,167-byte complete request using ASCII placeholder text with the actual metadata,
prompt and schema. Actual UTF-8 characters and JSON escaping may increase this:
no raw text was retained to reproduce its exact byte count. The live size guard
still checks the actual complete payload against 8,000 bytes before any API request.

## BIS READ feasibility (no AI extraction)

```sh
.venv/bin/python -B scripts/read_bis.py --limit 3
```

Uses existing discovery validation and processed state without writing either. Selects
up to three currently eligible unprocessed BIS/FSI items in the confirmed London
seven-date window. On 2026-09-11 only two snapshot items qualify; older items are
not brought forward merely to fill the sample.

Observed BIS publication HTML exposes `div.text__component` inside `article`.
The source-specific parser captures its paragraphs, lists and section headings,
excluding navigation, related/author cards outside the component, hidden text,
scripts, figures, tables and footer material. Recognised components must close
correctly and provide at least 200 characters and 35 paragraph words. This is a
structural feasibility check, not semantic verification or a complete-paper claim.
If HTML is unavailable, denied or unsuitable, the already-discovered RSS excerpt
is used with explicit provenance; absent excerpts fail with no text. No extra feed
fetch, API, PDF or linked-file request is made.

Robots is checked before pages; requests identify the project, allow no redirects
or retries, and stop further page access after failure/challenge. The existing
20-second/2 MB READ guard and polite minimum three-second interval are retained.
These are spike details, not newly selected production architecture. Disallowed
BIS file paths, including `/sites/default/files/` and `/p/`, are never followed.

Live inspection on 2026-09-11:
- FSI Paper 28, “When machines attack: frontier AI cyber threats and policy responses
  in the financial sector”, 2026-09-09: HTML publication prose, 1,790 characters /
  approximately 230 words; no fallback.
- Working Paper 1376, “What determines banks' excess demand for reserves?”,
  2026-09-07: headed HTML research sections, 3,379 characters / approximately
  513 words; no fallback.

Both appear substantial enough for later semantic extraction of the available
page-level research summary, not every result in the full paper. RSS descriptions
are shorter; the reserves RSS excerpt visibly ends with an ellipsis and concatenates
its opening heading with prose. It is preserved as supplied rather than repaired
with invented content. No silent truncation is applied by READ.

Diagnostics retain title, URL, publication date, institution, source, text provenance,
fallback flag, character/word counts and `complete_paper_extracted: false`.
The optional callback receives metadata, text and provenance transiently; no raw
HTML or article text is saved, and no item is marked processed. The reader itself makes no AI request; the separate extraction CLI below uses the
existing semantic schema.

The existing six-field schema appears suitable for policy/financial research; empty
asset/geography lists and a null time horizon remain valid when unsupported.
Recommend the reserves working paper as the eventual first one-item AI test: its
513-word research summary offers more depth than the FSI landing prose while being
moderate in size. Any eventual complete-payload limit still needs checking with the
actual BIS prompt/schema; READ counts are not token counts. Existing BIS terms,
non-commercial-use, attribution and limited-reuse caveats remain documented in
`docs/SOURCES.md`.

Tests use synthetic HTML and mocked transport for content isolation, malformed pages,
access failures, RSS fallback, transient callbacks and no state writes. Full suite:
`.venv/bin/python -B -m unittest discover -s tests -v`.


## BIS structured extraction (offline-tested; live test not yet authorised)

Metadata-only exact-URL preview:

```sh
.venv/bin/python -B scripts/extract_bis.py --limit 1 --url 'https://www.bis.org/publications/working-paper-1376-what-determines-banks-excess-demand-reserves'
```

After Avi authorises the single paid test, the intended command is:

```sh
.venv/bin/python -B scripts/extract_bis.py --live --limit 1 --url 'https://www.bis.org/publications/working-paper-1376-what-determines-banks-excess-demand-reserves'
```

Only one item per invocation is accepted. Exact URLs must match an eligible,
unprocessed BIS discovery item; they cannot bypass the London seven-day window.
Without a URL the latest eligible unprocessed BIS item is selected. The metadata
preview makes no network requests or state writes.

This source-specific wrapper reuses the existing Responses client, exact six-field
schema, validation/copy checks and budget helpers without changing BBVA or ABN.
BIS instructions identify publication-page summaries/RSS excerpts as incomplete
paper coverage and prohibit filling unsupported fields from outside knowledge.
It retains Terra, low reasoning, strict output, no tools, no retries and store=False.

Validated semantics are stored in ignored `bis-ai-extractions.json`, keyed by URL.
Title, URL, publication date, institution/source, model and extraction timestamp stay
outside the semantic fields. Deterministic `read_metadata` includes text provenance,
RSS fallback status, character/word counts, `complete_paper_extracted: false` and
the absence of chart/table/linked-report extraction. Raw HTML/text is never saved.
Only successful validation followed by output persistence permits a processed-state
acknowledgement; selection, READ, failure or refusal alone never marks an item processed.

The separate ignored `bis-ai-spike-ledger.json` retains the three-model-call spike
limit and $0.15 reservation budget. It is created on the first attempted call; absence
means zero used. HTTP 401/429 rejected requests release reservations but stay audited;
returned results consume slots even on refusal/validation failure; uncertain outcomes
remain held pending review. BBVA and ABN ledgers are not used or changed.

The reserves candidate's earlier 3,379-character READ size yields an estimated
5,833-byte complete request using ASCII placeholder text and the actual prompt,
schema and metadata. UTF-8 and JSON escaping can increase this; exact source bytes
were not retained. The unchanged 8,000-byte actual-request guard stops before a call
on oversized input, without truncation. Live size/quality remain unproven for BIS.
No publisher fetch or paid API call was made during this extraction implementation.

## Liberty Street Economics READ feasibility (no AI extraction)

```sh
.venv/bin/python -B scripts/read_nyfed_lse.py --limit 3
```

The command selects only currently eligible, unprocessed Liberty Street items from
local discovery metadata, using the existing London seven-calendar-date window.
It writes neither ready output nor processed state. An empty eligible set returns
empty diagnostics without requesting robots or articles.

On 2026-09-11, the saved snapshot and a fresh official RSS check both had zero
eligible posts (window 5–11 September). The newest posts were dated 1–3 September.
For structural feasibility only, these three older posts were passed directly to
the reader; no eligibility exception or historical-processing CLI was introduced.
No currently eligible article could be live-tested.

The observed article-specific container is `div.ts-article-text` within `main`.
The parser includes paragraphs, section headings and lists, excluding author bylines/
biographies, citation/disclaimer blocks, related content, sharing controls, navigation,
forms, newsletter modules, footer material, scripts, figures, tables and explicitly
marked footnote blocks. It requires one closed, well-formed body container and
substantive paragraph text. Existing size/timeout/polite-access limits are retained,
robots is checked, and access failure/challenge stops subsequent article requests.
No redirected, gated, PDF, chart or table content is retrieved.

Historical structural samples, all HTML with no RSS fallback:
- 3 September, Jackson Hole: Exploring the Financial Frontier:
  7,995 characters / approximately 1,209 words.
- 2 September, Are Central Banks Moving Out of Dollar Assets?:
  9,964 characters / approximately 1,436 words.
- 1 September, Businesses Are Using AI to Transform Work, Not Cut Jobs:
  7,164 characters / approximately 1,141 words.

All three share the same core container with varying paragraph lengths and page
modules. They appear substantive enough for later extraction, but charts/figures,
linked material and excluded footnotes are not captured. In-body methodology prose
is retained when it uses the selected text elements; completeness of special boxes
is not guaranteed. No silent truncation. Existing RSS excerpts are used only when
HTML is denied, unavailable or structurally insufficient. The feed also exposes
content:encoded, but fetching/parsing that alternative was unnecessary once HTML
worked; no claim of equivalent content or tested structured full-text fallback.

Metadata and provenance/count diagnostics are separate from the transient callback
text. No raw HTML/text files, AI calls, schemas or processed acknowledgements are
created. The existing six semantic fields still fit research commentary; attribution
must respect that blog authors' views need not be official New York Fed positions.
The general access licence does not remove the blog-specific recurring-distribution/
archive caveat documented in SOURCES.md.

No sample is currently eligible for a live AI test. If separately approved as a
historical quality test, the businesses/AI post is the smallest of these samples and
offers a concrete empirical topic. All three would exceed the current 8,000-byte
complete AI-request cap after prompt/schema overhead; that guard has not been changed.
Prefer a new eligible, shorter post for the eventual first call, or seek explicit
review of size and historical-test constraints before proceeding.

Offline tests use synthetic HTML/mocked transport, including content isolation,
malformed pages, denied access, RSS fallback, deterministic metadata and no writes.
Run the full suite with `.venv/bin/python -B -m unittest discover -s tests -v`.

## Liberty Street extraction (offline-complete; live proof pending)

The subsequent sizing decision supersedes the earlier 8,000-byte limitation and
historical-test suggestion above: Liberty alone now permits a 16,000-byte complete
request. Historical samples remain diagnostics only, with no eligibility override.
BBVA/ABN/BIS remain at 8,000 bytes with their original reservations and budgets.

```sh
.venv/bin/python -B scripts/extract_nyfed_lse.py --limit 1
```

This default preview is metadata-only, with no network or state writes. Once a
naturally eligible unprocessed article exists and Avi authorises one paid call:

```sh
.venv/bin/python -B scripts/extract_nyfed_lse.py --live --limit 1
```

Optional `--url 'EXACT_DISCOVERED_URL'` selects a specific eligible, unprocessed post.
There is no force, historical or backfill exception. Only limit 1 is accepted.
The current preview selects nothing; Liberty live extraction is not yet proven.

The wrapper reuses the existing schema/client/validation and call accounting.
Small optional shared-helper arguments carry Liberty's request ceiling, reservation
and budget; defaults for other sources are unchanged. No schema, framework or chunking.
The source prompt preserves author attribution (“the authors find”, “the post argues”)
and explicitly rejects inferring an official New York Fed position from publication.
This is a prompt requirement, not a guarantee of semantic quality; live review remains.

The existing conservative formula yields:
(16,000 + 1,024) × $2.50/million + 2,000 × $12/million = **$0.06656 per call**.
Three reservations total **$0.19968**, within the **$0.20** Liberty spike ceiling.
These reuse the earlier pricing assumptions, not measured billing or new price research.
The 16,000-byte guard counts the existing full JSON serialization with UTF-8 encoding;
oversize input fails before sending, without truncation. No automatic retries.
401/429 rejections release slots but retain audit history; returned results consume
slots, and uncertain outcomes remain reserved until review.

Ignored runtime files are `nyfed-lse-ai-spike-ledger.json` and
`nyfed-lse-ai-extractions.json`. An absent ledger means zero attempts. Existing
source ledgers are neither migrated nor modified. Results keep the six semantic
fields separate from institution/source, title, URL, date, model/time and READ
provenance, fallback status, character/word counts and incomplete-content flags.
Raw publisher content stays in memory. Only validated, successfully stored semantics
permit a processed-state acknowledgement; discovery-seen is not AI-processed.

Offline tests include request/schema/attribution construction, exact size boundaries,
budget enforcement, response failures, no retries, eligibility, no raw persistence
and preserved existing-source defaults. No publisher fetch or OpenAI call occurred
during this extraction implementation.
