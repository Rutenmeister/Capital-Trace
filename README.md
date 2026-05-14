# Capital Trace v0.5 — SEC Form 4 Refresh

Capital Trace is an Edgefield Research prototype for turning public records of capital movement into a ranked evidence queue.

This version adds the first real-data layer:

- `data/watchlist.json` — companies to check
- `scripts/refresh_sec_form4.py` — pulls recent SEC Form 4 records from EDGAR
- `.github/workflows/refresh-capital-trace.yml` — runs the refresh every hour and updates `data/capital_trace.json`

## First-time setup on GitHub

1. Upload these files to your `capital-trace` repository.
2. Go to **Settings → Actions → General**.
3. Under **Workflow permissions**, choose **Read and write permissions**.
4. Click **Save**.
5. Go to **Actions → Refresh Capital Trace → Run workflow**.
6. Wait 1-2 minutes.
7. Open `data/capital_trace.json` and confirm it contains real records.
8. Open your GitHub Pages site and click **Check Now**.

## Optional but recommended

Create a GitHub secret called `CAPITAL_TRACE_USER_AGENT` with a real contact email, for example:

```text
CapitalTrace/0.5 your-email@example.com
```

GitHub path:

```text
Settings → Secrets and variables → Actions → New repository secret
```

The script uses SEC public EDGAR endpoints and is intentionally watchlist-based so it stays lightweight.

## Editing the watchlist

Open `data/watchlist.json` and add/remove companies:

```json
{ "ticker": "NVDA", "cik": "0001045810" }
```

Use 10-digit CIKs when possible. The script will also normalize shorter CIKs.

## What this version does not do yet

- It does not scan the entire SEC universe.
- It does not retrieve Congress disclosures yet.
- It does not retrieve 13F or 13D/G records yet.
- It does not call SEC from the browser.

The browser only reads the saved `data/capital_trace.json` file.
