# Capital Trace v0.8 — Universal SEC Filing Engine + Trust Layer

Capital Trace is an Edgefield research module: **public records of capital movement, traced to the source**.

This version preserves the working Evidence Queue UI and adds a more professional SEC refresh foundation.

## Current architecture

```text
SEC watchlist
  -> universal SEC refresh script
  -> Form 4 normalizer
  -> 13D/G ownership normalizer
  -> normalized Capital Trace records
  -> lane diagnostics
  -> data/capital_trace.json
  -> static dashboard
```

## Supported lanes in v0.8

- **Insider lane**: Form 4 / 4/A
- **Ownership lane**: SC 13D, SC 13D/A, SC 13G, SC 13G/A

The system remains **watchlist-based**. It does not scan the entire SEC universe.

## Lookback

Default lookback is **60 days**. The GitHub Action passes:

```text
CAPITAL_TRACE_LOOKBACK_DAYS=60
```

The frontend also includes a time-window filter:

- All loaded records
- Last 7 days
- Last 30 days
- Last 60 days
- Last 90 days

## Trust layer

The generated JSON now includes `lane_diagnostics`, so the UI can say honestly:

- what lane ran
- what forms were checked
- how many companies were checked
- how many filings were seen/matched
- how many records were added
- whether a lane found no records or failed

No fake records should be created. If a lane finds zero records, the UI should say so.

## Upload guidance

For normal code updates, upload these files only:

```text
index.html
style.css
app.js
README.md
schema/capital_trace_record.schema.json
scripts/refresh_capital_trace.py
scripts/refresh_sec_form4.py
scripts/refresh_sec_ownership.py
.github/workflows/refresh-capital-trace.yml
```

Do **not** upload the `data/` folder unless intentionally resetting live data.

## GitHub Action

The workflow runs:

```text
python scripts/refresh_capital_trace.py
```

The script runs lanes sequentially and commits once at the end.

## Integrity rules

- Do not fabricate missing filings.
- Do not label a 13D record as activist unless the source clearly supports it.
- If a lane fails, report it in diagnostics.
- If no new records are produced and previous live records exist, preserve the previous live records instead of overwriting with an empty file.
- Website users load the saved JSON; their browser does not directly call SEC.
