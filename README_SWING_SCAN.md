# Koral Equities — Broad Swing Alpha Research Queue

Status: BUILT AND RESEARCH-ONLY. No broker, AI model calls, paper account updates or scheduled observer changes.

The existing Compounder Discovery sleeve studies 3–10-year investment ideas. Its underwriting scores must never become short-term paper orders. This separate Swing Alpha queue is a deterministic selection mechanism for FUTURE observation and research.

## Coverage and cost policy

- Use official US exchange listing files via the existing discovery helper; exclude ETFs and other filtered issues. No invented fallback symbols.
- Default: a rotating 600-stock slice per completed NY session, not the same three stocks forever. The default does NOT screen the entire market daily. Request full-universe screening with --max-symbols 0 only if provider throughput permits.
- Apply the established Swing Alpha 6-minus-1 and 12-minus-1 momentum weights (60/40), 253 completed sessions, $10 minimum price, 200-session average trend, and $20 million daily dollar volume.
- Verify USD quote and USD financial currency, equity type, verified sector and at least $2 billion market capitalisation. Do not assume vendor profiles are issuer-certified or historically point-in-time.
- Fetch up to 40 candidate profiles, then prepare at most eight sector-diversified ticker names for NEXT-session observation.
- No earnings verification is invented. A name in this queue remains blocked from any AI-to-trade conversion until the proper future preopen, preclose and close observer snapshots pass.
- Write one immutable session-scoped queue file, including listing hash, source coverage, exclusions and zero portfolio/order/model calls.

## Offline commands after a source-only install

PowerShell, inside the Koral Equities root:

    .\.venv\Scripts\python.exe -m pytest -q tests/test_swing_scan.py
    .\.venv\Scripts\python.exe -m tradingagents.paper.swing_scan --max-symbols 200 --max-profiles 25 --top 8

The first real-provider check uses 200 subjects rather than 600. Normal production-sized daily screening is not approved until response coverage and time taken are measured. The output lives at:

    %USERPROFILE%\.tradingagents\swing_research\runs\YYYY-MM-DD\queue.json

## Separate release gates

1. Prove one actual brokerless market scan completes inside the true New York after-close window and reports honest vendor coverage. Synthetic unit tests alone are insufficient.
2. Predeclare a new shortlist for the next actual NY session by a separately verified Windows scheduler update. The presently running scheduler remains fixed on COST/JPM/WMT. No mid-day ticker changes or backdated observations.
3. The GitHub read-only AI audit now supports exactly matched evidence for up to eight stocks, but still limits paid model research to three companies. It requires a pinned Windows update before use.
4. Align the separate existing £10,000 V3.1 paper account safely, retaining all its history. Bridge source-hashed future observer snapshots to the portfolio policy gates and bar-marking flow; do not reset it.
5. Enable paper-only automation only after the exact future entry/exit, gap, earnings, costs, drawdown and no-retroactive-trade tests pass. No real brokerage connection.

No evidence, no advancement.
