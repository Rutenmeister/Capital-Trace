#!/usr/bin/env python3
"""
Capital Trace SEC 13F institutional holdings refresh lane.

Adds a third SEC source lane for 13F-HR / 13F-HR/A filings. This lane is
watchlist-based in two ways:
  1. It checks a curated manager watchlist, not the entire SEC universe.
  2. It tries to map 13F holdings back to the existing company watchlist by
     issuer-name matching when possible. Holdings that cannot be mapped are
     still represented with their CUSIP so they are not fabricated as tickers.

Important integrity notes:
- 13F holdings are delayed and usually long U.S.-listed securities only.
- 13F does not show shorts or full economic exposure.
- This lane does not yet compare quarter-over-quarter changes. It creates
  current reported-holding records and labels them honestly as delayed context.
"""

from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from refresh_sec_form4 import (
    Company,
    LOOKBACK_DAYS,
    SEC_ARCHIVES,
    DATA_DIR,
    all_desc,
    company_submissions,
    filing_base_url,
    load_watchlist,
    now_utc,
    parse_date,
    recent_filings_by_forms,
    sec_get,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
INSTITUTIONAL_WATCHLIST_PATHS = [
    DATA_DIR / "institutional_watchlist.json",
    CONFIG_DIR / "institutional_watchlist.json",
]

INSTITUTIONAL_FORMS = {"13F-HR", "13F-HR/A"}
MAX_13F_FILINGS_PER_MANAGER = int(__import__("os").environ.get("CAPITAL_TRACE_MAX_13F_FILINGS_PER_MANAGER", "2"))
MAX_13F_HOLDINGS_PER_MANAGER = int(__import__("os").environ.get("CAPITAL_TRACE_MAX_13F_HOLDINGS_PER_MANAGER", "25"))


@dataclass
class Manager:
    name: str
    cik: str

    @property
    def cik10(self) -> str:
        digits = re.sub(r"\D", "", self.cik)
        return digits.zfill(10)

    @property
    def cik_int(self) -> str:
        return str(int(self.cik10))


# Conservative fallback list. Users can override with data/institutional_watchlist.json.
# If any CIK is wrong or stale, diagnostics will show errors / no records instead of
# pretending the lane worked.
DEFAULT_MANAGERS = [
    {"name": "Berkshire Hathaway Inc", "cik": "0001067983"},
    {"name": "Renaissance Technologies LLC", "cik": "0001037389"},
    {"name": "Bridgewater Associates LP", "cik": "0001350694"},
    {"name": "Citadel Advisors LLC", "cik": "0001423053"},
    {"name": "ARK Investment Management LLC", "cik": "0001697748"},
    {"name": "Pershing Square Capital Management LP", "cik": "0001336528"},
]


def load_managers() -> List[Manager]:
    raw: List[Dict[str, Any]] | None = None
    for path in INSTITUTIONAL_WATCHLIST_PATHS:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            break
    if raw is None:
        raw = DEFAULT_MANAGERS
    managers: List[Manager] = []
    for item in raw:
        if item.get("name") and item.get("cik"):
            managers.append(Manager(name=str(item["name"]), cik=str(item["cik"])))
    return managers


def normalize_name(value: str) -> str:
    text = str(value or "").upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    stop = {
        "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "PLC", "LLC", "LP",
        "THE", "CLASS", "CL", "COM", "COMMON", "STOCK", "SHS", "ORD", "NEW", "HOLDINGS", "HLDGS",
    }
    parts = [p for p in text.split() if p and p not in stop]
    return " ".join(parts)


def build_issuer_map(companies: List[Company]) -> List[Dict[str, str]]:
    issuers: List[Dict[str, str]] = []
    for company in companies:
        name = company.name or company.ticker
        if not company.name:
            try:
                payload = company_submissions(company)
                name = payload.get("name") or payload.get("entityName") or company.ticker
            except Exception:
                name = company.ticker
        issuers.append({
            "ticker": company.ticker,
            "company": name,
            "normalized": normalize_name(name),
        })
    return issuers


def match_watchlist_issuer(name_of_issuer: str, issuer_map: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    target = normalize_name(name_of_issuer)
    if not target:
        return None
    for item in issuer_map:
        n = item.get("normalized", "")
        if not n:
            continue
        # Avoid very short accidental matches.
        if len(n) >= 4 and (n in target or target in n):
            return item
        # Compare first two meaningful words for common issuer suffix noise.
        a = " ".join(target.split()[:2])
        b = " ".join(n.split()[:2])
        if len(a) >= 6 and a == b:
            return item
    return None


def manager_company(manager: Manager) -> Company:
    return Company(ticker=manager.name[:20].upper(), cik=manager.cik10)


def recent_13f_filings(manager: Manager) -> List[Dict[str, Any]]:
    company = manager_company(manager)
    return recent_filings_by_forms(company, INSTITUTIONAL_FORMS, MAX_13F_FILINGS_PER_MANAGER, LOOKBACK_DAYS)


def find_13f_info_table_url(manager: Manager, filing: Dict[str, Any]) -> Tuple[str, str]:
    company = manager_company(manager)
    base = filing_base_url(company, filing.get("accession", ""))
    index = sec_get(f"{base}/index.json", as_json=True)
    candidates: List[str] = []
    if index:
        for item in index.get("directory", {}).get("item", []):
            name = item.get("name", "")
            lname = name.lower()
            if not name:
                continue
            if lname.endswith(".xml") and ("info" in lname or "13f" in lname or "table" in lname):
                candidates.append(name)
        # Fallback: any XML after likely primary document.
        if not candidates:
            for item in index.get("directory", {}).get("item", []):
                name = item.get("name", "")
                if name.lower().endswith(".xml"):
                    candidates.append(name)
    for name in candidates:
        url = f"{base}/{name}"
        text = sec_get(url, as_json=False)
        if text and "infoTable" in text:
            return text, url

    # Final fallback to primary document or complete submission txt.
    primary = filing.get("primary_document") or ""
    if primary:
        url = f"{base}/{primary}"
        text = sec_get(url, as_json=False)
        if text:
            return text, url
    txt_url = f"{SEC_ARCHIVES}/{manager.cik_int}/{str(filing.get('accession','')).replace('-', '')}/{filing.get('accession','')}.txt"
    text = sec_get(txt_url, as_json=False)
    return text or "", txt_url


def tag(node: ET.Element) -> str:
    return node.tag.split("}", 1)[-1] if "}" in node.tag else node.tag


def child_text(node: ET.Element, names: set[str]) -> str:
    for child in node.iter():
        if child is node:
            continue
        if tag(child) in names and child.text:
            return child.text.strip()
    return ""


def _plausible_price(price: Optional[float]) -> bool:
    if price is None:
        return False
    # Broad sanity band. Allows high-priced securities while rejecting obvious
    # 1,000x unit errors such as $200,000/share for AMZN/NVDA.
    return 0.05 <= price <= 100_000


def normalize_13f_value(value_raw: str, shares: Optional[int] = None) -> tuple[Optional[int], Optional[int], str]:
    """Return (reported_value_field, market_value_usd, basis).

    SEC 13F values are *usually* reported in thousands of dollars, but EDGAR
    XML/table variants and legacy Capital Trace preserved data have shown both
    raw-thousands and already-USD values. The only safe interpretation is to use
    shares as a sanity check when available: choose the unit treatment that gives
    a plausible implied price per share.
    """
    if not value_raw:
        return None, None, "missing"
    cleaned = str(value_raw).replace(",", "").strip()
    if not cleaned:
        return None, None, "missing"
    try:
        raw_value = int(float(cleaned))
    except ValueError:
        return None, None, "unparsed"

    if shares and shares > 0:
        price_if_usd = raw_value / shares
        price_if_thousands = (raw_value * 1000) / shares
        usd_plausible = _plausible_price(price_if_usd)
        thousands_plausible = _plausible_price(price_if_thousands)
        if usd_plausible and not thousands_plausible:
            return raw_value, raw_value, "13F value interpreted as USD by implied-price sanity check"
        if thousands_plausible and not usd_plausible:
            return raw_value, raw_value * 1000, "SEC 13F value field converted from thousands of dollars"
        if usd_plausible and thousands_plausible:
            # Official basis is thousands; prefer it only when both are plausible.
            return raw_value, raw_value * 1000, "SEC 13F value field converted from thousands of dollars"

    # Fallback without shares: avoid single-holding trillion-dollar displays.
    if raw_value >= 2_000_000_000:
        return raw_value, raw_value, "13F value treated as USD because thousands conversion would be implausibly large"
    return raw_value, raw_value * 1000, "SEC 13F value field converted from thousands of dollars"


def parse_13f_holdings(xml_text: str) -> List[Dict[str, Any]]:
    if not xml_text:
        return []
    # Some full submission documents wrap XML in text. Try to isolate informationTable.
    start = xml_text.find("<informationTable")
    if start == -1:
        start = xml_text.find("<XML>")
    candidate = xml_text[start:] if start >= 0 else xml_text
    if "<XML>" in candidate:
        m = re.search(r"<XML>([\s\S]*?)</XML>", candidate, flags=re.I)
        if m:
            candidate = m.group(1)
    try:
        root = ET.fromstring(candidate.encode("utf-8"))
    except ET.ParseError:
        # Last-chance cleanup for leading SEC text.
        m = re.search(r"(<informationTable[\s\S]*?</informationTable>)", xml_text, flags=re.I)
        if not m:
            return []
        try:
            root = ET.fromstring(m.group(1).encode("utf-8"))
        except ET.ParseError:
            return []

    rows: List[Dict[str, Any]] = []
    for node in root.iter():
        if tag(node) != "infoTable":
            continue
        name = child_text(node, {"nameOfIssuer"})
        title = child_text(node, {"titleOfClass"})
        cusip = child_text(node, {"cusip"})
        value_raw = child_text(node, {"value"})
        shares_raw = child_text(node, {"sshPrnamt"})
        put_call = child_text(node, {"putCall"})
        investment_discretion = child_text(node, {"investmentDiscretion"})
        try:
            shares = int(float(shares_raw.replace(",", ""))) if shares_raw else None
        except ValueError:
            shares = None
        reported_value_raw, market_value, value_basis = normalize_13f_value(value_raw, shares)
        if name or cusip:
            rows.append({
                "name_of_issuer": name,
                "title_of_class": title,
                "cusip": cusip,
                "reported_market_value_thousands": reported_value_raw if value_basis == "SEC 13F value field converted from thousands of dollars" else None,
                "reported_market_value_usd": market_value,
                "market_value": market_value,
                "market_value_unit_basis": value_basis,
                "shares": shares,
                "put_call": put_call,
                "investment_discretion": investment_discretion,
            })
    rows.sort(key=lambda r: r.get("market_value") or 0, reverse=True)
    return rows


def score_13f_record(holding: Dict[str, Any], filed_date: str, report_date: str, watchlist_match: bool, rank: int) -> Tuple[int, str, str, str, List[str], str]:
    score = 46
    reasons = ["SEC 13F reported institutional holding", "13F records are delayed and should be treated as portfolio context"]
    market_value = holding.get("market_value") or 0
    if market_value >= 1_000_000_000:
        score += 14
        reasons.append("Reported market value is above $1B")
    elif market_value >= 100_000_000:
        score += 9
        reasons.append("Reported market value is above $100M")
    elif market_value >= 10_000_000:
        score += 4
    if rank <= 10:
        score += 8
        reasons.append("Top reported holding by market value within this 13F filing")
    if watchlist_match:
        score += 10
        reasons.append("Issuer appears to match the Capital Trace company watchlist")
    if holding.get("put_call"):
        score -= 8
        reasons.append("Derivative put/call field present; review source context")
    filed_dt = parse_date(filed_date)
    if filed_dt:
        age = (now_utc() - filed_dt).days
        if age <= 30:
            score += 3
        elif age > 90:
            score -= 8
    score = max(0, min(100, int(round(score))))
    grade = "A" if score >= 84 else "B" if score >= 68 else "C" if score >= 50 else "D"
    freshness = "Delayed"
    action = "Watch" if score >= 68 else "Context Only" if score >= 45 else "Low Signal"
    caveat = "13F filings are delayed, generally show long U.S. listed holdings, and do not show shorts or current positions. Use as institutional context, not live trade evidence."
    return score, grade, freshness, action, reasons, caveat


def format_money(value: Optional[int]) -> str:
    if value is None:
        return "unknown value"
    if value >= 1_000_000_000:
        return f"${value/1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"${value/1_000_000:.1f}M"
    return f"${value:,.0f}"


def collect_13f_records(companies: List[Company], diagnostics: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    managers = load_managers()
    issuer_map = build_issuer_map(companies)
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()

    if diagnostics is not None:
        diagnostics["status"] = "running"
        diagnostics["companies_checked"] = len(managers)  # managers checked; kept compatible with UI wording.
        diagnostics["managers_checked"] = 0
        diagnostics["forms_checked"] = sorted(INSTITUTIONAL_FORMS)
        diagnostics["lookback_days"] = LOOKBACK_DAYS
        diagnostics["filings_seen"] = 0
        diagnostics["filings_matched"] = 0
        diagnostics["holdings_parsed"] = 0
        diagnostics.setdefault("errors", [])
        diagnostics["note"] = "13F lane is manager-watchlist based; it does not scan all 13F managers."

    for manager in managers:
        print(f"[INFO] {manager.name} 13F lane")
        if diagnostics is not None:
            diagnostics["managers_checked"] += 1
        try:
            filings = recent_13f_filings(manager)
        except Exception as exc:
            if diagnostics is not None:
                diagnostics["errors"].append(f"{manager.name}: 13F lookup failed: {type(exc).__name__}: {exc}")
            continue
        if diagnostics is not None:
            diagnostics["filings_seen"] += len(filings)
            diagnostics["filings_matched"] += len(filings)
        print(f"[INFO]   recent 13F filings: {len(filings)}")

        for filing in filings:
            accession = filing.get("accession") or ""
            try:
                info_text, source_url = find_13f_info_table_url(manager, filing)
                holdings = parse_13f_holdings(info_text)
            except Exception as exc:
                if diagnostics is not None:
                    diagnostics["errors"].append(f"{manager.name} {accession}: 13F parse failed: {type(exc).__name__}: {exc}")
                continue
            if diagnostics is not None:
                diagnostics["holdings_parsed"] += len(holdings)
            print(f"[INFO]   holdings parsed: {len(holdings)}")
            total_reported_value = sum((h.get("market_value") or 0) for h in holdings)
            for rank, holding in enumerate(holdings[:MAX_13F_HOLDINGS_PER_MANAGER], start=1):
                match = match_watchlist_issuer(holding.get("name_of_issuer", ""), issuer_map)
                # 13F holdings are often keyed by CUSIP, but CUSIP should not be the
                # primary display title. If ticker is unmapped, leave ticker blank and
                # let the UI use issuer/company as the headline while showing CUSIP as
                # a key figure.
                ticker = match["ticker"] if match else ""
                company_name = match["company"] if match else holding.get("name_of_issuer") or "Unknown issuer"
                display_security = ticker or company_name or f"CUSIP {holding.get('cusip') or '-'}"
                watchlist_match = bool(match)
                filed_date = filing.get("filing_date") or ""
                report_date = filing.get("report_date") or filed_date
                score, grade, freshness, action, reasons, caveat = score_13f_record(holding, filed_date, report_date, watchlist_match, rank)
                market_value = holding.get("market_value")
                position_weight = (market_value / total_reported_value * 100) if market_value and total_reported_value else None
                event_type = "13F Top Holding" if rank <= 10 else "13F Reported Holding"
                record_id = f"sec-13f:{accession}:{manager.cik10}:{holding.get('cusip') or rank}"
                if record_id in seen:
                    continue
                seen.add(record_id)
                records.append({
                    "id": record_id,
                    "record_id": record_id,
                    "ticker": ticker,
                    "company": company_name,
                    "source_group": "SEC Institutional Holdings",
                    "source_type": f"SEC {filing.get('form') or '13F-HR'}",
                    "source_form": filing.get("form") or "13F-HR",
                    "filing_type": "13F-HR/A" if "/A" in str(filing.get("form") or "").upper() else "13F-HR",
                    "record_type": "Institutional Position Record",
                    "event_type": event_type,
                    "entity_type": "institutional manager",
                    "filer": manager.name,
                    "manager_name": manager.name,
                    "manager_cik": manager.cik10,
                    "role": "13F institutional investment manager",
                    "owner_type": "Institutional holdings report",
                    "filed_date": filed_date,
                    "event_date": report_date,
                    "transaction_date": "",
                    "period_end": report_date,
                    "accession_number": accession,
                    "transaction_code": "",
                    "shares": holding.get("shares"),
                    "price": None,
                    "transaction_value": market_value,
                    "market_value": market_value,
                    "reported_market_value_thousands": holding.get("reported_market_value_thousands"),
                    "reported_market_value_usd": holding.get("reported_market_value_usd"),
                    "market_value_unit_basis": holding.get("market_value_unit_basis") or "SEC 13F value field converted from thousands of dollars",
                    "cusip": holding.get("cusip"),
                    "position_rank": rank,
                    "position_weight": position_weight,
                    "position_value_label": format_money(market_value),
                    "value_basis_label": holding.get("market_value_unit_basis") or "13F value converted from thousands",
                    "change_vs_prior": "Pending comparison",
                    "score": score,
                    "evidence_grade": grade,
                    "freshness": freshness,
                    "actionability": action,
                    "watchlist_match": watchlist_match,
                    "rank_reasons": reasons,
                    "does_not_prove": [
                        "Current position ownership",
                        "Short exposure or hedges",
                        "Future price movement",
                        "Complete portfolio or real-time manager intent",
                    ],
                    "caveat": caveat,
                    "source_url": source_url,
                    "vital_point": f"13F holding: {manager.name} reported {holding.get('shares'):,} shares of {display_security}, market value {format_money(market_value)}, as of {report_date}." if holding.get("shares") is not None else f"13F holding: {manager.name} reported {display_security}, market value {format_money(market_value)}, as of {report_date}.",
                })
    if diagnostics is not None:
        diagnostics["records_added"] = len(records)
    return records


def main() -> int:
    companies = load_watchlist()
    records = collect_13f_records(companies)
    print(json.dumps({"records_found": len(records)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
