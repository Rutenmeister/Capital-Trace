# Capital Trace v0.12k

Refresh-proof repair build.

This version adds a low-request SEC daily-index discovery scan so the system can prove a fresh scan happened without replacing the live dataset with an empty result. It also keeps the tiered scanner/rate-limit guard from v0.12j.

Key behavior:

- Fast Core workflow runs hourly against the core watchlist.
- Broad S&P 500 workflow runs daily using a rotating S&P 500 issuer window.
- Broad mode uses SEC daily master index discovery instead of hammering every issuer/form endpoint.
- Previous records are merged/preserved instead of overwritten by a zero-record refresh.
- Trace Brief now reports whether records were fresh, merged, or preserved.
- Refresh proof includes SEC request count, 403 count, and circuit-breaker state.
- 13F values continue to be normalized with implied-price sanity checks.

Upload code files only. Do not manually upload data/ unless intentionally restoring a known-good data file.
