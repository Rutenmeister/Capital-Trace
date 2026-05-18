#!/usr/bin/env python3
"""
Capital Trace v0.12g master refresh.

Universal SEC filing engine + trust layer:
- runs SEC lanes sequentially
- normalizes all supported filings into the Capital Trace record model
- writes diagnostics so the UI can say what was checked, found, or failed
- commits one combined data/capital_trace.json payload

Supported watchlist lanes in v0.12g:
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

import os

MAX_OUTPUT_RECORDS = int(os.environ.get("CAPITAL_TRACE_MAX_OUTPUT_RECORDS", "10000"))
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


def record_sort_key(record: Dict[str, Any]) -> Tuple[int, str]:
    return (int(record.get("score") or 0), str(record.get("filed_date") or ""))


def signal_safe_cap(records: List[Dict[str, Any]], max_records: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Cap output only after scoring, and never silently drop vital records.

    The cap is a storage/browser-safety guard, not a signal filter. Always keep
    Research Now records, A-tier records, 13D/G ownership events, and the newest
    records from every lane before trimming lower-signal context records.
    """
    total = len(records)
    if max_records <= 0 or total <= max_records:
        return records, {"enabled": False, "max_records": max_records, "total_before_cap": total, "total_after_cap": total, "capped": False, "trimmed": 0}

    def is_force_keep(record: Dict[str, Any]) -> bool:
        grade = str(record.get("evidence_grade") or "").upper()
        action = str(record.get("actionability") or "").lower()
        text = " ".join(str(record.get(k, "")) for k in ["source_group", "source_type", "filing_type", "record_type", "event_type"]).lower()
        score = int(record.get("score") or 0)
        value = record.get("transaction_value") or record.get("market_value") or 0
        try:
            value = float(value)
        except Exception:
            value = 0
        return (
            "research now" in action
            or grade in {"A", "A-"}
            or score >= 85
            or "13d" in text
            or "13g" in text
            or value >= 1_000_000_000
        )

    sorted_records = sorted(records, key=record_sort_key, reverse=True)
    selected: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add(record: Dict[str, Any]) -> None:
        rid = str(record.get("record_id") or record.get("id") or id(record))
        if rid not in selected_ids:
            selected.append(record)
            selected_ids.add(rid)

    for record in sorted_records:
        if is_force_keep(record):
            add(record)

    # Preserve recency/coverage by lane before filling the rest by score.
    by_lane: Dict[str, List[Dict[str, Any]]] = {}
    for record in sorted_records:
        lane = str(record.get("source_group") or record.get("source_type") or "Unknown")
        by_lane.setdefault(lane, []).append(record)
    per_lane_keep = max(20, min(150, max_records // max(1, len(by_lane)) // 2))
    for lane_records in by_lane.values():
        newest = sorted(lane_records, key=lambda r: str(r.get("filed_date") or ""), reverse=True)[:per_lane_keep]
        for record in newest:
            if len(selected) < max_records:
                add(record)

    for record in sorted_records:
        if len(selected) >= max_records:
            break
        add(record)

    return selected[:max_records], {
        "enabled": True,
        "max_records": max_records,
        "total_before_cap": total,
        "total_after_cap": min(len(selected), max_records),
        "capped": True,
        "trimmed": max(0, total - min(len(selected), max_records)),
        "rule": "signal-safe: force-keeps Research Now, A-tier, high-score, 13D/G, high-value, and newest-per-lane records before trimming lower-signal context",
    }


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


def previous_record_count(previous: Dict[str, Any] | None) -> int:
    if not previous or not isinstance(previous, dict):
        return 0
    prev_records = previous.get("records")
    return len(prev_records) if isinstance(prev_records, list) else 0


def refresh_guard_status(new_count: int, previous_count: int, diagnostics: Dict[str, Any]) -> Dict[str, Any]:
    lane_counts = {key: int((diag or {}).get("records_added") or 0) for key, diag in diagnostics.items()}
    all_lanes_zero = bool(lane_counts) and all(value == 0 for value in lane_counts.values())
    suspicious_drop = previous_count >= 50 and new_count < max(10, int(previous_count * 0.25))
    return {
        "new_record_count": new_count,
        "previous_record_count": previous_count,
        "lane_record_counts": lane_counts,
        "all_lanes_zero": all_lanes_zero,
        "suspicious_drop": suspicious_drop,
    }


def write_outputs(records: List[Dict[str, Any]], companies: List[Company], diagnostics: Dict[str, Any]) -> None:
    previous = load_previous_payload()
    prev_count = previous_record_count(previous)
    records = postprocess_records(dedupe(records))
    records = sorted(records, key=record_sort_key, reverse=True)
    records, output_cap = signal_safe_cap(records, MAX_OUTPUT_RECORDS)

    # Integrity guard: never replace a previously useful live file with an empty or near-empty refresh.
    # Important: if we preserve old records, we also preserve the diagnostics that describe
    # the loaded dataset. The latest zero/failed attempt is moved into latest_refresh_diagnostics
    # so the UI does not show "0 records" while old records are still loaded.
    preserved_previous_records = False
    latest_refresh_diagnostics = deepcopy(diagnostics)
    guard = refresh_guard_status(len(records), prev_count, diagnostics)
    if previous and isinstance(previous.get("records"), list) and previous.get("records"):
        if not records or guard["suspicious_drop"] or guard["all_lanes_zero"]:
            records = previous["records"]
            preserved_previous_records = True
            guard["preservation_reason"] = "new refresh returned zero/near-zero records or all lanes zero; preserved previous live records"
            previous_diags = previous.get("lane_diagnostics") or (previous.get("metadata") or {}).get("lane_diagnostics") or {}
            if isinstance(previous_diags, dict) and previous_diags:
                diagnostics = previous_diags
            # Previous good records may have been produced by older code. Re-run
            # postprocessing after preservation so legacy 13F unit bugs are repaired
            # before the audit step inspects the generated file.
            records = postprocess_records(dedupe(records))
            records = sorted(records, key=record_sort_key, reverse=True)
            records, output_cap = signal_safe_cap(records, MAX_OUTPUT_RECORDS)

    timestamp = iso_now()
    source_groups = sorted({str(r.get("source_group") or "Unknown") for r in records}) or ["SEC Insider Ownership", "SEC Ownership Thresholds"]
    lane_count = len([d for d in diagnostics.values() if d.get("status") not in {"disabled"}])
    counts_by_lane = source_group_counts(records)
    counts_by_form = filing_type_counts(records)

    payload = {
        "schema_version": "0.12g",
        "data_mode": "sec-watchlist-multilane",
        "generated_at": timestamp,
        "lookback_days": LOOKBACK_DAYS,
        "records_count": len(records),
        "lane_count": lane_count,
        "supported_forms": SUPPORTED_FORMS,
        "lane_diagnostics": diagnostics,
        "metadata": {
            "product": "Capital Trace",
            "schema_version": "0.12g",
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
            "refresh_guard": guard,
            "latest_refresh_diagnostics": latest_refresh_diagnostics,
            "methodology_version": "0.12g-preserved-data-repair-and-signal-safe-capping",
            "extraction_summary": extraction_summary(records),
            "output_cap": output_cap,
        },
        "latest_refresh_diagnostics": latest_refresh_diagnostics,
        "output_cap": output_cap,
        "records": records,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUTPUT_JS.write_text("window.CAPITAL_TRACE_PAYLOAD = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"[OK] wrote {OUTPUT_JSON} with {len(records)} records; lanes={json.dumps({k: v.get('records_added') for k, v in diagnostics.items()})}")
    if preserved_previous_records:
        print("[WARN] Refresh returned zero/near-zero records; preserved previous live records instead of overwriting good data.")
    elif not records:
        print("[ERROR] Refresh produced zero records and no previous records were available to preserve. Audit should fail before commit.")


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
    print(f"[INFO] Capital Trace v0.12g stability refresh: {len(companies)} watchlist companies; lookback={LOOKBACK_DAYS} days")

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
