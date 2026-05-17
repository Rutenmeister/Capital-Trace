# Capital Trace v0.12a — Integrity Audit + 13F Value Unit Fix

Capital Trace is an Edgefield Research SEC filing terminal. It converts public SEC records into an evidence-ranked reading queue with source links, caveats, lane diagnostics, extraction quality, and vital-point summaries.

## Current supported SEC lanes

- SEC Form 4 / Form 4/A insider transactions
- SEC Schedule 13D / 13D/A / 13G / 13G/A ownership diagnostics
- SEC 13F-HR / 13F-HR/A institutional holdings
- SEC Form 144 / 144/A proposed sale notices

## What v0.12a adds

- Sharper Vital Point sentences across all current filing lanes.
- Better key-figure panels for shares, price, value, ownership percent, report period, CUSIP, broker, and proposed sale fields when parsed.
- Top Vital Points focus view.
- Extraction Quality readout in the right rail.
- Improved Form 144 extraction patterns for proposed shares, market value, broker, relationship, and prior-three-month sale hints.
- 13F records now include manager CIK, position rank, position weight when calculable, and an explicit pending prior-quarter comparison label.
- More explicit missing-field language: not parsed, not applicable, and pending comparison where appropriate.
- Scoring remains conservative: Form 144 is proposed sale context, 13F is delayed holdings context, and insider sales are treated as context unless unusually strong.


## v0.12a integrity fixes

- Fixes SEC 13F market-value interpretation. SEC 13F information-table `value` fields are reported in thousands of dollars; Capital Trace now stores both `reported_market_value_thousands` and converted USD fields.
- Adds frontend guards for older live JSON records that may have been displayed 1,000x too high. Example: Berkshire/AAPL should display around `$20.47B`, not `$20,471.92B`.
- Updates the schema so 13F value-basis fields are explicit.
- Updates the 13F key-figure panel to show `SEC reported value basis: Converted from thousands of dollars`.
- Keeps all missing-data language honest: not disclosed / not parsed / not applicable / pending comparison.

## Upload guidance

Do not upload `data/` unless intentionally resetting live data.

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

If `.github` is hidden, manually update `.github/workflows/refresh-capital-trace.yml` so it runs:

```yaml
run: python scripts/refresh_capital_trace.py
# then
run: python scripts/audit_data_integrity.py
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
