# Capital Trace v0.12 — Vital Point Precision + Scoring Calibration

Capital Trace is an Edgefield Research SEC filing terminal. It converts public SEC records into an evidence-ranked reading queue with source links, caveats, lane diagnostics, extraction quality, and vital-point summaries.

## Current supported SEC lanes

- SEC Form 4 / Form 4/A insider transactions
- SEC Schedule 13D / 13D/A / 13G / 13G/A ownership diagnostics
- SEC 13F-HR / 13F-HR/A institutional holdings
- SEC Form 144 / 144/A proposed sale notices

## What v0.12 adds

- Sharper Vital Point sentences across all current filing lanes.
- Better key-figure panels for shares, price, value, ownership percent, report period, CUSIP, broker, and proposed sale fields when parsed.
- Top Vital Points focus view.
- Extraction Quality readout in the right rail.
- Improved Form 144 extraction patterns for proposed shares, market value, broker, relationship, and prior-three-month sale hints.
- 13F records now include manager CIK, position rank, position weight when calculable, and an explicit pending prior-quarter comparison label.
- More explicit missing-field language: not parsed, not applicable, and pending comparison where appropriate.
- Scoring remains conservative: Form 144 is proposed sale context, 13F is delayed holdings context, and insider sales are treated as context unless unusually strong.

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
.github/workflows/refresh-capital-trace.yml
```

If `.github` is hidden, manually update `.github/workflows/refresh-capital-trace.yml` so it runs:

```yaml
run: python scripts/refresh_capital_trace.py
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
