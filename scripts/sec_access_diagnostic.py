#!/usr/bin/env python3
"""Capital Trace SEC access diagnostic.

Read-only: this never writes or commits Capital Trace data. It proves which SEC
endpoints work from the runner before live refresh is allowed.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
import sys

from refresh_sec_form4 import USER_AGENT, CONTACT_EMAIL, sec_get, get_sec_request_stats


def quarter(month: int) -> int:
    return ((month - 1) // 3) + 1


def test_url(label: str, url: str, *, as_json: bool = False, expect: str | None = None) -> dict:
    print(f"[DIAG] testing {label}: {url}")
    data = sec_get(url, as_json=as_json)
    if isinstance(data, dict):
        text_probe = json.dumps(data)[:1000]
        size = len(json.dumps(data))
    else:
        text_probe = str(data or "")[:1000]
        size = len(data or "") if data is not None else 0
    ok = data is not None and (expect is None or expect in text_probe)
    print(f"[DIAG] {label} ok={ok} bytes={size}")
    return {"label": label, "url": url, "ok": bool(ok), "bytes": size}


def main() -> int:
    now = datetime.now(timezone.utc)
    qtr = quarter(now.month)
    print("[INFO] Capital Trace SEC access diagnostic")
    print(f"[INFO] SEC User-Agent: {USER_AGENT}")
    print(f"[INFO] SEC From/contact: {CONTACT_EMAIL}")

    tests = []
    # Lightweight metadata endpoint. If this fails, SEC/GitHub access is likely blocked.
    tests.append(test_url(
        "company_tickers_json",
        "https://www.sec.gov/files/company_tickers.json",
        as_json=True,
    ))
    # Quarterly full-index is the preferred low-request discovery source.
    tests.append(test_url(
        "current_quarter_full_index",
        f"https://www.sec.gov/Archives/edgar/full-index/{now.year}/QTR{qtr}/master.idx",
        expect="CIK|Company Name|Form Type|Date Filed|Filename",
    ))
    # Previous quarter fallback in case current quarter index has not rolled yet.
    prev = now - timedelta(days=100)
    tests.append(test_url(
        "previous_quarter_full_index",
        f"https://www.sec.gov/Archives/edgar/full-index/{prev.year}/QTR{quarter(prev.month)}/master.idx",
        expect="CIK|Company Name|Form Type|Date Filed|Filename",
    ))
    # Single known issuer submissions endpoint.
    tests.append(test_url(
        "aapl_submissions_json",
        "https://data.sec.gov/submissions/CIK0000320193.json",
        as_json=True,
    ))

    stats = get_sec_request_stats()
    report = {
        "user_agent": USER_AGENT,
        "contact_email": CONTACT_EMAIL,
        "requests": stats,
        "results": tests,
        "sec_access_ok": any(t["ok"] for t in tests),
        "full_index_ok": any(t["ok"] and "full_index" in t["label"] for t in tests),
        "submissions_ok": any(t["ok"] and "submissions" in t["label"] for t in tests),
    }
    print("[DIAG_REPORT] " + json.dumps(report, sort_keys=True))

    if not report["sec_access_ok"]:
        print("[ERROR] SEC access diagnostic failed across all tested endpoints. Do not run live refresh from this environment.")
        return 1
    if not report["full_index_ok"] and not report["submissions_ok"]:
        print("[WARN] Some SEC access works, but neither full-index nor submissions worked. Live filing refresh should remain disabled.")
        return 2
    print("[OK] SEC access diagnostic found at least one usable filing-data route.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
