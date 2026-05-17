# Capital Trace v0.10a — SEC Form 144 Proposed Sale Lane

Capital Trace is an Edgefield research prototype: **public records of capital movement, traced to the source.**

This version preserves the v0.8 Universal SEC Filing Engine + Trust Layer and adds a third SEC lane:

- **SEC Form 4 / 4/A** — insider transactions
- **SEC SC 13D / 13D/A / 13G / 13G/A** — beneficial ownership / threshold records
- **SEC 13F-HR / 13F-HR/A** — institutional holdings reports

## Important integrity notes

- The site does **not** call SEC from a visitor's browser.
- GitHub Actions runs the refresh script and writes `data/capital_trace.json`.
- The dashboard reads the saved JSON file.
- 13F data is delayed portfolio context. It does not prove current holdings, shorts, hedges, or manager intent.
- The 13F lane is manager-watchlist based. It does not scan every 13F manager in the SEC universe.
- Do not upload the `data/` folder unless intentionally resetting live data.

## Upload files

Upload these files/folders from the unzipped package:

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
.github/workflows/refresh-capital-trace.yml
```

If `.github` is hidden on your computer, manually edit this file in GitHub:

```text
.github/workflows/refresh-capital-trace.yml
```

The workflow should run:

```yaml
run: python scripts/refresh_capital_trace.py
```

## Optional 13F manager watchlist

The 13F lane has a conservative built-in fallback manager list. To customize it later, create:

```text
data/institutional_watchlist.json
```

Example:

```json
[
  { "name": "Berkshire Hathaway Inc", "cik": "0001067983" },
  { "name": "Renaissance Technologies LLC", "cik": "0001037389" }
]
```

## After upload

Run:

```text
Actions → Refresh Capital Trace → Run workflow
```

Then open the live site and hard refresh:

```text
Ctrl + Shift + R
```

## Success criteria

- GitHub Action finishes green.
- `data/capital_trace.json` shows `schema_version: 0.9`.
- `lane_diagnostics` includes `insider_form4`, `ownership_13d_13g`, and `institutional_13f`.
- The UI shows Form 4, 13D/G, and 13F filing-type support.
- If 13F finds zero records, the Lane Health panel should say so honestly.


## v0.10 notes — Form 144 Proposed Sale Lane

Capital Trace v0.10a adds a watchlist-based SEC Form 144 / 144/A lane. Form 144 is a proposed sale notice, not a confirmed sale. The lane is intentionally caveated and should be used as context until later Form 4 records confirm actual transactions.

Supported lanes after v0.10:

- SEC Form 4 / 4/A insider transaction records
- SEC SC 13D / 13D/A / 13G / 13G/A ownership threshold records
- SEC 13F-HR / 13F-HR/A institutional holdings records
- SEC Form 144 / 144/A proposed sale notices

Upload reminder: do not upload the `data/` folder unless intentionally resetting live data.
