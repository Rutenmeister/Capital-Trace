#!/usr/bin/env python3
"""SEC daily-index discovery lane for Capital Trace.

This is a low-request broad-market fallback. Instead of checking hundreds of
companies one by one, it downloads SEC daily master index files and filters for
forms and CIKs Capital Trace cares about. Records are intentionally labeled as
index-discovery records when a filing has not been parsed into detailed figures.
"""
from __future__ import annotations

import os
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from refresh_sec_form4 import Company, now_utc, parse_date, sec_get, get_sec_request_stats

SEC_ARCHIVES_ROOT = "https://www.sec.gov/Archives"
INDEX_LOOKBACK_DAYS = int(os.environ.get("CAPITAL_TRACE_INDEX_LOOKBACK_DAYS", "14"))
INDEX_MAX_DAYS = int(os.environ.get("CAPITAL_TRACE_INDEX_MAX_DAYS", str(INDEX_LOOKBACK_DAYS)))
SUPPORTED_INDEX_FORMS = {"4", "4/A", "144", "144/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}


def quarter_for_month(month: int) -> int:
    return ((month - 1) // 3) + 1


def master_index_url(dt, *, gz: bool = False) -> str:
    qtr = quarter_for_month(dt.month)
    ymd = dt.strftime("%Y%m%d")
    suffix = ".idx.gz" if gz else ".idx"
    return f"{SEC_ARCHIVES_ROOT}/edgar/daily-index/{dt.year}/QTR{qtr}/master.{ymd}{suffix}"




def full_index_url(dt, *, gz: bool = False) -> str:
    qtr = quarter_for_month(dt.month)
    suffix = ".idx.gz" if gz else ".idx"
    return f"{SEC_ARCHIVES_ROOT}/edgar/full-index/{dt.year}/QTR{qtr}/master{suffix}"


def load_index_text_for_day(dt) -> tuple[str | None, str, bool]:
    """Try SEC index sources from lowest-request to daily fallback.

    Returns (text, source_url, is_full_index). The quarterly full-index is a
    single request that usually includes filings through the previous business
    day, so it is the safest first choice. Daily index is only a fallback.
    """
    candidates = []
    if os.environ.get("CAPITAL_TRACE_USE_FULL_INDEX", "true").strip().lower() in {"1", "true", "yes"}:
        candidates.append((full_index_url(dt), True))
        candidates.append((full_index_url(dt, gz=True), True))
    if os.environ.get("CAPITAL_TRACE_DISABLE_DAILY_INDEX", "false").strip().lower() not in {"1", "true", "yes"}:
        candidates.append((master_index_url(dt), False))
        candidates.append((master_index_url(dt, gz=True), False))

    for url, is_full in candidates:
        text = sec_get(url, as_json=False)
        if text and "CIK|Company Name|Form Type|Date Filed|Filename" in text:
            return text, url, is_full
    return None, "", False

def accession_from_filename(filename: str) -> str:
    # filename: edgar/data/320193/0000320193-26-000028.txt
    base = filename.rsplit("/", 1)[-1]
    return base.replace(".txt", "")


def source_url_from_filename(filename: str) -> str:
    return f"{SEC_ARCHIVES_ROOT}/{filename}"


def parse_master_index(text: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not text:
        return rows
    start = False
    for line in text.splitlines():
        if not start:
            if line.startswith("-----"):
                start = True
            continue
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik, company, form, filed_date, filename = [p.strip() for p in parts]
        if not cik or not form or not filename:
            continue
        rows.append({
            "cik": re.sub(r"\D", "", cik).zfill(10),
            "company": company,
            "form": form.upper(),
            "filed_date": filed_date,
            "filename": filename,
            "accession_number": accession_from_filename(filename),
            "source_url": source_url_from_filename(filename),
        })
    return rows


def lane_for_form(form: str) -> Tuple[str, str, str, str]:
    f = form.upper()
    if f in {"4", "4/A"}:
        return "SEC Insider Ownership", "SEC Form 4", "Direct Insider Capital", "Form 4 Index Match"
    if f in {"144", "144/A"}:
        return "SEC Proposed Sales", "SEC Form 144", "Proposed Sale Notice", "Form 144 Index Match"
    if f in {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}:
        return "SEC 13D/G Ownership", "SEC 13D/G", "Ownership Threshold Event", "13D/G Index Match"
    return "SEC Filing Discovery", f"SEC {form}", "SEC Filing", "SEC Index Match"


def score_index_record(form: str) -> tuple[int, str, str]:
    f = form.upper()
    if f in {"SC 13D", "SC 13D/A"}:
        return 82, "B", "Research Now"
    if f in {"SC 13G", "SC 13G/A"}:
        return 74, "B", "Watch"
    if f in {"4", "4/A"}:
        return 68, "B", "Watch"
    if f in {"144", "144/A"}:
        return 64, "C", "Watch"
    return 50, "C", "Context Only"


def collect_index_discovery_records(companies: List[Company], diagnostics: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cik_map = {c.cik10: c for c in companies}
    forms_seen = 0
    forms_matched = 0
    index_days_attempted = 0
    index_days_loaded = 0
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()
    cutoff = now_utc() - timedelta(days=INDEX_LOOKBACK_DAYS)

    if diagnostics is not None:
        diagnostics["status"] = "running"
        diagnostics["lane"] = "index_discovery"
        diagnostics["companies_checked"] = len(companies)
        diagnostics["forms_checked"] = sorted(SUPPORTED_INDEX_FORMS)
        diagnostics["lookback_days"] = INDEX_LOOKBACK_DAYS
        diagnostics["filings_seen"] = 0
        diagnostics["filings_matched"] = 0
        diagnostics["records_added"] = 0
        diagnostics["index_days_attempted"] = 0
        diagnostics["index_days_loaded"] = 0
        diagnostics.setdefault("errors", [])
        diagnostics["note"] = "SEC daily-index discovery scan. Low-request broad coverage; detailed figures require source parser follow-up."

    # Prefer the quarterly full-index first. It is one request and can cover
    # the current quarter through the previous business day. If it loads, we do
    # not loop over daily files. If it fails, we fall back to a small daily scan.
    current = now_utc().date()
    loaded_full_index = False
    loaded_urls: List[str] = []

    text, source_url, is_full_index = load_index_text_for_day(current)
    if text:
        index_days_attempted = 1
        index_days_loaded = 1
        loaded_full_index = is_full_index
        loaded_urls.append(source_url)
        rows = parse_master_index(text)
        forms_seen += len(rows)
        for row in rows:
            form = row["form"]
            if form not in SUPPORTED_INDEX_FORMS:
                continue
            if row["cik"] not in cik_map:
                continue
            filed_dt = parse_date(row.get("filed_date"))
            if filed_dt and filed_dt < cutoff:
                continue
            company = cik_map[row["cik"]]
            forms_matched += 1
            source_group, source_type, record_type, event_type = lane_for_form(form)
            score, grade, action = score_index_record(form)
            accession = row["accession_number"]
            rid = f"sec-index:{form}:{accession}:{company.ticker}"
            if rid in seen:
                continue
            seen.add(rid)
            records.append({
                "id": rid,
                "record_id": rid,
                "ticker": company.ticker,
                "company": company.name or row.get("company") or company.ticker,
                "source_group": source_group,
                "source_type": source_type,
                "source_form": form,
                "filing_type": form,
                "record_type": record_type,
                "event_type": event_type,
                "entity_type": "issuer",
                "filer": row.get("company") or company.name or company.ticker,
                "role": "SEC index matched issuer filing",
                "owner_type": "Index discovery",
                "filed_date": row.get("filed_date") or "",
                "event_date": row.get("filed_date") or "",
                "transaction_date": "",
                "period_end": "",
                "accession_number": accession,
                "transaction_code": "",
                "shares": None,
                "price": None,
                "transaction_value": None,
                "market_value": None,
                "score": score,
                "evidence_grade": grade,
                "freshness": "Recent",
                "actionability": action,
                "watchlist_match": True,
                "rank_reasons": [
                    "Matched SEC master index for a Capital Trace issuer",
                    "Low-request discovery scan found a supported filing form",
                    "Detailed transaction figures require parser/source-document follow-up",
                ],
                "does_not_prove": [
                    "Transaction details beyond the filing existence",
                    "Future price movement",
                    "A complete investment thesis",
                ],
                "caveat": "This is an SEC index discovery record. It proves a matching filing appeared in the SEC index, but it may not contain all parsed transaction figures yet.",
                "source_url": row.get("source_url") or "",
                "vital_point": f"SEC index match: {company.ticker} had a {form} filing accepted on {row.get('filed_date') or 'unknown date'}.",
                "key_figures": {
                    "Filing form": form,
                    "Filed date": row.get("filed_date") or "",
                    "Accession": accession,
                    "Discovery source": "SEC quarterly full index" if is_full_index else "SEC daily master index",
                },
                "person_entity": {
                    "Filer / entity": row.get("company") or company.name or company.ticker,
                    "Role / relationship": "SEC filing issuer / subject CIK",
                    "Issuer": company.name or company.ticker,
                },
                "source_trust": {
                    "Filing type": form,
                    "Source lane": source_group,
                    "Filed date": row.get("filed_date") or "",
                    "Accession": accession,
                    "Discovery method": "SEC quarterly full index" if is_full_index else "SEC daily master index",
                },
                "extraction_quality": {
                    "status": "partial",
                    "parsed_fields": ["accession_number", "filed_date", "filing_type", "source_url", "ticker", "company"],
                    "missing_fields": ["shares", "price", "transaction_value"],
                    "notes": ["Index discovery record; source parser can enrich later."],
                },
            })
    else:
        # No index source loaded. Record the single failed attempt.
        index_days_attempted = 1

    if diagnostics is not None:
        diagnostics["sec_request_stats"] = get_sec_request_stats()
        diagnostics["filings_seen"] = forms_seen
        diagnostics["filings_matched"] = forms_matched
        diagnostics["records_added"] = len(records)
        diagnostics["index_days_attempted"] = index_days_attempted
        diagnostics["index_days_loaded"] = index_days_loaded
        diagnostics["loaded_full_index"] = loaded_full_index
        diagnostics["loaded_index_urls"] = loaded_urls
        if records:
            diagnostics["status"] = "ok"
            diagnostics["note"] = f"SEC daily-index scan found {len(records)} supported filing matches."
        elif index_days_loaded:
            diagnostics["status"] = "checked_no_records"
            diagnostics["note"] = "SEC daily-index files loaded, but no supported filing matches were found for this watchlist/window."
        else:
            diagnostics["status"] = "failed"
            stats = get_sec_request_stats()
            if stats.get("http_403_count"):
                diagnostics.setdefault("errors", []).append(
                    f"SEC access denied while loading daily-index files: {stats.get('http_403_count')} HTTP 403 responses; circuit_open={stats.get('circuit_open')}"
                )
                diagnostics["note"] = "SEC daily-index scan could not load index files because SEC denied the requests. Previous data should be preserved; this is source access failure, not no-records."
            else:
                diagnostics.setdefault("errors", []).append("No SEC daily-index files loaded for the requested window.")
                diagnostics["note"] = "SEC daily-index scan could not load index files."
    return records
