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
