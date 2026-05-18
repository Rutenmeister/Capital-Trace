#!/usr/bin/env python3
"""Capital Trace data integrity audit v0.12h.

Run from the repository root after a refresh:
    python scripts/audit_data_integrity.py

This audit is intentionally conservative. It catches the class of bugs that can
make a financial UI misleading: unit errors, missing records arrays, missing
source links, missing vital points, unsupported lane regressions, and obvious
13F value scaling problems.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from extraction_utils import normalize_13f_market_value

DATA_PATH = Path("data/capital_trace.json")
SEVERE_SINGLE_13F_VALUE_USD = 2_000_000_000_000
REQUIRED_DIAGNOSTIC_LANES = [
    "insider_form4",
    "ownership_13d_13g",
    "institutional_13f",
    "proposed_sales_144",
]


def is_13f(record: Dict[str, Any]) -> bool:
    text = " ".join(str(record.get(k, "")) for k in ["source_group", "source_type", "filing_type", "record_type"]).lower()
    return "13f" in text or "institutional" in text


def is_144(record: Dict[str, Any]) -> bool:
    text = " ".join(str(record.get(k, "")) for k in ["source_group", "source_type", "filing_type", "record_type", "event_type"]).lower()
    return "144" in text or "proposed sale" in text


def is_form4(record: Dict[str, Any]) -> bool:
    text = " ".join(str(record.get(k, "")) for k in ["source_group", "source_type", "filing_type", "record_type"]).lower()
    return "form 4" in text or text.strip() in {"4", "4/a"}


def number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        raw = value.replace("$", "").replace(",", "").replace("%", "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def best_13f_value(record: Dict[str, Any]) -> Tuple[float | None, List[str]]:
    value, notes = normalize_13f_market_value(record)
    n = number(value)
    return n, notes


def main() -> int:
    if not DATA_PATH.exists():
        print(f"[ERROR] {DATA_PATH} not found")
        return 2
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        print("[ERROR] data file does not contain a records array")
        return 2

    warnings: List[str] = []
    severe: List[str] = []

    if not records:
        severe.append("records array is empty; refusing to accept an empty Capital Trace dataset")

    if isinstance(payload, dict):
        diagnostics = payload.get("lane_diagnostics") or {}
        for lane in REQUIRED_DIAGNOSTIC_LANES:
            if lane not in diagnostics:
                warnings.append(f"lane_diagnostics missing expected lane: {lane}")
        lane_counts = {lane: int((diagnostics.get(lane) or {}).get("records_added") or 0) for lane in REQUIRED_DIAGNOSTIC_LANES if lane in diagnostics}
        if lane_counts and all(value == 0 for value in lane_counts.values()):
            metadata = payload.get("metadata") or {}
            guard = metadata.get("refresh_guard") if isinstance(metadata, dict) else {}
            if not (isinstance(guard, dict) and guard.get("preservation_reason")):
                severe.append("all expected SEC lanes reported zero records with no preservation guard reason")

    ids = set()
    for rec in records:
        rid = rec.get("record_id") or rec.get("id") or "unknown-record"
        if rid in ids:
            warnings.append(f"duplicate record id: {rid}")
        ids.add(rid)

        if not rec.get("source_url"):
            warnings.append(f"{rid}: missing source_url")
        if not rec.get("vital_point"):
            warnings.append(f"{rid}: missing vital_point")
        if not rec.get("filing_type"):
            warnings.append(f"{rid}: missing filing_type")

        if is_13f(rec):
            val, val_notes = best_13f_value(rec)
            for note in val_notes:
                warnings.append(f"{rid}: {note}")
            if val is not None and val >= SEVERE_SINGLE_13F_VALUE_USD:
                severe.append(f"{rid}: 13F single holding value looks suspiciously high after normalization: ${val:,.0f}")
            raw_thousands = number(rec.get("reported_market_value_thousands"))
            explicit_usd = number(rec.get("reported_market_value_usd"))
            if raw_thousands is not None and explicit_usd is not None and raw_thousands < 2_000_000_000:
                expected = raw_thousands * 1000
                if expected and abs(explicit_usd - expected) / expected > 0.02:
                    severe.append(f"{rid}: 13F USD value does not match raw thousands conversion")
            if not rec.get("market_value_unit_basis"):
                warnings.append(f"{rid}: 13F missing market_value_unit_basis; display should fall back but refresh should populate it")

        if is_144(rec):
            # 144 is proposed sale only. The record should say so somewhere obvious.
            caveat = " ".join(str(rec.get(k, "")) for k in ["caveat", "vital_point", "record_type", "event_type"]).lower()
            if "proposed" not in caveat:
                warnings.append(f"{rid}: Form 144/proposed-sale record lacks proposed-sale wording")

        if is_form4(rec):
            shares = number(rec.get("shares"))
            price = number(rec.get("price"))
            value = number(rec.get("transaction_value"))
            if shares is not None and price is not None and price > 0 and value is not None:
                expected = shares * price
                if expected and abs(value - expected) / expected > 0.05:
                    warnings.append(f"{rid}: Form 4 transaction_value differs from shares*price by >5%")

    print(f"[OK] records inspected: {len(records)}")
    print(f"[OK] schema_version: {payload.get('schema_version') if isinstance(payload, dict) else 'array'}")
    if warnings:
        print(f"[WARN] warnings: {len(warnings)}")
        for msg in warnings[:75]:
            print(f" - {msg}")
        if len(warnings) > 75:
            print(f" - ... {len(warnings) - 75} more")
    else:
        print("[OK] no integrity warnings")

    if severe:
        print(f"[ERROR] severe integrity failures: {len(severe)}")
        for msg in severe[:50]:
            print(f" - {msg}")
        if len(severe) > 50:
            print(f" - ... {len(severe) - 50} more")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
