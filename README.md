# Capital Trace v0.12i — Source Truth + CUSIP Display Fix

Capital Trace is an Edgefield Research SEC filing terminal. It converts public SEC records into an evidence-ranked reading queue with source links, caveats, lane diagnostics, extraction quality, and vital-point summaries.

## Current supported SEC lanes

- SEC Form 4 / Form 4/A insider transactions
- SEC Schedule 13D / 13D/A / 13G / 13G/A ownership diagnostics
- SEC 13F-HR / 13F-HR/A institutional holdings
- SEC Form 144 / 144/A proposed sale notices

## What v0.12i fixes

- Keeps the 13F value-unit repair so 13F values display as actual USD, not 1,000x inflated values.
- Repairs preserved legacy 13F records before audit when possible.
- Keeps signal-safe output capping so high-signal records are not silently discarded by a global cap.
- Cleans up 13F display titles: unmapped CUSIPs no longer appear as the headline when issuer/company name is available. CUSIP remains visible as a key figure.
- Separates loaded dataset coverage from latest refresh diagnostics so preserved prior records are not confused with a zero-record refresh attempt.
- Adds clearer data-source-truth wording in the Current Trace Brief.
- Keeps the 20-record center Evidence Queue display.

## Upload guidance

Do not upload `data/` unless intentionally resetting or restoring live data.

Upload these files/folders:

```text
index.html
style.css
app.js
README.md
schema/capital_trace_record.schema.json
scripts/refresh_capital_trace.py
scripts/refresh_sec_form4.py
scripts/refresh_sec_ownership.py
scripts/refresh_sec_13f.py
scripts/refresh_sec_144.py
scripts/extraction_utils.py
scripts/audit_data_integrity.py
.github/workflows/refresh-capital-trace.yml
```

If `.github` is hidden, manually update `.github/workflows/refresh-capital-trace.yml` so it runs the master refresh and audit scripts.

## Suggested workflow settings

```yaml
CAPITAL_TRACE_LOOKBACK_DAYS: "180"
CAPITAL_TRACE_MAX_FORM4_PER_COMPANY: "100"
CAPITAL_TRACE_MAX_OUTPUT_RECORDS: "5000"
CAPITAL_TRACE_USER_AGENT: "CapitalTrace/0.12i rutenmeister@users.noreply.github.com"
CAPITAL_TRACE_EMPTY_DATA_GUARD: "true"
```

Then run:

```text
Actions -> Refresh Capital Trace -> Run workflow
```

After a green check, hard refresh the live site with Ctrl + Shift + R.

## Integrity notes

- Capital Trace does not fabricate records or values.
- If a field is not present or not parsed, the UI says so.
- Form 144 records are proposed sale notices, not confirmed sales.
- 13F records are delayed institutional holdings, not live trade records.
- 13D/G records require source review for intent and ownership context.


## v0.12j Tiered SEC Scanner notes

This version replaces the brute-force S&P 500 hourly scan with a tiered scanner:

- `refresh-capital-trace.yml` runs the fast core watchlist hourly.
- `refresh-capital-trace-broad.yml` runs a slower S&P 500 batch scan daily.
- Broad mode uses `CAPITAL_TRACE_MAX_ISSUERS_PER_RUN` and `CAPITAL_TRACE_ISSUER_OFFSET=auto` to rotate through issuers instead of hammering all S&P 500 companies in one run.
- Broad mode sets `CAPITAL_TRACE_DISABLE_OWNERSHIP_BROWSE=true` to avoid the high-fanout old EDGAR Atom fallback that triggered repeated SEC HTTP 403 responses.
- `CAPITAL_TRACE_SEC_MAX_403_ERRORS` opens a circuit breaker when SEC starts denying requests. The run preserves previous good records rather than continuing to hammer SEC.
- `CAPITAL_TRACE_MERGE_PREVIOUS_RECORDS=true` merges new batch records with the existing live dataset so partial broad scans do not erase prior coverage.

Recommended manual workflow setup:

Fast core hourly:

```yaml
CAPITAL_TRACE_REFRESH_SCOPE: "fast"
CAPITAL_TRACE_ISSUER_WATCHLIST_MODE: "core"
CAPITAL_TRACE_SEC_REQUEST_DELAY_SECONDS: "0.5"
CAPITAL_TRACE_SEC_MAX_403_ERRORS: "10"
CAPITAL_TRACE_RUN_13F: "false"
```

Broad S&P 500 daily:

```yaml
CAPITAL_TRACE_REFRESH_SCOPE: "broad"
CAPITAL_TRACE_ISSUER_WATCHLIST_MODE: "sp500"
CAPITAL_TRACE_MAX_ISSUERS_PER_RUN: "100"
CAPITAL_TRACE_ISSUER_OFFSET: "auto"
CAPITAL_TRACE_SEC_REQUEST_DELAY_SECONDS: "0.75"
CAPITAL_TRACE_SEC_MAX_403_ERRORS: "10"
CAPITAL_TRACE_DISABLE_OWNERSHIP_BROWSE: "true"
CAPITAL_TRACE_RUN_13F: "false"
```

13F should later be its own slower manager-watchlist workflow because 13F is delayed quarterly data and does not need hourly scanning.
