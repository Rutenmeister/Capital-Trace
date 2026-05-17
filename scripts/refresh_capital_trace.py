#!/usr/bin/env python3
"""
Capital Trace master refresh.

Runs all active source lanes, merges them into the universal Capital Trace record
format, and writes data/capital_trace.json plus the local embedded fallback JS.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from refresh_sec_form4 import DATA_DIR, OUTPUT_JS, OUTPUT_JSON, Company, iso_now, load_watchlist, next_hour_iso, collect_form4_records
from refresh_sec_ownership import collect_ownership_records

MAX_OUTPUT_RECORDS = 180


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


def write_outputs(records: List[Dict[str, Any]], companies: List[Company], lane_counts: Dict[str, int]) -> None:
    records = dedupe(records)
    records = sorted(records, key=lambda r: (int(r.get("score") or 0), str(r.get("filed_date") or "")), reverse=True)[:MAX_OUTPUT_RECORDS]
    timestamp = iso_now()
    active_lanes = []
    if lane_counts.get("form4", 0) >= 0:
        active_lanes.append("SEC Form 4")
    if lane_counts.get("ownership", 0) >= 0:
        active_lanes.append("SEC 13D/G")

    source_groups = sorted({str(r.get("source_group") or "Unknown") for r in records}) or ["SEC Insider Ownership", "SEC Ownership Thresholds"]
    payload = {
        "metadata": {
            "product": "Capital Trace",
            "schema_version": "0.7",
            "data_mode": "sec-watchlist-multilane",
            "source_pipeline": "sec-edgar-form4-13dg-watchlist",
            "refresh_frequency": "hourly",
            "last_refreshed": timestamp,
            "last_data_update": timestamp,
            "last_sec_check": timestamp,
            "next_scheduled_check": next_hour_iso(),
            "source_groups": source_groups,
            "coverage_lanes": active_lanes,
            "watchlist_count": len(companies),
            "record_count": len(records),
            "lane_counts": lane_counts,
            "methodology_version": "0.7-sec-ownership-lane",
        },
        "records": records,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUTPUT_JS.write_text("window.CAPITAL_TRACE_PAYLOAD = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"[OK] wrote {OUTPUT_JSON} with {len(records)} records; lanes={lane_counts}")


def main() -> int:
    companies = load_watchlist()
    print(f"[INFO] Capital Trace master refresh: {len(companies)} watchlist companies")

    form4_records = collect_form4_records(companies)
    ownership_records = collect_ownership_records(companies)

    all_records = form4_records + ownership_records
    lane_counts = {
        "form4": len(form4_records),
        "ownership": len(ownership_records),
    }
    write_outputs(all_records, companies, lane_counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
