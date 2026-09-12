# Weekly briefing refresh runbook

This is a manual runbook for a fresh weekly briefing. It does **not** authorise any
live call. The backend is currently frozen: before every command marked **PAID/LIVE**,
Avi must explicitly request that exact command for that exact run in the current task.
Do not reset a ledger, reuse an exhausted allowance, change a timestamp to evade a
guard, or retry unless the command's output and Avi's new approval explicitly permit it.

Run commands from the project root:

```sh
cd ~/Desktop/big-bank-brain-codex
```

Use `YYYY-MM-DD` below for the intended London end date, for example `2026-09-18`.
The live synthesis command always uses the actual current Europe/London seven-day
window (that day and the preceding six calendar days), not a manually supplied date.

1. **[SETUP — required before a paid call, but not billable]**

   ```sh
   source ~/bbb-live.sh
   ```

   This loads the OpenAI key into this terminal only; it spends nothing. Run it before
   any command with `--live`, and never paste or print the key.

   Use `python3` for discovery, eligibility and reader scripts: they use the standard
   library and `curl` only. Use `.venv/bin/python` for the extraction, synthesis,
   verification and interpretation tools, so their offline preflights and paid calls
   use the same virtual environment that supplies the OpenAI SDK.

2. **[FREE — public-web discovery, no AI]**

   ```sh
   python3 scripts/discover_bbva.py
   python3 scripts/discover_abn_amro.py
   python3 scripts/discover_nyfed_lse.py
   python3 scripts/discover_bis.py
   ```

   These four commands check access rules and collect the latest public listing or
   feed metadata for BBVA, ABN AMRO, Liberty Street Economics and BIS.

3. **[FREE/OFFLINE]**

   ```sh
   python3 scripts/dedupe.py
   python3 -B scripts/processing.py prepare
   ```

   These commands remove already-seen URLs, then make the rolling seven-day list of
   eligible, unprocessed articles; stop if either command reports invalid input/state.

4. **[FREE — public-web reading, no AI]**

   ```sh
   python3 -B scripts/read_bbva.py
   python3 -B scripts/read_abn_amro.py --limit 3
   python3 -B scripts/read_bis.py --limit 3
   python3 -B scripts/read_nyfed_lse.py --limit 3
   ```

   These are source-specific READ diagnostics: they check permitted public article
   pages and report whether usable text is available without saving raw article text.

5. **[FREE/OFFLINE — extraction preflight]**

   ```sh
   .venv/bin/python -B scripts/extract_bbva.py --limit 3
   .venv/bin/python -B scripts/extract_abn_amro.py --limit 1
   .venv/bin/python -B scripts/extract_bis.py --limit 1
   .venv/bin/python -B scripts/extract_nyfed_lse.py --limit 1
   ```

   These show the eligible articles and remaining source-specific call capacity; do
   not continue if a selected article, request-size check or ledger is blocked. The
   limits are caps, not this week's article counts: use the `selected` entries in this
   preflight output and `ready-for-processing.json` to choose them. BBVA accepts 1,
   2 or 3, so use no more than the eligible articles and paid calls Avi has explicitly
   approved. ABN AMRO, BIS and Liberty Street currently accept only `--limit 1`; run
   one approved article at a time even when more are eligible. The reader commands'
   `--limit 3` values are likewise their maximum, not a claim that three articles exist.

6. **[PAID/LIVE — AI extraction; each command needs its own explicit approval]**

   ```sh
   .venv/bin/python -B scripts/extract_bbva.py --live --limit 3
   .venv/bin/python -B scripts/extract_abn_amro.py --live --limit 1
   .venv/bin/python -B scripts/extract_bis.py --live --limit 1
   .venv/bin/python -B scripts/extract_nyfed_lse.py --live --limit 1
   ```

   Each approved command reads its selected article(s), asks the AI for structured
   claims, saves only validated semantics, and records successfully processed URLs.

7. **[FREE/OFFLINE — assemble the evidence packet]**

   ```sh
   .venv/bin/python -B scripts/assemble_synthesis.py --date YYYY-MM-DD
   ```

   This checks the stored extractions and processed state, then prints the exact
   seven-day article-and-claim packet that can support a briefing; it makes no AI call.

8. **[FREE/OFFLINE — canonical synthesis preflight]**

   ```sh
   .venv/bin/python -B scripts/synthesise_candidate.py
   ```

   This builds and sizes the synthesis request, checks the exact-input ledger and
   reports blockers without sending anything or changing the stored briefing.

9. **[PAID/LIVE — canonical synthesis; explicit approval required]**

   ```sh
   .venv/bin/python -B scripts/synthesise_candidate.py --live --limit 1
   ```

   This makes one guarded AI synthesis call for the current London window and writes
   a new `synthesis-result.json` only after structural and claim-reference validation.

10. **[FREE/OFFLINE — canonical verification preflight]**

    ```sh
    .venv/bin/python -B scripts/verify_candidate.py
    ```

    This sizes and checks the saved synthesis snapshot, its verification eligibility
    and existing blockers without making an AI call.

11. **[PAID/LIVE — canonical verification; explicit approval required]**

    ```sh
    .venv/bin/python -B scripts/verify_candidate.py --live --limit 1
    ```

    This asks the independent checker whether the saved synthesis is supported by its
    extracted claims; PASS permits human review only and never publishes automatically.

    If the result is `FAIL` or `blocked`, stop. Do not create interpretation output,
    do not mark `site-config.js` as reviewed, do not publish the candidate, and do not
    label it as verified. Keep the previous reviewed briefing live or leave the new
    candidate pending while Avi decides what to do next.

12. **[FREE/OFFLINE — interpretation preflight]**

    ```sh
    .venv/bin/python -B scripts/interpret_briefing.py --show-prompt
    ```

    This prints the final interpretation prompt and reports request size, cost
    reservation and ledger state without asking the AI to write BBB's read.

13. **[PAID/LIVE — interpretation; explicit approval required]**

    ```sh
    .venv/bin/python -B scripts/interpret_briefing.py --live
    ```

    This makes the guarded BBB's read call against the stored synthesis and writes
    `interpretation.json` only if its schema, word limits and claim IDs validate.

14. **[HUMAN REVIEW — no script]**

    ```sh
    open http://127.0.0.1:4173/
    ```

    Review every displayed point, claim chain and interpretation against the publisher
    links. Record each correction by hand in root-level `corrections.json` using its
    `target_id`, `original_text`, `corrected_text` and `reason` fields. That file is
    specific to one briefing: replace its entries by hand for each new briefing; never
    carry old target IDs or original text into a different synthesis. After Avi has
    approved the corrected briefing, change `site-config.js` from
    `review_status: "pending"` to `review_status: "reviewed"`. Before that approval,
    it must remain `pending`.

15. **[FREE/OFFLINE — check the date and presentation files]**

    ```sh
    git diff -- synthesis-result.json interpretation.json corrections.json site-config.js index.html
    ```

    Check the generated `synthesis-result.json` window and timestamp, the matching
    `interpretation.json`, the rewritten `corrections.json`, and `site-config.js` review
    status. `index.html` has no date to edit: its `LATEST BRIEFING` placeholder is
    replaced by `app.js` using the stored synthesis window. Do not edit it merely for a
    new date range.

16. **[FREE/OFFLINE — commit and push only after review]**

    ```sh
    git status --short
    git add synthesis-result.json interpretation.json corrections.json site-config.js
    git commit -m "feat: weekly briefing YYYY-MM-DD"
    git push
    ```

    First inspect `git status --short` and stage only the reviewed briefing files (plus
    any deliberately changed documentation); then commit and push the approved update.
