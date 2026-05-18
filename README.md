# Capital Trace v0.13 — Stable Recovery Build

This freezes v0.12n as the last attempted SEC full-index crawler iteration and creates a stable operational build that does not pretend SEC access worked when GitHub Actions receives HTTP 403 responses.

## What this version does

- Keeps the existing Capital Trace dashboard and Evidence Queue.
- Keeps preserved records safe instead of overwriting them with empty refreshes.
- Disables scheduled SEC crawler refreshes in the two refresh workflows.
- Adds a safe repair workflow for the current `data/capital_trace.json` file.
- Repairs 13F value/unit problems already present in the loaded dataset.
- Keeps a diagnostic-only SEC access workflow so source access can be tested without touching live data.

## Upload

Upload these files/folders:

```text
index.html
style.css
app.js
README.md
schema/
scripts/
.github/workflows/
```

Do not upload `data/` from this package. Keep the current live `data/capital_trace.json` in the repository.

## Run this first

Run:

```text
Actions -> Repair Capital Trace Existing Data -> Run workflow
```

This does not contact SEC. It only repairs the existing dataset, especially 13F unit/value fields, then audits and commits the repaired data.

## What is intentionally disabled

The direct SEC refresh workflows are manual-only. They should not be scheduled again until SEC access is proven reliable from the chosen environment or a provider/API route replaces direct GitHub Actions crawling.

## Frozen baseline note

v0.12n is frozen as the last SEC access-recovery crawler attempt. v0.13 is the stable recovery build: app shell works, data repair works, and source fetching is isolated from production data.
