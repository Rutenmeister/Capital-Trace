#!/usr/bin/env python3
"""
Capital Trace SEC Form 4 refresh script.

Reads data/watchlist.json, checks each watchlist company's recent EDGAR filings,
parses recent Form 4 / 4/A ownership XML, normalizes transactions into the
Capital Trace record format, and writes:
  - data/capital_trace.json
  - data/capital_trace_data.js

No paid API keys are required. This uses SEC public EDGAR endpoints politely.
Set CAPITAL_TRACE_USER_AGENT to a descriptive value before running in production,
for example: "CapitalTrace/0.5 your-email@example.com".
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import html
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"
CONFIG_DIR = ROOT / "config"
SP500_WATCHLIST_PATH = CONFIG_DIR / "sp500_watchlist.json"
OUTPUT_JSON = DATA_DIR / "capital_trace.json"
OUTPUT_JS = DATA_DIR / "capital_trace_data.js"

USER_AGENT = os.environ.get("CAPITAL_TRACE_USER_AGENT", "CapitalTrace/0.6 contact@example.com")
REQUEST_DELAY_SECONDS = float(os.environ.get("CAPITAL_TRACE_REQUEST_DELAY", "0.25"))
MAX_FORM4_PER_COMPANY = int(os.environ.get("CAPITAL_TRACE_MAX_FORM4_PER_COMPANY", "30"))
LOOKBACK_DAYS = int(os.environ.get("CAPITAL_TRACE_LOOKBACK_DAYS", "180"))
MAX_OUTPUT_RECORDS = int(os.environ.get("CAPITAL_TRACE_MAX_OUTPUT_RECORDS", "500"))

SEC_DATA = "https://data.sec.gov"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SEC_COMPANY_TICKERS_JSON = "https://www.sec.gov/files/company_tickers.json"
WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SEC_GET_CACHE: Dict[str, Any] = {}


@dataclass
class Company:
    ticker: str
    cik: str
    name: str = ""

    @property
    def cik10(self) -> str:
        digits = re.sub(r"\D", "", self.cik)
        return digits.zfill(10)

    @property
    def cik_int(self) -> str:
        return str(int(self.cik10))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def sec_get(url: str, *, as_json: bool = False) -> Any:
    cache_key = f"json:{url}" if as_json else f"text:{url}"
    if cache_key in SEC_GET_CACHE:
        return SEC_GET_CACHE[cache_key]
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/xml,application/xml,text/html,*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            if as_json:
                parsed = json.loads(text)
                SEC_GET_CACHE[cache_key] = parsed
                return parsed
            SEC_GET_CACHE[cache_key] = text
            return text
    except urllib.error.HTTPError as exc:
        print(f"[WARN] SEC HTTP {exc.code}: {url}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"[WARN] SEC request failed: {url} :: {exc}", file=sys.stderr)
        return None


def _clean_html_cell(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_ticker_symbol(symbol: str) -> str:
    # Keep dot tickers human-readable in the UI. SEC CIK mapping is CIK-based after resolution.
    return str(symbol or "").strip().upper().replace("/", ".")


def fetch_sp500_watchlist() -> List[Company]:
    """Fetch the current S&P 500 table and return issuer CIKs.

    This avoids maintaining a stale 500-name JSON by hand. The Wikipedia table
    includes ticker, security name, and CIK; if the table shape changes, we fall
    back to config/sp500_watchlist.json or data/watchlist.json.
    """
    text = sec_get(WIKIPEDIA_SP500_URL, as_json=False)
    if not text:
        return []
    companies: List[Company] = []
    seen: set[str] = set()
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", text, flags=re.I):
        cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row, flags=re.I)
        if len(cells) < 7:
            continue
        ticker = _normalize_ticker_symbol(_clean_html_cell(cells[0]))
        name = _clean_html_cell(cells[1])
        cik = re.sub(r"\D", "", _clean_html_cell(cells[6]))
        if not ticker or not cik:
            continue
        key = cik.zfill(10)
        if key in seen:
            continue
        seen.add(key)
        companies.append(Company(ticker=ticker, cik=key, name=name))
    if len(companies) < 400:
        print(f"[WARN] S&P 500 fetch returned only {len(companies)} companies; falling back to local watchlist", file=sys.stderr)
        return []
    return companies


def load_watchlist_from_path(path: Path) -> List[Company]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    companies: List[Company] = []
    for item in raw:
        if not item.get("ticker") or not item.get("cik"):
            continue
        companies.append(Company(ticker=str(item["ticker"]).upper(), cik=str(item["cik"]), name=str(item.get("name") or item.get("company") or "")))
    return companies


def load_watchlist() -> List[Company]:
    mode = os.environ.get("CAPITAL_TRACE_ISSUER_WATCHLIST_MODE", "file").strip().lower()
    if mode in {"sp500", "s&p500", "s_and_p_500"}:
        companies = fetch_sp500_watchlist()
        if companies:
            print(f"[OK] issuer watchlist mode=sp500 companies={len(companies)}", file=sys.stderr)
            return companies
        if SP500_WATCHLIST_PATH.exists():
            return load_watchlist_from_path(SP500_WATCHLIST_PATH)
    if not WATCHLIST_PATH.exists():
        raise FileNotFoundError(f"Missing {WATCHLIST_PATH}")
    return load_watchlist_from_path(WATCHLIST_PATH)


def company_submissions(company: Company) -> Dict[str, Any]:
    url = f"{SEC_DATA}/submissions/CIK{company.cik10}.json"
    payload = sec_get(url, as_json=True)
    return payload or {}


def recent_filings_by_forms(company: Company, accepted_forms: set[str], max_rows: int, lookback_days: int = LOOKBACK_DAYS) -> List[Dict[str, Any]]:
    payload = company_submissions(company)
    if not payload:
        return []

    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])
    cutoff = now_utc() - timedelta(days=lookback_days)

    rows: List[Dict[str, Any]] = []
    accepted_upper = {form.upper() for form in accepted_forms}
    for i, form in enumerate(forms):
        form_value = str(form or "").upper().strip()
        if form_value not in accepted_upper:
            continue
        filing_date = filing_dates[i] if i < len(filing_dates) else ""
        filing_dt = parse_date(filing_date)
        if filing_dt and filing_dt < cutoff:
            continue
        rows.append({
            "form": form,
            "accession": accessions[i] if i < len(accessions) else "",
            "filing_date": filing_date,
            "report_date": report_dates[i] if i < len(report_dates) else "",
            "primary_document": primary_docs[i] if i < len(primary_docs) else "",
        })
        if len(rows) >= max_rows:
            break
    return rows


def recent_filings(company: Company) -> List[Dict[str, Any]]:
    return recent_filings_by_forms(company, {"4", "4/A"}, MAX_FORM4_PER_COMPANY)


def filing_base_url(company: Company, accession: str) -> str:
    nodash = accession.replace("-", "")
    return f"{SEC_ARCHIVES}/{company.cik_int}/{nodash}"


def get_filing_document(company: Company, filing: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Return XML/HTML text and source URL for a Form 4 filing."""
    base = filing_base_url(company, filing["accession"])
    primary = filing.get("primary_document") or ""

    candidates: List[str] = []
    if primary:
        candidates.append(primary)

    # Index JSON lets us find XML ownership docs even when primaryDocument is HTML.
    index = sec_get(f"{base}/index.json", as_json=True)
    if index:
        for item in index.get("directory", {}).get("item", []):
            name = item.get("name", "")
            lower = name.lower()
            if lower.endswith(".xml") and "filingsummary" not in lower:
                if name not in candidates:
                    candidates.insert(0, name)

    if not candidates:
        candidates = ["ownership.xml"]

    for doc in candidates:
        url = f"{base}/{doc}"
        text = sec_get(url, as_json=False)
        if text and ("ownershipDocument" in text or "<XML>" in text or doc.lower().endswith(".xml")):
            return text, url
    return None, f"{base}/{primary or ''}"


def xml_from_text(text: str) -> Optional[ET.Element]:
    if not text:
        return None
    stripped = text.strip()
    try:
        return ET.fromstring(stripped.encode("utf-8"))
    except ET.ParseError:
        pass

    # Many EDGAR HTML docs include XML inside tags. Extract ownershipDocument.
    match = re.search(r"(<ownershipDocument[\s\S]*?</ownershipDocument>)", text, re.IGNORECASE)
    if match:
        candidate = match.group(1)
        try:
            return ET.fromstring(candidate.encode("utf-8"))
        except ET.ParseError:
            return None
    return None


def local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def children(elem: ET.Element, name: str) -> List[ET.Element]:
    return [child for child in list(elem) if local(child.tag) == name]


def first(elem: ET.Element, path: Iterable[str]) -> Optional[ET.Element]:
    current: Optional[ET.Element] = elem
    for name in path:
        if current is None:
            return None
        matches = children(current, name)
        current = matches[0] if matches else None
    return current


def text_at(elem: ET.Element, path: Iterable[str], default: str = "") -> str:
    found = first(elem, path)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def all_desc(elem: ET.Element, name: str) -> List[ET.Element]:
    return [node for node in elem.iter() if local(node.tag) == name]


def to_float(value: str) -> Optional[float]:
    if value is None:
        return None
    s = str(value).replace(",", "").replace("$", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def relationship_role(owner: ET.Element) -> str:
    rel = first(owner, ["reportingOwnerRelationship"])
    if rel is None:
        return "-"
    title = text_at(rel, ["officerTitle"])
    is_director = text_at(rel, ["isDirector"]).lower() == "true"
    is_officer = text_at(rel, ["isOfficer"]).lower() == "true"
    is_ten = text_at(rel, ["isTenPercentOwner"]).lower() == "true"
    roles: List[str] = []
    if is_officer:
        roles.append(title or "Officer")
    if is_director:
        roles.append("Director")
    if is_ten:
        roles.append("10% Owner")
    return " / ".join(roles) if roles else title or "Insider"


def normalize_transaction_code(code: str, acquired_disposed: str) -> Tuple[str, str]:
    code = (code or "").upper().strip()
    ad = (acquired_disposed or "").upper().strip()
    if code == "P":
        return "Open-Market Purchase", "Direct Insider Capital"
    if code == "S":
        return "Insider Sale", "Potentially Routine Sale"
    if code == "M":
        return "Option Exercise", "Administrative / Compensation Event"
    if code in {"A", "G"}:
        return "Stock Award / Grant", "Administrative / Compensation Event"
    if code == "D" or ad == "D":
        return "Disposition", "Potentially Routine Sale"
    if ad == "A":
        return "Acquisition", "Direct Insider Capital"
    return f"Form 4 Transaction {code or '-'}", "SEC Insider Ownership"


def score_form4(role: str, code: str, value: Optional[float], owner_type: str, filed_date: str, event_date: str) -> Tuple[int, str, str, str, List[str], str]:
    role_l = role.lower()
    code = (code or "").upper()
    score = 30
    reasons: List[str] = ["Source-linked SEC Form 4 record"]

    if code == "P":
        score += 36
        reasons.append("Open-market insider purchase")
    elif code == "S":
        score += 0
        reasons.append("Insider sale disclosed; treated as context unless unusually strong")
    elif code == "M":
        score -= 12
        reasons.append("Option exercise; weaker capital signal")
    elif code in {"A", "G"}:
        score -= 16
        reasons.append("Compensation or grant-style transaction")
    else:
        reasons.append(f"Transaction code {code or '-'} requires review")

    if any(word in role_l for word in ["chief executive", "ceo", "president"]):
        score += 18
        reasons.append("Senior executive involved")
    elif any(word in role_l for word in ["chief financial", "cfo"]):
        score += 16
        reasons.append("Senior finance officer involved")
    elif "director" in role_l:
        score += 8
        reasons.append("Director involved")
    elif "10%" in role_l:
        score += 10
        reasons.append("Large holder involved")

    if value is not None:
        if value >= 1_000_000:
            score += 18
            reasons.append("Large disclosed transaction value")
        elif value >= 250_000:
            score += 12
            reasons.append("Meaningful disclosed transaction value")
        elif value >= 50_000:
            score += 6
            reasons.append("Non-trivial disclosed transaction value")
    else:
        score -= 5
        reasons.append("Transaction value could not be fully calculated")

    if owner_type.lower().startswith("direct"):
        score += 5
        reasons.append("Direct ownership reported")
    elif owner_type:
        score -= 3
        reasons.append("Indirect ownership reported")

    filing_dt = parse_date(filed_date)
    event_dt = parse_date(event_date)
    filing_age_days = (now_utc() - filing_dt).days if filing_dt else 999
    lag_days = (filing_dt - event_dt).days if filing_dt and event_dt else None

    if lag_days is not None and lag_days > 4:
        score -= min(12, lag_days)
        freshness = "Late-Filed"
        reasons.append(f"Reported {lag_days} days after transaction date")
    elif filing_age_days <= 7:
        freshness = "Recent"
        reasons.append("Filed within the last week")
    elif filing_age_days <= 30:
        freshness = "Fresh"
    else:
        freshness = "Stale"
        score -= 8

    # Tighten attention compression: sales and admin records should rarely become urgent.
    if code == "S":
        score = min(score, 72 if (value or 0) >= 1_000_000 else 64)
    elif code in {"M", "A", "G"}:
        score = min(score, 44)

    score = max(0, min(100, int(round(score))))

    if score >= 84 and code == "P":
        grade = "A"
    elif score >= 70:
        grade = "B"
    elif score >= 50:
        grade = "C"
    else:
        grade = "D"

    if code == "P" and score >= 84 and grade in {"A", "B"}:
        action = "Research Now"
    elif code == "P" and score >= 62:
        action = "Watch"
    elif code == "S":
        action = "Watch" if score >= 68 else "Context Only"
    elif code in {"M", "A", "G"}:
        action = "Context Only" if score >= 40 else "Low Signal"
    elif score >= 78 and grade in {"A", "B"}:
        action = "Watch"
    elif score >= 50:
        action = "Context Only"
    else:
        action = "Low Signal"

    if code == "P":
        caveat = "One insider purchase is not a complete investment thesis. Review fundamentals, valuation, and broader context."
    elif code == "S":
        caveat = "Insider sales can be planned, tax-related, or diversification-driven; treat as context rather than a standalone bearish signal."
    elif code in {"M", "A", "G"}:
        caveat = "Administrative, compensation, or option-related Form 4 activity is usually weaker evidence than open-market buying."
    else:
        caveat = "Transaction code requires review in the original filing before drawing conclusions."

    return score, grade, freshness, action, reasons, caveat


def parse_form4(company: Company, filing: Dict[str, Any]) -> List[Dict[str, Any]]:
    text, source_url = get_filing_document(company, filing)
    root = xml_from_text(text or "")
    if root is None:
        print(f"[WARN] Could not parse Form 4 XML for {company.ticker} {filing.get('accession')}", file=sys.stderr)
        return []

    issuer_name = text_at(root, ["issuer", "issuerName"], company.ticker)
    issuer_symbol = text_at(root, ["issuer", "issuerTradingSymbol"], company.ticker).upper() or company.ticker

    owners = all_desc(root, "reportingOwner")
    owner_name = "-"
    role = "Insider"
    if owners:
        owner_name = text_at(owners[0], ["reportingOwnerId", "rptOwnerName"], "-")
        role = relationship_role(owners[0])

    records: List[Dict[str, Any]] = []
    txs = all_desc(root, "nonDerivativeTransaction")
    for idx, tx in enumerate(txs, start=1):
        tx_date = text_at(tx, ["transactionDate", "value"], filing.get("report_date") or filing.get("filing_date") or "")
        code = text_at(tx, ["transactionCoding", "transactionCode"], "")
        shares = to_float(text_at(tx, ["transactionAmounts", "transactionShares", "value"], ""))
        price = to_float(text_at(tx, ["transactionAmounts", "transactionPricePerShare", "value"], ""))
        acquired_disposed = text_at(tx, ["transactionAmounts", "transactionAcquiredDisposedCode", "value"], "")
        owner_type = text_at(tx, ["ownershipNature", "directOrIndirectOwnership", "value"], "")
        shares_after = to_float(text_at(tx, ["postTransactionAmounts", "sharesOwnedFollowingTransaction", "value"], ""))
        value = shares * price if shares is not None and price is not None else None

        event_type, record_type = normalize_transaction_code(code, acquired_disposed)
        score, grade, freshness, action, reasons, caveat = score_form4(
            role=role,
            code=code,
            value=value,
            owner_type=owner_type,
            filed_date=filing.get("filing_date") or "",
            event_date=tx_date,
        )

        amount_reason = None
        if value is not None:
            amount_reason = f"Approx. disclosed transaction value: ${value:,.0f}"
        elif shares is not None:
            amount_reason = f"Shares disclosed: {shares:,.0f}; price unavailable"
        if amount_reason:
            reasons.append(amount_reason)

        accession = filing.get("accession") or ""
        record_id = f"sec-form4:{accession}:{idx}:{issuer_symbol}:{code}:{tx_date}:{owner_name}"
        records.append({
            "id": record_id,
            "record_id": record_id,
            "ticker": issuer_symbol,
            "company": issuer_name,
            "source_group": "SEC Insider Ownership",
            "source_type": "SEC Form 4" if filing.get("form") == "4" else "SEC Form 4/A",
            "source_form": filing.get("form", "4"),
            "record_type": record_type,
            "event_type": event_type,
            "entity_type": "insider",
            "filer": owner_name,
            "role": role,
            "owner_type": owner_type or "-",
            "filed_date": filing.get("filing_date") or "",
            "event_date": tx_date,
            "transaction_date": tx_date,
            "period_end": "",
            "accession_number": accession,
            "transaction_code": code,
            "shares": shares,
            "price": price,
            "transaction_value": value,
            "shares_after": shares_after,
            "score": score,
            "evidence_grade": grade,
            "freshness": freshness,
            "actionability": action,
            "watchlist_match": True,
            "rank_reasons": reasons,
            "does_not_prove": [
                "Future price movement",
                "Undisclosed knowledge",
                "An investment recommendation",
                "A complete view of the insider's personal financial situation"
            ],
            "caveat": caveat,
            "source_url": source_url,
            "vital_point": (f"{event_type}: {owner_name} ({role}) reported {shares:,.0f} shares at ${price:,.2f}, estimated value ${value:,.0f}." if value is not None and shares is not None and price is not None else f"{event_type}: {owner_name} ({role}) Form 4 record; key price/value fields require source review."),
        })
    return records


def next_hour_iso() -> str:
    nxt = now_utc().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return nxt.isoformat().replace("+00:00", "Z")


def write_outputs(records: List[Dict[str, Any]], companies: List[Company]) -> None:
    records = sorted(records, key=lambda r: (r.get("score", 0), r.get("filed_date", "")), reverse=True)[:MAX_OUTPUT_RECORDS]
    timestamp = iso_now()
    payload = {
        "metadata": {
            "product": "Capital Trace",
            "schema_version": "0.6",
            "data_mode": "sec-form-4-watchlist",
            "source_pipeline": "sec-edgar-form4-watchlist",
            "refresh_frequency": "hourly",
            "last_refreshed": timestamp,
            "last_data_update": timestamp,
            "last_sec_check": timestamp,
            "next_scheduled_check": next_hour_iso(),
            "source_groups": ["SEC Insider Ownership"],
            "coverage_lanes": ["Form 4 active", "13F ready", "13D/G ready", "Congress ready"],
            "watchlist_count": len(companies),
            "record_count": len(records),
            "methodology_version": "0.6-form4-attention-compression"
        },
        "records": records,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUTPUT_JS.write_text("window.CAPITAL_TRACE_PAYLOAD = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"[OK] wrote {OUTPUT_JSON.relative_to(ROOT)} with {len(records)} records")


def collect_form4_records(companies: List[Company], diagnostics: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    all_records: List[Dict[str, Any]] = []
    seen: set[str] = set()

    if diagnostics is not None:
        diagnostics["status"] = "running"
        diagnostics["companies_checked"] = 0
        diagnostics["forms_checked"] = ["4", "4/A"]
        diagnostics["lookback_days"] = LOOKBACK_DAYS
        diagnostics["filings_seen"] = 0
        diagnostics["filings_matched"] = 0
        diagnostics.setdefault("errors", [])

    for company in companies:
        print(f"[INFO] {company.ticker} CIK {company.cik10}")
        if diagnostics is not None:
            diagnostics["companies_checked"] += 1
        try:
            filings = recent_filings(company)
        except Exception as exc:
            if diagnostics is not None:
                diagnostics["errors"].append(f"{company.ticker}: recent Form 4 lookup failed: {type(exc).__name__}: {exc}")
            continue
        print(f"[INFO]   recent Form 4 filings: {len(filings)}")
        if diagnostics is not None:
            diagnostics["filings_seen"] += len(filings)
            diagnostics["filings_matched"] += len(filings)
        for filing in filings:
            try:
                parsed = parse_form4(company, filing)
            except Exception as exc:
                if diagnostics is not None:
                    diagnostics["errors"].append(f"{company.ticker} {filing.get('accession')}: Form 4 parse failed: {type(exc).__name__}: {exc}")
                continue
            for record in parsed:
                rid = record.get("record_id")
                if rid in seen:
                    continue
                seen.add(rid)
                # v0.8 normalized filing type field for frontend controls
                form = str(record.get("source_form") or filing.get("form") or "4").upper()
                record["filing_type"] = "Form 4/A" if form == "4/A" else "Form 4"
                all_records.append(record)
    if diagnostics is not None:
        diagnostics["records_added"] = len(all_records)
    return all_records


def main() -> int:
    companies = load_watchlist()
    print(f"[INFO] Checking {len(companies)} watchlist companies")
    all_records = collect_form4_records(companies)
    write_outputs(all_records, companies)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
