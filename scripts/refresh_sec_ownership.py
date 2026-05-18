#!/usr/bin/env python3
"""
Capital Trace SEC 13D/G ownership refresh lane.

This script adds a second SEC source lane for Schedule 13D / 13G ownership
records. It is intentionally conservative: records are normalized into the
same Capital Trace schema as Form 4 records, with source links and caveats.

The SEC filing ecosystem does not expose every 13D/G record by target ticker
through one simple endpoint. This lane uses two watchlist-safe methods:
  1. Company submissions where SC 13D / SC 13G records are present.
  2. EDGAR browse endpoint for each watchlist CIK and ownership form type.

If a watchlist company has no recent ownership filings, the lane simply returns
zero records instead of fabricating sample data.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import os
import xml.etree.ElementTree as ET
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from refresh_sec_form4 import (
    Company,
    LOOKBACK_DAYS,
    REQUEST_DELAY_SECONDS,
    SEC_ARCHIVES,
    SEC_DATA,
    all_desc,
    filing_base_url,
    load_watchlist,
    now_utc,
    parse_date,
    recent_filings_by_forms,
    sec_get,
    sec_circuit_open,
)

OWNERSHIP_FORMS = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
MAX_OWNERSHIP_PER_COMPANY = 20
DISABLE_OWNERSHIP_BROWSE = os.environ.get("CAPITAL_TRACE_DISABLE_OWNERSHIP_BROWSE", "false").strip().lower() in {"1", "true", "yes"}
REFRESH_SCOPE = os.environ.get("CAPITAL_TRACE_REFRESH_SCOPE", "fast").strip().lower()


def strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", strip_tags(text)).strip()


def ownership_source_url(company: Company, filing: Dict[str, Any]) -> str:
    base = filing_base_url(company, filing.get("accession", ""))
    primary = filing.get("primary_document") or ""
    return f"{base}/{primary}" if primary else base


def get_ownership_document(company: Company, filing: Dict[str, Any]) -> Tuple[str, str]:
    """Return filing text and source URL for a 13D/G record."""
    source_url = ownership_source_url(company, filing)
    text = sec_get(source_url, as_json=False)
    if text:
        return text, source_url

    # Fallback to index lookup if primary document did not resolve.
    base = filing_base_url(company, filing.get("accession", ""))
    index = sec_get(f"{base}/index.json", as_json=True)
    if index:
        for item in index.get("directory", {}).get("item", []):
            name = item.get("name", "")
            if name.lower().endswith((".htm", ".html", ".txt")):
                url = f"{base}/{name}"
                text = sec_get(url, as_json=False)
                if text:
                    return text, url
    return "", source_url


def parse_atom_entries(text: str) -> List[Dict[str, str]]:
    """Parse EDGAR browse-edgar Atom entries without external dependencies."""
    if not text:
        return []
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return []

    def lname(tag: str) -> str:
        return tag.split("}", 1)[-1] if "}" in tag else tag

    entries = []
    for entry in root.iter():
        if lname(entry.tag) != "entry":
            continue
        item: Dict[str, str] = {}
        for child in list(entry):
            name = lname(child.tag)
            if name == "title":
                item["title"] = (child.text or "").strip()
            elif name == "updated":
                item["updated"] = (child.text or "").strip()
            elif name == "link":
                item["link"] = child.attrib.get("href", "")
            elif name == "accession-number":
                item["accession"] = (child.text or "").strip()
            elif name == "filing-date":
                item["filing_date"] = (child.text or "").strip()
            elif name == "filing-type":
                item["form"] = (child.text or "").strip()
        if item:
            entries.append(item)
    return entries


def browse_ownership_filings(company: Company) -> List[Dict[str, Any]]:
    """Best-effort watchlist ownership search through SEC browse endpoint."""
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    cutoff = now_utc() - timedelta(days=LOOKBACK_DAYS)

    for form in ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"):
        query = urllib.parse.urlencode({
            "action": "getcompany",
            "CIK": company.cik10,
            "type": form,
            "owner": "include",
            "count": "40",
            "output": "atom",
        })
        feed = sec_get(f"https://www.sec.gov/cgi-bin/browse-edgar?{query}", as_json=False)
        for entry in parse_atom_entries(feed or ""):
            accession = entry.get("accession") or ""
            if not accession:
                # Pull accession from the details URL if Atom did not include it.
                m = re.search(r"accession_number=([0-9-]+)", entry.get("link", ""))
                accession = m.group(1) if m else ""
            if not accession or accession in seen:
                continue
            filing_date = entry.get("filing_date") or entry.get("updated", "")[:10]
            filing_dt = parse_date(filing_date)
            if filing_dt and filing_dt < cutoff:
                continue
            rows.append({
                "form": entry.get("form") or form,
                "accession": accession,
                "filing_date": filing_date,
                "report_date": filing_date,
                "primary_document": "",
                "browse_link": entry.get("link", ""),
            })
            seen.add(accession)
            if len(rows) >= MAX_OWNERSHIP_PER_COMPANY:
                return rows
    return rows


def recent_ownership_filings(company: Company) -> List[Dict[str, Any]]:
    rows = recent_filings_by_forms(company, OWNERSHIP_FORMS, MAX_OWNERSHIP_PER_COMPANY)
    # In broad/S&P 500 mode, do not fan out into old browse-edgar Atom URLs for
    # every issuer/form pair. That pattern caused SEC 403 throttling. Broad mode
    # relies on the submissions endpoint and preserves prior data when throttled.
    if DISABLE_OWNERSHIP_BROWSE or REFRESH_SCOPE in {"broad", "sp500"}:
        return rows
    # Add browse results because Schedule 13D/G may not appear in a target company's
    # recent submissions in every case.
    seen = {r.get("accession") for r in rows}
    for row in browse_ownership_filings(company):
        if row.get("accession") not in seen:
            rows.append(row)
            seen.add(row.get("accession"))
        if len(rows) >= MAX_OWNERSHIP_PER_COMPANY:
            break
    return rows


def regex_first(patterns: List[str], text: str) -> str:
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            value = html.unescape(m.group(1)).strip(" :-\t\r\n")
            value = re.sub(r"\s+", " ", value)
            if value:
                return value[:160]
    return ""


def infer_issuer_name(text: str, fallback: str) -> str:
    plain = compact_text(text)
    return regex_first([
        r"Name of Issuer\s*[:\-]?\s*([^\n|]{2,120})",
        r"Issuer\s*[:\-]?\s*([^\n|]{2,120})",
        r"Item\s*1\.\s*Security and Issuer\s*([^\n]{2,120})",
    ], plain) or fallback


def infer_owner_name(text: str) -> str:
    plain = compact_text(text)
    return regex_first([
        r"Name of Reporting Person\s*[:\-]?\s*([^\n|]{2,120})",
        r"Reporting Person\s*[:\-]?\s*([^\n|]{2,120})",
        r"Filed by\s*[:\-]?\s*([^\n|]{2,120})",
    ], plain) or "Beneficial owner / reporting person"


def infer_percent_owned(text: str) -> Optional[float]:
    plain = compact_text(text)
    patterns = [
        r"Percent of Class Represented by Amount in Row \(11\)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)%",
        r"percent of class\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)%",
        r"([0-9]+(?:\.[0-9]+)?)%\s+of the outstanding",
    ]
    for pattern in patterns:
        m = re.search(pattern, plain, flags=re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None



def infer_beneficial_shares(text: str) -> Optional[float]:
    plain = compact_text(text)
    patterns = [
        r"Aggregate Amount Beneficially Owned by Each Reporting Person\s*[:\-]?\s*([0-9,]+(?:\.[0-9]+)?)",
        r"amount beneficially owned.*?([0-9,]+(?:\.[0-9]+)?)\s+(?:shares|share)",
        r"beneficially owned\s+([0-9,]+(?:\.[0-9]+)?)\s+(?:shares|share)",
        r"sole voting power\s*[:\-]?\s*([0-9,]+(?:\.[0-9]+)?)",
    ]
    for pattern in patterns:
        m = re.search(pattern, plain, flags=re.I)
        if m:
            try:
                return float(m.group(1).replace(',', ''))
            except ValueError:
                return None
    return None

def score_ownership(form: str, filed_date: str, percent_owned: Optional[float], matched_watchlist: bool) -> Tuple[int, str, str, str, List[str], str]:
    form_u = form.upper()
    is_13d = "13D" in form_u
    is_amendment = form_u.endswith("/A") or "/A" in form_u
    score = 58
    reasons = ["Source-linked SEC Schedule 13D/G ownership record"]

    if is_13d:
        score += 18
        reasons.append("Schedule 13D can indicate active or potentially influential beneficial ownership")
    else:
        score += 10
        reasons.append("Schedule 13G reports beneficial ownership, often passive or exempt")

    if is_amendment:
        score -= 8
        reasons.append("Amendment filing; review source to confirm whether ownership or intent changed")
    else:
        score += 8
        reasons.append("New ownership schedule filing rather than amendment")

    if percent_owned is not None:
        if percent_owned >= 10:
            score += 12
            reasons.append(f"Reported ownership percentage appears large: {percent_owned:.1f}%")
        elif percent_owned >= 5:
            score += 7
            reasons.append(f"Reported ownership appears above 5% threshold: {percent_owned:.1f}%")
    else:
        score -= 4
        reasons.append("Ownership percentage not extracted automatically; source review required")

    # Beneficial share amount is not always easy to extract from HTML, but include it when available.
    # The universal extraction layer will label the field as missing rather than guessing if absent.

    filed_dt = parse_date(filed_date)
    age_days = (now_utc() - filed_dt).days if filed_dt else 999
    if age_days <= 7:
        score += 8
        freshness = "Recent"
        reasons.append("Filed within the last week")
    elif age_days <= 30:
        score += 3
        freshness = "Fresh"
    else:
        freshness = "Stale"
        score -= 8

    if matched_watchlist:
        score += 6
        reasons.append("Matched to Capital Trace watchlist")

    score = max(0, min(100, int(round(score))))
    grade = "A" if score >= 84 else "B" if score >= 68 else "C" if score >= 50 else "D"
    if score >= 84 and is_13d and not is_amendment:
        action = "Research Now"
    elif score >= 62:
        action = "Watch"
    elif score >= 45:
        action = "Context Only"
    else:
        action = "Low Signal"

    caveat = "Schedule 13D/G records show reported beneficial ownership. Read the source filing to verify ownership percentage, reporting person, and intent."
    return score, grade, freshness, action, reasons, caveat


def parse_ownership_record(company: Company, filing: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text, source_url = get_ownership_document(company, filing)
    if not text:
        return None

    form = filing.get("form") or "SC 13D/G"
    issuer = infer_issuer_name(text, company.ticker)
    owner = infer_owner_name(text)
    percent_owned = infer_percent_owned(text)
    beneficial_shares = infer_beneficial_shares(text)
    filed_date = filing.get("filing_date") or filing.get("report_date") or ""
    score, grade, freshness, action, reasons, caveat = score_ownership(
        form=form,
        filed_date=filed_date,
        percent_owned=percent_owned,
        matched_watchlist=True,
    )

    is_13d = "13D" in form.upper()
    is_amendment = "/A" in form.upper()
    event_type = ("13D" if is_13d else "13G") + (" Ownership Amendment" if is_amendment else " Ownership Filing")
    if is_13d and not is_amendment:
        event_type = "New 13D Ownership Filing"
    elif not is_13d and not is_amendment:
        event_type = "New 13G Ownership Filing"

    accession = filing.get("accession") or ""
    record_id = f"sec-ownership:{accession}:{company.ticker}:{form}"
    return {
        "id": record_id,
        "record_id": record_id,
        "ticker": company.ticker,
        "company": issuer or company.ticker,
        "source_group": "SEC Ownership Thresholds",
        "source_type": f"SEC {form}",
        "source_form": form,
        "record_type": "Ownership Threshold Event",
        "event_type": event_type,
        "entity_type": "beneficial owner",
        "filer": owner,
        "role": "Beneficial owner / reporting person",
        "owner_type": "Beneficial ownership",
        "filed_date": filed_date,
        "event_date": filing.get("report_date") or filed_date,
        "transaction_date": "",
        "period_end": filing.get("report_date") or "",
        "accession_number": accession,
        "transaction_code": "",
        "shares": beneficial_shares,
        "price": None,
        "transaction_value": None,
        "ownership_percent": percent_owned,
        "score": score,
        "evidence_grade": grade,
        "freshness": freshness,
        "actionability": action,
        "watchlist_match": True,
        "rank_reasons": reasons,
        "does_not_prove": [
            "Future price movement",
            "Activist intent unless stated in the filing",
            "Complete position history",
            "Current ownership after later undisclosed changes",
        ],
        "caveat": caveat,
        "source_url": source_url,
    }


def collect_ownership_records(companies: List[Company], diagnostics: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    all_records: List[Dict[str, Any]] = []
    seen: set[str] = set()

    if diagnostics is not None:
        diagnostics["status"] = "running"
        diagnostics["companies_checked"] = 0
        diagnostics["forms_checked"] = sorted(OWNERSHIP_FORMS)
        diagnostics["lookback_days"] = LOOKBACK_DAYS
        diagnostics["filings_seen"] = 0
        diagnostics["filings_matched"] = 0
        diagnostics.setdefault("errors", [])

    for company in companies:
        if sec_circuit_open():
            if diagnostics is not None:
                diagnostics.setdefault("errors", []).append("SEC circuit breaker opened; 13D/G lane stopped early to avoid further 403s.")
            break
        print(f"[INFO] {company.ticker} ownership lane")
        if diagnostics is not None:
            diagnostics["companies_checked"] += 1
        try:
            filings = recent_ownership_filings(company)
        except Exception as exc:
            if diagnostics is not None:
                diagnostics["errors"].append(f"{company.ticker}: 13D/G lookup failed: {type(exc).__name__}: {exc}")
            continue
        print(f"[INFO]   recent 13D/G filings: {len(filings)}")
        if diagnostics is not None:
            diagnostics["filings_seen"] += len(filings)
            diagnostics["filings_matched"] += len(filings)
        for filing in filings:
            try:
                record = parse_ownership_record(company, filing)
            except Exception as exc:
                if diagnostics is not None:
                    diagnostics["errors"].append(f"{company.ticker} {filing.get('accession')}: 13D/G parse failed: {type(exc).__name__}: {exc}")
                continue
            if not record:
                continue
            rid = record.get("record_id")
            if rid in seen:
                continue
            seen.add(rid)
            form = str(record.get("source_form") or filing.get("form") or "").upper().strip()
            if "13D/A" in form:
                record["filing_type"] = "SC 13D/A"
            elif "13G/A" in form:
                record["filing_type"] = "SC 13G/A"
            elif "13D" in form:
                record["filing_type"] = "SC 13D"
            elif "13G" in form:
                record["filing_type"] = "SC 13G"
            all_records.append(record)
    if diagnostics is not None:
        diagnostics["records_added"] = len(all_records)
    return all_records


def main() -> int:
    companies = load_watchlist()
    records = collect_ownership_records(companies)
    print(json.dumps({"records_found": len(records)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
