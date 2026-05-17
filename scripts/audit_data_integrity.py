#!/usr/bin/env python3
"""Lightweight Capital Trace data integrity audit.

Run from the repository root after a refresh:
    python scripts/audit_data_integrity.py

This audit is intentionally non-destructive. It checks the generated JSON for
unit-scaling problems, missing source links, missing vital points, and obvious
lane-format regressions. It prints warnings and exits nonzero only for severe
format failures.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

DATA_PATH = Path("data/capital_trace.json")
SEVERE_SINGLE_13F_VALUE_USD = 2_000_000_000_000


def is_13f(record: Dict[str, Any]) -> bool:
    text = " ".join(str(record.get(k, "")) for k in ["source_group", "source_type", "filing_type", "record_type"]).lower()
    return "13f" in text or "institutional" in text


def money_value(record: Dict[str, Any]) -> float | None:
    for key in ["reported_market_value_usd", "market_value", "transaction_value"]:
        value = record.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    raw_thousands = record.get("reported_market_value_thousands")
    if isinstance(raw_thousands, (int, float)):
        return float(raw_thousands) * 1000
    return None


def main() -> int:
    if not DATA_PATH.exists():
        print(f"[ERROR] {DATA_PATH} not found")
        return 2
    payload = json.loads(DATA_PATH.read_text())
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        print("[ERROR] data file does not contain a records array")
        return 2

    warnings: List[str] = []
    for rec in records:
        rid = rec.get("record_id") or rec.get("id") or "unknown-record"
        if not rec.get("source_url"):
            warnings.append(f"{rid}: missing source_url")
        if not rec.get("vital_point"):
            warnings.append(f"{rid}: missing vital_point")
        if is_13f(rec):
            val = money_value(rec)
            if val is not None and val >= SEVERE_SINGLE_13F_VALUE_USD:
                warnings.append(f"{rid}: 13F single holding value looks suspiciously high: ${val:,.0f}")
            if rec.get("reported_market_value_thousands") and not rec.get("reported_market_value_usd"):
                warnings.append(f"{rid}: has 13F raw thousands but lacks converted USD field")

    print(f"[OK] records inspected: {len(records)}")
    print(f"[OK] schema_version: {payload.get('schema_version') if isinstance(payload, dict) else 'array'}")
    if warnings:
        print(f"[WARN] warnings: {len(warnings)}")
        for msg in warnings[:50]:
            print(f" - {msg}")
        if len(warnings) > 50:
            print(f" - ... {len(warnings) - 50} more")
    else:
        print("[OK] no obvious integrity warnings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
