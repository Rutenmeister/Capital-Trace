# Capital Trace v0.7 — SEC Ownership Lane

Capital Trace is an Edgefield Research prototype for turning public records of capital movement into an evidence-ranked research queue.

This version preserves the v0.6a hosted dashboard and adds the next SEC lane:

- **SEC Form 4** insider activity
- **SEC Schedule 13D / 13G** ownership threshold records

The dashboard still reads `data/capital_trace.json`. GitHub Actions runs the refresh job hourly and rewrites the data file. The website never calls SEC directly from a visitor browser.

## Upload note

If your live data is already working, do **not** upload the `data/` folder when applying this update unless you intentionally want to reset the data file.

For the v0.7 update, upload:

```text
index.html
style.css
app.js
README.md
scripts/refresh_sec_form4.py
scripts/refresh_sec_ownership.py
scripts/refresh_capital_trace.py
.github/workflows/refresh-capital-trace.yml
```

If your computer hides `.github`, create or edit this file directly in GitHub:

```text
.github/workflows/refresh-capital-trace.yml
```

## Run once after upload

After committing the files:

```text
Actions → Refresh Capital Trace → Run workflow
```

Wait for a green check, then open the live page and hard-refresh:

```text
Ctrl + Shift + R
```

## Data mode expected after v0.7 refresh

After the workflow runs, `data/capital_trace.json` should show:

```json
"data_mode": "sec-watchlist-multilane"
```

and `coverage_lanes` should include:

```json
["SEC Form 4", "SEC 13D/G"]
```

## Important caveat

SEC Schedule 13D/G records are less standardized than Form 4 records. This lane is conservative and source-linked. Any extracted issuer, filer, or ownership percentage should be verified against the original SEC filing before use.

Capital Trace is research software only. It is not investment advice, a signal service, or a prediction engine.
