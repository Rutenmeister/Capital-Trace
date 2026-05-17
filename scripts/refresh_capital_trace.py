#!/usr/bin/env python3
"""
Capital Trace v0.12 master refresh.

Universal SEC filing engine + trust layer:
- runs SEC lanes sequentially
- normalizes all supported filings into the Capital Trace record model
- writes diagnostics so the UI can say what was checked, found, or failed
- commits one combined data/capital_trace.json payload

Supported watchlist lanes in v0.12:
- Form 4 / 4/A insider transactions
- SC 13D, SC 13D/A, SC 13G, SC 13G/A beneficial ownership records
- 13F-HR / 13F-HR/A institutional holdings records
- Form 144 / 144/A proposed sale notices

This stays watchlist-based. It does not scan the entire SEC universe.
"""

from __future__ import annotations

import json
import traceback
from copy import deepcopy
from typing import Any, Dict, List, Tuple

from refresh_sec_form4 import (
    DATA_DIR,
    LOOKBACK_DAYS,
    OUTPUT_JS,
    OUTPUT_JSON,
    Company,
    iso_now,
    load_watchlist,
    next_hour_iso,
    collect_form4_records,
)
from refresh_sec_ownership import collect_ownership_records
from refresh_sec_13f import collect_13f_records
from refresh_sec_144 import collect_form144_records
from extraction_utils import postprocess_records, extraction_summary

MAX_OUTPUT_RECORDS = 650
SUPPORTED_FORMS = ["4", "4/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "13F-HR", "13F-HR/A", "144", "144/A"]


def base_diag(*, lane: str, forms: List[str], lookback_days: int = LOOKBACK_DAYS) -> Dict[str, Any]:
    return {
        "status": "disabled",
        "lane": lane,
        "forms_checked": forms,
        "companies_checked": 0,
        "lookback_days": lookback_days,
        "filings_seen": 0,
        "filings_matched": 0,
        "records_added": 0,
        "errors": [],
        "note": "Lane not run.",
    }


def finalize_diag(diag: Dict[str, Any]) -> Dict[str, Any]:
    errors = diag.get("errors") or []
    records_added = int(diag.get("records_added") or 0)
    filings_matched = int(diag.get("filings_matched") or 0)
    if errors and records_added > 0:
        diag["status"] = "partial"
        diag["note"] = "Lane produced records, but some requests or parses failed. Review errors."
    elif errors:
        diag["status"] = "failed"
        diag["note"] = "Lane failed or produced no records because of errors. Existing records should be preserved if available."
    elif records_added > 0:
        diag["status"] = "ok"
        diag["note"] = "Lane completed and added records."
    elif filings_matched > 0:
        diag["status"] = "checked_no_records"
        diag["note"] = "Lane found matching filings, but no normalized records were extracted. Review parser coverage."
    else:
        diag["status"] = "checked_no_records"
        diag["note"] = "Lane completed and found no matching filings in the current watchlist/lookback window."
    return diag


def dedupe(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for record in records:
        rid = record.get("record_id") or record.get("id") or json.dumps(record, sort_keys=True)
        if rid in seen:
            continue
        seen.add(rid)
        out.append(record)
    return out


def load_previous_payload() -> Dict[str, Any] | None:
    if not OUTPUT_JSON.exists():
        return None
    try:
        return json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


def source_group_counts(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        key = str(record.get("source_group") or record.get("source_type") or "Unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def filing_type_counts(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        key = str(record.get("filing_type") or record.get("source_form") or record.get("source_type") or "Unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def write_outputs(records: List[Dict[str, Any]], companies: List[Company], diagnostics: Dict[str, Any]) -> None:
    records = postprocess_records(dedupe(records))
    records = sorted(records, key=lambda r: (int(r.get("score") or 0), str(r.get("filed_date") or "")), reverse=True)[:MAX_OUTPUT_RECORDS]
    previous = load_previous_payload()

    # Integrity guard: if all lanes fail or produce zero records, do not destroy a previously good live file.
    preserved_previous_records = False
    if not records and previous and isinstance(previous.get("records"), list) and previous.get("records"):
        records = previous["records"]
        preserved_previous_records = True

    timestamp = iso_now()
    source_groups = sorted({str(r.get("source_group") or "Unknown") for r in records}) or ["SEC Insider Ownership", "SEC Ownership Thresholds"]
    lane_count = len([d for d in diagnostics.values() if d.get("status") not in {"disabled"}])
    counts_by_lane = source_group_counts(records)
    counts_by_form = filing_type_counts(records)

    payload = {
        "schema_version": "0.12",
        "data_mode": "sec-watchlist-multilane",
        "generated_at": timestamp,
        "lookback_days": LOOKBACK_DAYS,
        "records_count": len(records),
        "lane_count": lane_count,
        "supported_forms": SUPPORTED_FORMS,
        "lane_diagnostics": diagnostics,
        "metadata": {
            "product": "Capital Trace",
            "schema_version": "0.12",
            "data_mode": "sec-watchlist-multilane",
            "source_pipeline": "sec-universal-watchlist-template",
            "refresh_frequency": "hourly",
            "last_refreshed": timestamp,
            "last_data_update": timestamp,
            "last_sec_check": timestamp,
            "next_scheduled_check": next_hour_iso(),
            "source_groups": source_groups,
            "coverage_lanes": ["SEC Form 4", "SEC 13D/G Ownership", "SEC 13F Institutional Holdings", "SEC Form 144 Proposed Sales"],
            "watchlist_count": len(companies),
            "record_count": len(records),
            "records_count": len(records),
            "lane_count": lane_count,
            "lookback_days": LOOKBACK_DAYS,
            "supported_forms": SUPPORTED_FORMS,
            "counts_by_lane": counts_by_lane,
            "counts_by_form": counts_by_form,
            "preserved_previous_records": preserved_previous_records,
            "methodology_version": "0.12-vital-point-precision-scoring-calibration",
            "extraction_summary": extraction_summary(records),
        },
        "records": records,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUTPUT_JS.write_text("window.CAPITAL_TRACE_PAYLOAD = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"[OK] wrote {OUTPUT_JSON} with {len(records)} records; lanes={json.dumps({k: v.get('records_added') for k, v in diagnostics.items()})}")
    if preserved_previous_records:
        print("[WARN] No new records were produced; preserved previous live records instead of overwriting with empty data.")


def run_lane(name: str, func, companies: List[Company], diag: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    print(f"[INFO] Running lane: {name}")
    try:
        records = func(companies, diagnostics=diag)
        diag["records_added"] = len(records)
        return records, finalize_diag(diag)
    except TypeError:
        # Backward compatibility if an older lane function without diagnostics is uploaded by mistake.
        try:
            records = func(companies)
            diag["records_added"] = len(records)
            diag["errors"].append("Lane used compatibility path without full diagnostics. Upload v0.8 lane script.")
            return records, finalize_diag(diag)
        except Exception as exc:
            diag["errors"].append(f"{type(exc).__name__}: {exc}")
            diag["traceback_tail"] = traceback.format_exc()[-1200:]
            return [], finalize_diag(diag)
    except Exception as exc:
        diag["errors"].append(f"{type(exc).__name__}: {exc}")
        diag["traceback_tail"] = traceback.format_exc()[-1200:]
        return [], finalize_diag(diag)


def main() -> int:
    companies = load_watchlist()
    print(f"[INFO] Capital Trace v0.12 vital point precision refresh: {len(companies)} watchlist companies; lookback={LOOKBACK_DAYS} days")

    form4_diag = base_diag(lane="insider", forms=["4", "4/A"])
    ownership_diag = base_diag(lane="ownership", forms=["SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"])
    institutional_diag = base_diag(lane="institutional", forms=["13F-HR", "13F-HR/A"])
    proposed_sales_diag = base_diag(lane="proposed_sales", forms=["144", "144/A"])

    form4_records, form4_diag = run_lane("SEC Form 4", collect_form4_records, companies, form4_diag)
    ownership_records, ownership_diag = run_lane("SEC 13D/G Ownership", collect_ownership_records, companies, ownership_diag)
    institutional_records, institutional_diag = run_lane("SEC 13F Institutional Holdings", collect_13f_records, companies, institutional_diag)
    proposed_sales_records, proposed_sales_diag = run_lane("SEC Form 144 Proposed Sales", collect_form144_records, companies, proposed_sales_diag)

    diagnostics = {
        "insider_form4": form4_diag,
        "ownership_13d_13g": ownership_diag,
        "institutional_13f": institutional_diag,
        "proposed_sales_144": proposed_sales_diag,
    }
    write_outputs(form4_records + ownership_records + institutional_records + proposed_sales_records, companies, diagnostics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
