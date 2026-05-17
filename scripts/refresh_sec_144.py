#!/usr/bin/env python3
"""
Capital Trace SEC Form 144 proposed sale notice lane.

Adds a watchlist-based SEC Form 144 lane. Form 144 is a notice of proposed sale
of restricted/control securities; it is not confirmation that a sale happened.
Records are normalized into the Capital Trace schema and labeled as proposed
sale context with strict caveats.
"""

from __future__ import annotations

import html
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from refresh_sec_form4 import (
    Company,
    LOOKBACK_DAYS,
    filing_base_url,
    now_utc,
    parse_date,
    recent_filings_by_forms,
    sec_get,
)

FORM144_FORMS = {"144", "144/A"}
MAX_144_PER_COMPANY = int(__import__("os").environ.get("CAPITAL_TRACE_MAX_144_PER_COMPANY", "30"))


def strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", strip_tags(text or "")).strip()


def source_url(company: Company, filing: Dict[str, Any]) -> str:
    base = filing_base_url(company, filing.get("accession", ""))
    primary = filing.get("primary_document") or ""
    return f"{base}/{primary}" if primary else base


def get_form144_document(company: Company, filing: Dict[str, Any]) -> Tuple[str, str]:
    """Return Form 144 filing text and source URL."""
    url = source_url(company, filing)
    text = sec_get(url, as_json=False)
    if text:
        return text, url

    base = filing_base_url(company, filing.get("accession", ""))
    index = sec_get(f"{base}/index.json", as_json=True)
    if index:
        for item in index.get("directory", {}).get("item", []):
            name = item.get("name", "")
            lname = name.lower()
            if lname.endswith((".htm", ".html", ".txt", ".xml")) and "144" in lname:
                url = f"{base}/{name}"
                text = sec_get(url, as_json=False)
                if text:
                    return text, url
        for item in index.get("directory", {}).get("item", []):
            name = item.get("name", "")
            if name.lower().endswith((".htm", ".html", ".txt", ".xml")):
                url = f"{base}/{name}"
                text = sec_get(url, as_json=False)
                if text:
                    return text, url
    return "", url


def regex_first(patterns: List[str], text: str) -> str:
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            value = html.unescape(m.group(1)).strip(" :-\t\r\n")
            value = re.sub(r"\s+", " ", value)
            if value:
                return value[:180]
    return ""


def parse_float(value: str) -> Optional[float]:
    if not value:
        return None
    s = re.sub(r"[^0-9.\-]", "", value)
    if not s or s in {".", "-", "-."}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def infer_filer(text: str) -> str:
    plain = compact_text(text)
    return regex_first([
        r"Name of Person for Whose Account the Securities are To Be Sold\s*[:\-]?\s*([^|\n]{2,160})",
        r"Name of person for whose account.*?sold\s*[:\-]?\s*([^|\n]{2,160})",
        r"Reporting Person\s*[:\-]?\s*([^|\n]{2,160})",
        r"Name\s*[:\-]?\s*([^|\n]{2,160})",
    ], plain) or "Proposed seller / reporting person"


def infer_relationship(text: str) -> str:
    plain = compact_text(text)
    return regex_first([
        r"Relationship to Issuer\s*[:\-]?\s*([^|\n]{2,160})",
        r"Relationship of Person to Issuer\s*[:\-]?\s*([^|\n]{2,160})",
        r"Position with Issuer\s*[:\-]?\s*([^|\n]{2,160})",
        r"Officer Title\s*[:\-]?\s*([^|\n]{2,160})",
    ], plain) or "Proposed seller / affiliate"


def infer_broker(text: str) -> str:
    plain = compact_text(text)
    return regex_first([
        r"Name of Broker\s*[:\-]?\s*([^|\n]{2,160})",
        r"Broker or Market Maker\s*[:\-]?\s*([^|\n]{2,160})",
        r"Broker\s*[:\-]?\s*([^|\n]{2,160})",
    ], plain)


def infer_approx_sale_date(text: str) -> str:
    plain = compact_text(text)
    return regex_first([
        r"Approximate Date of Sale\s*[:\-]?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
        r"Date of Sale\s*[:\-]?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
    ], plain)


def infer_shares(text: str) -> Optional[float]:
    plain = compact_text(text)
    value = regex_first([
        r"Number of Shares(?: or Other Units)? of Securities To Be Sold\s*[:\-]?\s*([0-9,]+(?:\.[0-9]+)?)",
        r"No\. of Shares(?: or Other Units)? To Be Sold\s*[:\-]?\s*([0-9,]+(?:\.[0-9]+)?)",
        r"Number of Shares.*?To Be Sold\s*[:\-]?\s*([0-9,]+(?:\.[0-9]+)?)",
        r"Shares.*?To Be Sold\s*[:\-]?\s*([0-9,]+(?:\.[0-9]+)?)",
        r"Amount.*?To Be Sold\s*[:\-]?\s*([0-9,]+(?:\.[0-9]+)?)",
    ], plain)
    return parse_float(value)


def infer_market_value(text: str) -> Optional[float]:
    plain = compact_text(text)
    value = regex_first([
        r"Aggregate Market Value(?: of Securities To Be Sold)?\s*[:\-]?\s*\$?([0-9,]+(?:\.[0-9]+)?)",
        r"Approximate.*?Market Value\s*[:\-]?\s*\$?([0-9,]+(?:\.[0-9]+)?)",
        r"Market Value.*?To Be Sold\s*[:\-]?\s*\$?([0-9,]+(?:\.[0-9]+)?)",
        r"Aggregate Sales Price\s*[:\-]?\s*\$?([0-9,]+(?:\.[0-9]+)?)",
    ], plain)
    return parse_float(value)


def infer_prior_three_month_sales(text: str) -> str:
    plain = compact_text(text)
    return regex_first([
        r"Securities Sold During the Past 3 Months.*?([0-9,]+(?:\.[0-9]+)?\s+(?:shares|units).{0,120})",
        r"Past 3 Months.*?([0-9,]+(?:\.[0-9]+)?\s+(?:shares|units).{0,120})",
    ], plain)


def score_form144(filed_date: str, market_value: Optional[float], shares: Optional[float], is_amendment: bool, watchlist_match: bool) -> Tuple[int, str, str, str, List[str], str]:
    score = 42
    reasons = ["Source-linked SEC Form 144 proposed sale notice"]

    if is_amendment:
        score -= 6
        reasons.append("Amendment to proposed-sale notice; review source for what changed")
    else:
        score += 8
        reasons.append("New proposed-sale notice")

    if market_value is not None:
        if market_value >= 10_000_000:
            score += 18
            reasons.append(f"Large proposed sale value extracted: approximately ${market_value:,.0f}")
        elif market_value >= 1_000_000:
            score += 10
            reasons.append(f"Proposed sale value extracted: approximately ${market_value:,.0f}")
        else:
            score += 4
            reasons.append("Proposed sale value extracted")
    elif shares is not None:
        score += 4
        reasons.append("Proposed share amount extracted, but market value was not extracted")
    else:
        score -= 4
        reasons.append("Proposed size was not extracted automatically; source review required")

    filed_dt = parse_date(filed_date)
    age_days = (now_utc() - filed_dt).days if filed_dt else 999
    if age_days <= 7:
        score += 8
        freshness = "Recent"
        reasons.append("Filed within the last week")
    elif age_days <= 30:
        score += 3
        freshness = "Fresh"
    elif age_days <= LOOKBACK_DAYS:
        freshness = "Fresh"
    else:
        freshness = "Stale"
        score -= 8

    if watchlist_match:
        score += 5
        reasons.append("Matched to Capital Trace watchlist")

    score = max(0, min(100, int(round(score))))
    grade = "B" if score >= 70 else "C" if score >= 48 else "D"
    # Form 144 is proposed-sale context, not confirmed activity; keep actionability restrained.
    if score >= 76:
        action = "Watch"
    elif score >= 45:
        action = "Context Only"
    else:
        action = "Low Signal"
    caveat = "Form 144 is a proposed sale notice, not confirmation that shares were sold. Check later Form 4 filings and the source notice before drawing conclusions."
    return score, grade, freshness, action, reasons, caveat


def parse_form144_record(company: Company, filing: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text, url = get_form144_document(company, filing)
    if not text:
        return None
    form = filing.get("form") or "144"
    filed_date = filing.get("filing_date") or filing.get("report_date") or ""
    filer = infer_filer(text)
    relationship = infer_relationship(text)
    broker = infer_broker(text)
    approx_sale_date = infer_approx_sale_date(text)
    shares = infer_shares(text)
    market_value = infer_market_value(text)
    prior_three_month_sales = infer_prior_three_month_sales(text)
    is_amendment = "/A" in str(form).upper()
    score, grade, freshness, action, reasons, caveat = score_form144(filed_date, market_value, shares, is_amendment, True)
    accession = filing.get("accession") or ""
    record_id = f"sec-form144:{accession}:{company.ticker}:{form}"
    event_type = "Form 144 Amendment" if is_amendment else "Proposed Sale Notice"
    return {
        "id": record_id,
        "record_id": record_id,
        "ticker": company.ticker,
        "company": company.ticker,
        "source_group": "SEC Proposed Sales",
        "source_type": f"SEC Form {form}",
        "source_form": form,
        "filing_type": "Form 144/A" if is_amendment else "Form 144",
        "record_type": "Proposed Sale Notice",
        "event_type": event_type,
        "entity_type": "insider or affiliate proposed seller",
        "filer": filer,
        "role": relationship or "Proposed seller / affiliate",
        "owner_type": "Proposed sale notice",
        "filed_date": filed_date,
        "event_date": approx_sale_date or filed_date,
        "transaction_date": approx_sale_date or "",
        "period_end": "",
        "accession_number": accession,
        "transaction_code": "144",
        "shares": shares,
        "price": None,
        "transaction_value": market_value,
        "broker": broker,
        "prior_three_month_sales": prior_three_month_sales,
        "score": score,
        "evidence_grade": grade,
        "freshness": freshness,
        "actionability": action,
        "watchlist_match": True,
        "rank_reasons": reasons,
        "does_not_prove": [
            "That the proposed sale actually occurred",
            "Future price movement",
            "Insider intent beyond the notice itself",
            "That sale size or timing will match the notice",
        ],
        "caveat": caveat,
        "source_url": url,
        "vital_point": (f"Proposed sale notice: {filer} disclosed up to {shares:,.0f} shares, estimated value ${market_value:,.0f}." if shares is not None and market_value is not None else f"Proposed sale notice for {company.ticker}; proposed shares/value were not fully parsed."),
    }


def collect_form144_records(companies: List[Company], diagnostics: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if diagnostics is not None:
        diagnostics["companies_checked"] = len(companies)
        diagnostics["forms_checked"] = ["144", "144/A"]
    for company in companies:
        filings = recent_filings_by_forms(company, FORM144_FORMS, MAX_144_PER_COMPANY, LOOKBACK_DAYS)
        if diagnostics is not None:
            diagnostics["filings_seen"] = int(diagnostics.get("filings_seen") or 0) + len(filings)
            diagnostics["filings_matched"] = int(diagnostics.get("filings_matched") or 0) + len(filings)
        for filing in filings:
            try:
                record = parse_form144_record(company, filing)
                if record:
                    records.append(record)
            except Exception as exc:
                if diagnostics is not None:
                    diagnostics.setdefault("errors", []).append(f"{company.ticker} {filing.get('accession','')}: {type(exc).__name__}: {exc}")
        if len(records) >= MAX_144_PER_COMPANY * max(1, len(companies)):
            break
    if diagnostics is not None and not records:
        diagnostics["note"] = "No Form 144 proposed-sale records were found in the current watchlist/lookback window."
    return records


if __name__ == "__main__":
    from refresh_sec_form4 import load_watchlist
    diag: Dict[str, Any] = {"errors": []}
    rows = collect_form144_records(load_watchlist(), diagnostics=diag)
    print(f"records={len(rows)} diagnostics={diag}")
