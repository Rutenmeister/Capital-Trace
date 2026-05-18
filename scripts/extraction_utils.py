from __future__ import annotations

from typing import Any, Dict, List, Optional

MISSING_NOT_PARSED = "Not disclosed / not parsed"
MISSING_NOT_APPLICABLE = "Not applicable"
MISSING_PENDING = "Pending comparison"


def present(value: Any) -> bool:
    return value is not None and value != "" and value != "-" and value != []


def num(value: Any) -> Optional[float]:
    if value is None or value == "" or value == "-":
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").replace("$", "").replace("%", "").strip()
        return float(value)
    except Exception:
        return None


def fmt_number(value: Any) -> str:
    n = num(value)
    if n is None:
        return MISSING_NOT_PARSED
    if abs(n - int(n)) < 1e-9:
        return f"{int(n):,}"
    return f"{n:,.2f}"


def fmt_money(value: Any) -> str:
    n = num(value)
    if n is None:
        return MISSING_NOT_PARSED
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{sign}${n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{sign}${n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{sign}${n:,.0f}"
    return f"{sign}${n:,.2f}"





def _plausible_implied_price(value: float, shares: Optional[float]) -> bool:
    if shares is None or shares <= 0 or value is None or value <= 0:
        return False
    price = value / shares
    return 0.05 <= price <= 100_000


def normalize_13f_market_value(record: Dict[str, Any]) -> tuple[Any, List[str]]:
    """Return the best 13F USD market value plus integrity notes.

    Uses implied price per share to repair both fresh and preserved legacy 13F
    records. This prevents two opposite errors:
    - treating a USD table value as thousands and inflating by 1,000x;
    - treating a true SEC-thousands value as USD and shrinking by 1,000x.
    """
    notes: List[str] = []
    severe_single_holding = 2_000_000_000_000  # $2T for one security is a hard red flag here.
    shares = num(record.get("shares"))

    candidates: List[tuple[float, str]] = []
    raw_thousands = num(record.get("reported_market_value_thousands"))
    if raw_thousands is not None and raw_thousands > 0:
        candidates.append((raw_thousands, "reported value treated as USD by implied-price sanity check"))
        candidates.append((raw_thousands * 1000, "SEC 13F value field converted from thousands of dollars"))

    for key in ["reported_market_value_usd", "market_value", "transaction_value"]:
        value = num(record.get(key))
        if value is None or value <= 0:
            continue

        # Preserve the literal parsed USD candidate, but also add downscaled
        # candidates for legacy records that were accidentally inflated 1,000x.
        # This catches the common failure where a 13F table value that should be
        # $914M was displayed as $914B. The implied-price selector below chooses
        # the plausible candidate instead of the largest raw number.
        candidates.append((value, f"{key} used as USD"))

        scaled = value
        for factor in [1000, 1_000_000]:
            scaled = value / factor
            if scaled > 0:
                candidates.append((scaled, f"{key} normalized down by {factor:,} after implied-price sanity check"))

        if value >= severe_single_holding:
            notes.append(f"13F {key} looked severely high; downscaled candidates added for sanity selection.")

    if not candidates:
        return None, notes

    if shares and shares > 0:
        plausible = [(v, label) for v, label in candidates if _plausible_implied_price(v, shares)]
        if plausible:
            # Prefer the largest plausible value. This picks $20.47B for Berkshire/AAPL
            # over the shrunken $20.47M, while rejecting impossible $914B AMZN-style
            # values when the USD interpretation is $914M.
            best, label = max(plausible, key=lambda item: item[0])
            if "treated as USD" in label:
                notes.append("13F value treated as USD after implied-price sanity check.")
            elif "converted from thousands" in label:
                notes.append("13F value converted from SEC thousands after implied-price sanity check.")
            return best, notes

    # Without shares, choose a conservative non-trillion candidate.
    non_severe = [(v, label) for v, label in candidates if v < severe_single_holding]
    if non_severe:
        return min(non_severe, key=lambda item: item[0])[0], notes
    best = min(candidates, key=lambda item: item[0])[0]
    return best, notes


def repair_13f_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize 13F value fields in both freshly parsed and preserved legacy records."""
    form = str(record.get("filing_type") or record.get("source_form") or record.get("source_type") or "").upper()
    lane = str(record.get("source_group") or "").upper()
    if "13F" not in form and "INSTITUTIONAL" not in lane:
        return record

    r = dict(record)
    usd, notes = normalize_13f_market_value(r)
    if usd is not None:
        usd_int = int(round(float(usd)))
        r["reported_market_value_usd"] = usd_int
        r["market_value"] = usd_int
        r["transaction_value"] = usd_int
        # Keep a clean derived thousands field for transparency.
        r["reported_market_value_thousands"] = int(round(usd_int / 1000))
        r["market_value_unit_basis"] = "13F value normalized with global implied-price sanity check; prevents legacy 1,000x unit errors"
        r["position_value_label"] = fmt_money(usd_int)
        r["value_basis_label"] = "13F value normalized by implied-price sanity check"

    # Rebuild stale UI-derived text/quality if a previous version wrote bad values.
    for field in ["vital_point", "key_figures", "person_entity", "source_trust", "extraction_quality"]:
        r.pop(field, None)
    if notes:
        r["unit_repair_notes"] = sorted(set(str(n) for n in notes))
    return r


def fmt_price(value: Any) -> str:
    n = num(value)
    if n is None:
        return MISSING_NOT_PARSED
    return f"${n:,.2f}"


def fmt_percent(value: Any) -> str:
    n = num(value)
    if n is None:
        return MISSING_NOT_PARSED
    return f"{n:.2f}%"


def quality(parsed_fields: List[str], required_fields: List[str], notes: Optional[List[str]] = None) -> Dict[str, Any]:
    parsed = sorted(set(f for f in parsed_fields if f))
    required = list(dict.fromkeys(f for f in required_fields if f))
    missing = [f for f in required if f not in parsed]
    if not required:
        status = "minimal"
    else:
        ratio = len([f for f in parsed if f in required]) / max(1, len(required))
        if not missing:
            status = "complete"
        elif ratio >= 0.60:
            status = "partial"
        elif ratio >= 0.25:
            status = "minimal"
        else:
            status = "failed"
    return {"status": status, "parsed_fields": parsed, "missing_fields": missing, "notes": notes or []}


def _add_if_present(record: Dict[str, Any], field: str, parsed: List[str]) -> None:
    if present(record.get(field)):
        parsed.append(field)


def _base_trust(record: Dict[str, Any], filed: str, event_date: str) -> Dict[str, str]:
    return {
        "Filing type": str(record.get("filing_type") or record.get("source_form") or "Unknown"),
        "Source lane": str(record.get("source_group") or "Unknown"),
        "Filed date": filed or MISSING_NOT_PARSED,
        "Event / report date": event_date or MISSING_NOT_PARSED,
        "Accession": str(record.get("accession_number") or MISSING_NOT_PARSED),
    }


def add_vital_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    """Add universal vital point, key figures and extraction quality without inventing values."""
    r = dict(record)
    form = str(r.get("filing_type") or r.get("source_form") or r.get("source_type") or "").upper()
    lane = str(r.get("source_group") or "").lower()
    event = str(r.get("event_type") or "Record")
    ticker = str(r.get("ticker") or "-")
    filer = str(r.get("filer") or MISSING_NOT_PARSED)
    role = str(r.get("role") or MISSING_NOT_PARSED)
    filed = str(r.get("filed_date") or MISSING_NOT_PARSED)
    event_date = str(r.get("event_date") or r.get("transaction_date") or r.get("period_end") or MISSING_NOT_PARSED)

    parsed: List[str] = []
    required: List[str] = ["ticker", "filer", "filed_date", "source_url", "accession_number"]
    for field in required:
        _add_if_present(r, field, parsed)

    person_entity: Dict[str, str] = {
        "Filer / entity": filer,
        "Role / relationship": role,
    }
    trust: Dict[str, str] = _base_trust(r, filed, event_date)
    notes: List[str] = []
    key_figures: Dict[str, str] = {}

    # Common figure fields.
    for field in ["shares", "price", "transaction_value", "market_value", "role", "ownership_percent", "broker", "period_end", "cusip", "shares_after"]:
        _add_if_present(r, field, parsed)

    if "FORM 4" in form or ("SEC INSIDER" in lane and "144" not in form):
        required += ["shares", "price", "transaction_value", "transaction_date", "role"]
        value = r.get("transaction_value")
        key_figures = {
            "Shares": fmt_number(r.get("shares")),
            "Price": fmt_price(r.get("price")),
            "Estimated value": fmt_money(value),
            "Transaction code": str(r.get("transaction_code") or MISSING_NOT_PARSED),
            "Ownership after": fmt_number(r.get("shares_after")),
        }
        person_entity.update({"Insider": filer, "Relationship": role, "Ownership type": str(r.get("owner_type") or MISSING_NOT_PARSED)})
        if present(value) and present(r.get("shares")) and present(r.get("price")):
            vital = f"{event}: {filer} ({role}) reported {fmt_number(r.get('shares'))} shares at {fmt_price(r.get('price'))}, estimated value {fmt_money(value)}."
        elif present(r.get("shares")):
            vital = f"{event}: {filer} ({role}) reported {fmt_number(r.get('shares'))} shares; price/value were not fully parsed."
        else:
            vital = f"{event}: Form 4 record for {ticker}; key economic fields were not fully parsed."
    elif "144" in form or "PROPOSED" in lane:
        required += ["shares", "transaction_value", "event_date", "broker"]
        value = r.get("transaction_value") or r.get("market_value")
        key_figures = {
            "Proposed shares": fmt_number(r.get("shares")),
            "Estimated market value": fmt_money(value),
            "Approximate sale date": str(r.get("event_date") or MISSING_NOT_PARSED),
            "Broker": str(r.get("broker") or MISSING_NOT_PARSED),
        }
        person_entity.update({"Proposed seller": filer, "Relationship": role})
        if present(value) and present(r.get("shares")):
            vital = f"Proposed sale notice: {filer} disclosed up to {fmt_number(r.get('shares'))} shares, estimated value {fmt_money(value)}."
        elif present(r.get("shares")):
            vital = f"Proposed sale notice: {filer} disclosed up to {fmt_number(r.get('shares'))} shares; market value was not parsed."
        else:
            vital = f"Proposed sale notice for {ticker}; proposed shares/value were not fully parsed."
        notes.append("Form 144 is a notice of proposed sale, not confirmation that the sale occurred.")
    elif "13F" in form or "INSTITUTIONAL" in lane:
        required += ["shares", "market_value", "period_end", "cusip"]
        value, unit_notes = normalize_13f_market_value(r)
        notes.extend(unit_notes)
        if present(r.get("reported_market_value_thousands")):
            parsed.append("reported_market_value_thousands")
        if present(value):
            parsed.append("market_value")
        key_figures = {
            "Reported shares": fmt_number(r.get("shares")),
            "Reported market value": fmt_money(value),
            "SEC reported value basis": str(r.get("market_value_unit_basis") or "Converted from thousands of dollars"),
            "Report period": str(r.get("period_end") or MISSING_NOT_PARSED),
            "CUSIP": str(r.get("cusip") or MISSING_NOT_PARSED),
            "Position rank": str(r.get("position_rank") or MISSING_NOT_PARSED),
            "Change vs prior quarter": str(r.get("change_vs_prior") or MISSING_PENDING),
        }
        person_entity.update({"Manager": filer, "Manager CIK": str(r.get("manager_cik") or MISSING_NOT_PARSED), "Issuer": str(r.get("company") or ticker)})
        vital = f"13F holding: {filer} reported {fmt_number(r.get('shares'))} shares of {ticker}, market value {fmt_money(value)}, as of {r.get('period_end') or 'the reported period'}."
        notes.append("13F is delayed institutional holdings context, not live trade evidence.")
    elif "13D" in form or "13G" in form or "OWNERSHIP" in lane:
        required += ["ownership_percent", "shares"]
        key_figures = {
            "Beneficial shares": fmt_number(r.get("shares")),
            "Ownership percent": fmt_percent(r.get("ownership_percent")),
            "Report / event date": str(r.get("event_date") or r.get("period_end") or MISSING_NOT_PARSED),
        }
        person_entity.update({"Reporting person": filer, "Relationship": role})
        pct = key_figures["Ownership percent"]
        shares_text = key_figures["Beneficial shares"]
        if pct != MISSING_NOT_PARSED:
            vital = f"{event}: {filer} reported beneficial ownership of {pct} of {ticker}."
        elif shares_text != MISSING_NOT_PARSED:
            vital = f"{event}: {filer} reported {shares_text} beneficial shares of {ticker}; ownership percent was not parsed."
        else:
            vital = f"{event}: {filer} filed an ownership record for {ticker}; key ownership figures were not fully parsed."
        notes.append("13D/G ownership records require source review for intent and exact ownership context.")
    else:
        vital = f"{event}: source-linked record for {ticker}."
        key_figures = {"Status": MISSING_NOT_PARSED}

    r["vital_point"] = r.get("vital_point") or vital
    r["key_figures"] = r.get("key_figures") or key_figures
    r["person_entity"] = r.get("person_entity") or person_entity
    r["source_trust"] = r.get("source_trust") or trust
    r["extraction_quality"] = r.get("extraction_quality") or quality(parsed, required, notes)
    return r


def postprocess_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [add_vital_fields(repair_13f_record(r)) for r in records]


def extraction_summary(records: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {"complete": 0, "partial": 0, "minimal": 0, "failed": 0}
    for r in records:
        status = str((r.get("extraction_quality") or {}).get("status") or "minimal")
        out[status] = out.get(status, 0) + 1
    return out
