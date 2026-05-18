#!/usr/bin/env python3
"""Repair the existing Capital Trace dataset without contacting SEC.

This is the safe recovery workflow. It reads data/capital_trace.json, repairs
legacy 13F market-value/unit problems, rebuilds stale display fields where
needed, marks the dataset as preserved/repaired, and writes the same JSON/JS
files the frontend already consumes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from extraction_utils import repair_13f_record, add_vital_fields

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
JSON_PATH = DATA_DIR / "capital_trace.json"
JS_PATH = DATA_DIR / "capital_trace_data.js"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_payload() -> Dict[str, Any]:
    if not JSON_PATH.exists():
        raise FileNotFoundError(f"{JSON_PATH} not found. Keep the current live data file in the repo before running repair.")
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"records": payload, "metadata": {}}
    if not isinstance(payload, dict):
        raise ValueError("capital_trace.json is not a JSON object or record array")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("capital_trace.json does not contain a records array")
    return payload


def is_13f(record: Dict[str, Any]) -> bool:
    text = " ".join(str(record.get(k, "")) for k in ["source_group", "source_type", "source_form", "filing_type", "record_type"]).lower()
    return "13f" in text or "institutional" in text


def counts_by(records: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in records:
        value = str(r.get(key) or "Unknown")
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def main() -> int:
    payload = load_payload()
    records = payload.get("records") or []
    repaired_records: List[Dict[str, Any]] = []
    repaired_13f = 0

    for record in records:
        rec = dict(record)
        if is_13f(rec):
            before = json.dumps({k: rec.get(k) for k in ["market_value", "reported_market_value_usd", "transaction_value", "market_value_unit_basis"]}, sort_keys=True)
            rec = repair_13f_record(rec)
            after = json.dumps({k: rec.get(k) for k in ["market_value", "reported_market_value_usd", "transaction_value", "market_value_unit_basis"]}, sort_keys=True)
            if before != after:
                repaired_13f += 1
        rec = add_vital_fields(rec)
        repaired_records.append(rec)

    metadata = dict(payload.get("metadata") or {})
    now = iso_now()
    metadata.update({
        "schema_version": "0.13",
        "methodology_version": "0.13-stable-recovery-repair",
        "last_repaired": now,
        "last_data_update": now,
        "data_mode": metadata.get("data_mode") or "preserved-repaired-dataset",
        "data_source_truth": "preserved_repaired_dataset",
        "fresh_sec_refresh_status": "disabled_pending_source_access_fix",
        "refresh_frequency": "manual repair only until SEC/provider access is stable",
        "records_count": len(repaired_records),
        "counts_by_form": counts_by(repaired_records, "filing_type"),
        "counts_by_source_group": counts_by(repaired_records, "source_group"),
        "repair_summary": {
            "13f_records_repaired_or_rebuilt": repaired_13f,
            "sec_contacted": False,
            "note": "This workflow repairs the existing dataset only. It does not fetch SEC and cannot create new filings."
        },
        "refresh_guard": {
            "status": "repaired_preserved_dataset",
            "preserved_previous_records": True,
            "preservation_reason": "SEC live crawler disabled after HTTP 403 access-blocking. Existing data repaired and preserved."
        }
    })
    # Remove stale SEC 403 stats from older failed refresh attempts so the repaired dataset is not audited as a fake-green SEC refresh.
    metadata.pop("sec_request_stats", None)

    payload["schema_version"] = "0.13"
    payload["methodology_version"] = "0.13-stable-recovery-repair"
    payload["records"] = repaired_records
    payload["metadata"] = metadata

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    JS_PATH.write_text("window.CAPITAL_TRACE_PAYLOAD = " + json.dumps(payload, indent=2, sort_keys=False) + ";\n", encoding="utf-8")

    print(f"[OK] repaired existing dataset: {len(repaired_records)} records")
    print(f"[OK] 13F records repaired/rebuilt: {repaired_13f}")
    print(f"[OK] wrote {JSON_PATH}")
    print(f"[OK] wrote {JS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
