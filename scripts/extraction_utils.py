from __future__ import annotations

from typing import Any, Dict, List, Optional


def present(value: Any) -> bool:
    return value is not None and value != "" and value != "-" and value != []


def num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def fmt_number(value: Any) -> str:
    n = num(value)
    if n is None:
        return "Not disclosed / not parsed"
    if abs(n - int(n)) < 1e-9:
        return f"{int(n):,}"
    return f"{n:,.2f}"


def fmt_money(value: Any) -> str:
    n = num(value)
    if n is None:
        return "Not disclosed / not parsed"
    if abs(n) >= 1_000_000_000:
        return f"${n/1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    return f"${n:,.0f}"


def fmt_price(value: Any) -> str:
    n = num(value)
    if n is None:
        return "Not disclosed / not parsed"
    return f"${n:,.2f}"


def quality(parsed_fields: List[str], required_fields: List[str], notes: Optional[List[str]] = None) -> Dict[str, Any]:
    parsed = [f for f in parsed_fields if f]
    missing = [f for f in required_fields if f not in parsed]
    if not required_fields:
        status = "minimal"
    else:
        ratio = len(parsed) / max(1, len(required_fields))
        if not missing:
            status = "complete"
        elif ratio >= 0.60:
            status = "partial"
        elif ratio >= 0.25:
            status = "minimal"
        else:
            status = "failed"
    return {
        "status": status,
        "parsed_fields": parsed,
        "missing_fields": missing,
        "notes": notes or [],
    }


def add_vital_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    """Add universal vital point, key figures and extraction quality without inventing values."""
    r = dict(record)
    form = str(r.get("filing_type") or r.get("source_form") or r.get("source_type") or "").upper()
    lane = str(r.get("source_group") or "").lower()
    event = str(r.get("event_type") or "Record")
    ticker = str(r.get("ticker") or "-")
    filer = str(r.get("filer") or "Not disclosed / not parsed")
    role = str(r.get("role") or "Not disclosed / not parsed")
    filed = str(r.get("filed_date") or "Not disclosed / not parsed")
    event_date = str(r.get("event_date") or r.get("transaction_date") or r.get("period_end") or "Not disclosed / not parsed")

    parsed: List[str] = []
    required: List[str] = ["ticker", "filer", "filed_date", "source_url", "accession_number"]
    for field in required:
        if present(r.get(field)):
            parsed.append(field)

    key_figures: Dict[str, str] = {}
    person_entity: Dict[str, str] = {
        "Filer / entity": filer,
        "Role / relationship": role,
    }
    trust: Dict[str, str] = {
        "Filing type": str(r.get("filing_type") or r.get("source_form") or "Unknown"),
        "Source lane": str(r.get("source_group") or "Unknown"),
        "Filed date": filed,
        "Event / report date": event_date,
        "Accession": str(r.get("accession_number") or "Not disclosed / not parsed"),
    }
    notes: List[str] = []

    if present(r.get("shares")):
        parsed.append("shares")
    if present(r.get("price")):
        parsed.append("price")
    if present(r.get("transaction_value")) or present(r.get("market_value")):
        parsed.append("value")
    if present(r.get("role")):
        parsed.append("role")

    if "FORM 4" in form or "SEC INSIDER" in lane:
        required += ["shares", "price", "value", "transaction_date", "role"]
        key_figures = {
            "Shares": fmt_number(r.get("shares")),
            "Price": fmt_price(r.get("price")),
            "Estimated value": fmt_money(r.get("transaction_value")),
            "Transaction code": str(r.get("transaction_code") or "Not disclosed / not parsed"),
            "Ownership after": fmt_number(r.get("shares_after")),
        }
        if present(r.get("transaction_value")) and present(r.get("shares")) and present(r.get("price")):
            vital = f"{event}: {filer} ({role}) reported {fmt_number(r.get('shares'))} shares at {fmt_price(r.get('price'))}, estimated value {fmt_money(r.get('transaction_value'))}."
        elif present(r.get("shares")):
            vital = f"{event}: {filer} ({role}) reported {fmt_number(r.get('shares'))} shares; price/value were not fully parsed."
        else:
            vital = f"{event}: Form 4 record for {ticker}; key economic fields were not fully parsed."
    elif "144" in form or "PROPOSED" in lane:
        required += ["shares", "value", "event_date", "broker"]
        if present(r.get("broker")):
            parsed.append("broker")
        key_figures = {
            "Proposed shares": fmt_number(r.get("shares")),
            "Estimated market value": fmt_money(r.get("transaction_value") or r.get("market_value")),
            "Approximate sale date": str(r.get("event_date") or "Not disclosed / not parsed"),
            "Broker": str(r.get("broker") or "Not disclosed / not parsed"),
        }
        if present(r.get("transaction_value")) and present(r.get("shares")):
            vital = f"Proposed sale notice: {filer} disclosed up to {fmt_number(r.get('shares'))} shares, estimated value {fmt_money(r.get('transaction_value'))}."
        elif present(r.get("shares")):
            vital = f"Proposed sale notice: {filer} disclosed up to {fmt_number(r.get('shares'))} shares; market value was not parsed."
        else:
            vital = f"Proposed sale notice for {ticker}; proposed shares/value were not fully parsed."
        notes.append("Form 144 is a notice of proposed sale, not confirmation that the sale occurred.")
    elif "13F" in form or "INSTITUTIONAL" in lane:
        required += ["shares", "value", "period_end", "cusip"]
        if present(r.get("period_end")):
            parsed.append("period_end")
        if present(r.get("cusip")):
            parsed.append("cusip")
        key_figures = {
            "Reported shares": fmt_number(r.get("shares")),
            "Reported market value": fmt_money(r.get("market_value") or r.get("transaction_value")),
            "Report period": str(r.get("period_end") or "Not disclosed / not parsed"),
            "CUSIP": str(r.get("cusip") or "Not disclosed / not parsed"),
            "Position rank": str(r.get("position_rank") or "Not disclosed / not parsed"),
        }
        vital = f"13F holding: {filer} reported {fmt_number(r.get('shares'))} shares of {ticker}, market value {fmt_money(r.get('market_value') or r.get('transaction_value'))}, as of {r.get('period_end') or 'the reported period'}."
        notes.append("13F is delayed institutional holdings context, not live trade evidence.")
    elif "13D" in form or "13G" in form or "OWNERSHIP" in lane:
        required += ["ownership_percent", "shares"]
        if present(r.get("ownership_percent")):
            parsed.append("ownership_percent")
        key_figures = {
            "Beneficial shares": fmt_number(r.get("shares")),
            "Ownership percent": f"{num(r.get('ownership_percent')):.2f}%" if num(r.get("ownership_percent")) is not None else "Not disclosed / not parsed",
            "Report / event date": str(r.get("event_date") or r.get("period_end") or "Not disclosed / not parsed"),
        }
        pct = key_figures["Ownership percent"]
        vital = f"{event}: {filer} reported beneficial ownership for {ticker}; ownership percent {pct}."
        notes.append("13D/G ownership records require source review for intent and exact ownership context.")
    else:
        vital = f"{event}: source-linked record for {ticker}."

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
