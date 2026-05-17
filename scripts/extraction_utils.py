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





def normalize_13f_market_value(record: Dict[str, Any]) -> tuple[Any, List[str]]:
    """Return the best 13F USD market value plus integrity notes.

    SEC 13F information-table value fields are reported in thousands of dollars.
    New records should carry reported_market_value_thousands and
    reported_market_value_usd. This helper also guards older generated records
    that may have been accidentally scaled by 1,000 twice.
    """
    notes: List[str] = []
    raw_thousands = record.get("reported_market_value_thousands")
    if present(raw_thousands):
        raw_num = num(raw_thousands)
        if raw_num is not None:
            return int(raw_num * 1000), notes
    usd_explicit = record.get("reported_market_value_usd")
    if present(usd_explicit):
        return usd_explicit, notes
    value = record.get("market_value") or record.get("transaction_value")
    n = num(value)
    if n is not None and n >= 2_000_000_000_000:
        # A single 13F holding above $2T is far more likely to be a legacy
        # double-scaling bug than a real holding in the current universe. Keep
        # the correction visible in extraction notes rather than silently hiding it.
        notes.append("13F market value looked double-scaled; display normalized down by 1,000.")
        return n / 1000, notes
    return value, notes


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
            "SEC reported value basis": "Converted from thousands of dollars",
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
    return [add_vital_fields(r) for r in records]


def extraction_summary(records: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {"complete": 0, "partial": 0, "minimal": 0, "failed": 0}
    for r in records:
        status = str((r.get("extraction_quality") or {}).get("status") or "minimal")
        out[status] = out.get(status, 0) + 1
    return out
