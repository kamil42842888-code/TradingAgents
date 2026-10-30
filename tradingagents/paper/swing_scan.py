"""Koral Equities Swing Alpha: broad, rotating US equity *research* queue.

Price screen -> verified-USD equity profile -> diverse watchlist CANDIDATES.
NEVER creates/changes a V1/V2/V3 paper portfolio, observer archive, intent,
AI model decision or broker order. All earnings/trade permissions remain
BLOCKED pending future dated Observer evidence and independent risk gates.

This is a forward-research universe selection tool, not a backtest, an issuer-
certified feed or evidence of profitable stock selection. Current Yahoo price
history and profiles are not historical point-in-time certified datasets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tradingagents.paper import discovery
from tradingagents.paper.autopilot import feeds
from tradingagents.paper.research_screener import ScreeningRules

SCHEMA = "koral-swing-research-queue-v1"
HOME = Path.home() / ".tradingagents" / "swing_research"
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,11}\Z")


def _finite_positive(value: object) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def rotating_symbols(universe: list[str], session: str, maximum: int) -> list[str]:
    """Deterministic coverage, not the same three names or a random reroll.

    The declared coverage is for a fixed listing universe: issuer additions,
    delistings and vendor failures can change the set actually covered.
    """
    if type(maximum) is not int or not 0 <= maximum <= 12000:
        raise ValueError("Invalid rotating universe limit")
    try:
        day = date.fromisoformat(session)
        if day.isoformat() != session:
            raise ValueError("Noncanonical date")
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid scan session") from exc
    if not isinstance(universe, list):
        raise ValueError("Missing universe")
    if any(type(t) is not str or not _SYMBOL.fullmatch(t) for t in universe):
        raise ValueError("Unverified listing symbol")
    names = sorted(set(universe), key=lambda t: (hashlib.sha256(t.encode()).digest(), t))
    if not names:
        raise ValueError("Empty listing universe")
    if maximum == 0 or maximum >= len(names):
        return names
    # Advance by one screening batch per NY calendar day. This avoids a
    # permanently excluded tail, without claiming full-universe daily scans.
    offset = (day.toordinal() * maximum) % len(names)
    return [names[(offset + i) % len(names)] for i in range(maximum)]


def _price_candidate(ticker: str, rows: list[tuple[str, float, float]],
                     session: str, rules: ScreeningRules) -> tuple[dict | None, str]:
    """Use the existing Swing Alpha 6-1 / 12-1 momentum and liquidity rules.

    Profile and earnings qualification are deliberately NOT implied here.
    """
    if not isinstance(rows, list) or len(rows) < 253:
        return None, "insufficient_completed_history"
    try:
        dates = [date.fromisoformat(record[0]) for record in rows]
        if (any(record[0] != day.isoformat() for record, day in zip(rows, dates))
                or dates != sorted(set(dates)) or dates[-1].isoformat() != session):
            return None, "missing_or_future_completed_session"
        if any(len(record) != 3 or not _finite_positive(record[1]) or
               not _finite_positive(record[2]) for record in rows):
            return None, "invalid_price_or_volume"
    except (IndexError, TypeError, ValueError, AttributeError):
        return None, "invalid_history_schema"
    prices = [r[1] for r in rows]
    volumes = [r[2] for r in rows]
    close = prices[-1]
    avg_dollar = sum(a*b for a, b in zip(prices[-20:], volumes[-20:])) / 20
    if close < rules.min_price_usd:
        return None, "price_below_floor"
    if avg_dollar < rules.min_avg_daily_dollar_volume_usd:
        return None, "liquidity_below_floor"
    ma200 = sum(prices[-200:])/200
    if close <= ma200:
        return None, "below_200_session_average"
    skipped = prices[-22]
    mom6 = skipped / prices[-127] - 1
    mom12 = skipped / prices[-253] - 1
    if mom6 <= 0 or mom12 <= 0:
        return None, "nonpositive_intermediate_momentum"
    score = rules.weight_six_minus_one*mom6 + rules.weight_twelve_minus_one*mom12
    return {
        "ticker": ticker, "asof": session, "score": round(score, 10),
        "momentum_6_1": round(mom6, 10),
        "momentum_12_1": round(mom12, 10),
        "price_usd": round(close, 4),
        "moving_average_200": round(ma200, 4),
        "avg_dollar_volume_usd": round(avg_dollar, 2),
    }, "accepted"


def _profile_eligible(raw: object, rules: ScreeningRules) -> tuple[dict | None, str]:
    if not isinstance(raw, dict) or raw.get("quoteType") != "EQUITY":
        return None, "not_verified_equity"
    if raw.get("currency") != "USD" or raw.get("financialCurrency") != "USD":
        return None, "unverified_usd_currency_alignment"
    cap = raw.get("marketCap")
    if not _finite_positive(cap) or cap < rules.min_market_cap_usd:
        return None, "unverified_market_cap_or_below_floor"
    sector = raw.get("sector")
    if not isinstance(sector, str) or not sector.strip() or len(sector) > 70:
        return None, "unverified_sector"
    return {"market_cap_usd": int(cap), "sector": sector.strip()}, "accepted"


def _next_weekday(session: str) -> str:
    """Earliest weekday research window, NOT an exchange-holiday certificate."""
    day = date.fromisoformat(session) + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def _earnings_preview(observation: object, session: str, requested_at: datetime,
                      checked_at: datetime) -> tuple[dict | None, str]:
    """Prior-session secondary-vendor *selection filter*, never trade evidence.

    The actual future session must independently collect immutable preopen and
    preclose snapshots. A current Yahoo/Nasdaq preview cannot authorise entries.
    """
    if (getattr(observation, "asof_date", None) != session or
            getattr(observation, "verified", None) is not True or
            getattr(observation, "source", None) != "YAHOO+NASDAQ_CONSENSUS_SECONDARY"):
        return None, "unverified_secondary_earnings_preview"
    try:
        day = date.fromisoformat(session)
        effective = date.fromisoformat(_next_weekday(session))
        last = date.fromisoformat(observation.last_earnings_date)
        nxt = date.fromisoformat(observation.next_earnings_date)
        observed = datetime.fromisoformat(observation.observed_at_utc)
        if (observed.tzinfo is None or observed.utcoffset() != timedelta(0) or
                observed.astimezone(timezone.utc) < requested_at.astimezone(timezone.utc) - timedelta(minutes=2) or
                observed.astimezone(timezone.utc) > checked_at.astimezone(timezone.utc) or
                last >= nxt or last > day or nxt <= day):
            return None, "invalid_earnings_preview_provenance"
        if (nxt - effective).days <= 7 or (effective - last).days <= 2:
            return None, "earnings_blackout_preview"
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None, "invalid_earnings_preview_provenance"
    return {
        "source": "YAHOO+NASDAQ_CONSENSUS_SECONDARY_PREVIEW_ONLY",
        "asof": session,
        "earliest_next_weekday": effective.isoformat(),
        "next_weekday_is_not_holiday_certified": True,
        "last_earnings_date": last.isoformat(),
        "next_earnings_date": nxt.isoformat(),
        "observed_at_utc": observed.isoformat(),
        "warning": "NOT future-session approval; reobserve earnings preopen and preclose",
    }, "accepted"


def build_queue(*, home: Path = HOME, now: datetime | None = None,
                max_symbols: int = 600, max_profiles: int = 40,
                shortlist_size: int = 8, universe_csv: Path | None = None,
                universe_source=None, price_source=None, profile_source=None,
                earnings_source=None, max_earnings_checks: int = 40,
                completion_clock=None) -> dict:
    """Build one immutable completed-NY-session candidate queue, no orders.

    Profile limits control vendor calls; candidate rankings are not earnings
    approvals. Newly shortlisted symbols must be observed in a future session,
    not inserted into an already captured daily snapshot or an existing ledger.
    """
    if (type(max_symbols) is not int or not 0 <= max_symbols <= 12000 or
            type(max_profiles) is not int or not 1 <= max_profiles <= 200 or
            type(shortlist_size) is not int or not 1 <= shortlist_size <= 8 or
            type(max_earnings_checks) is not int or not 1 <= max_earnings_checks <= 80):
        raise ValueError("Invalid bounded Swing Alpha scanning parameters")
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Scan timestamp must be timezone aware")
    session = feeds.session_cutoff(current, "close")  # only after 17:40 NY
    target = home.expanduser().resolve() / "runs" / session / "queue.json"
    if target.exists():
        raise ValueError("Swing queue already exists for this session; do not rewrite historical selection")
    if universe_csv is not None:
        names, notes = discovery.csv_universe(universe_csv)
    else:
        names, notes = (universe_source or discovery.source_universe)()
    chosen = rotating_symbols(names, session, max_symbols)
    end = (date.fromisoformat(session) + timedelta(days=1)).isoformat()
    price_fn = price_source or discovery.batch_prices
    prices = []
    exclude = Counter()
    returned = set()
    rules = ScreeningRules()
    for ticker, rows in price_fn(chosen, end):
        if ticker not in chosen or ticker in returned:
            raise ValueError("Price vendor returned ticker outside declared universe or twice")
        returned.add(ticker)
        candidate, reason = _price_candidate(ticker, rows, session, rules)
        if candidate is not None:
            prices.append(candidate)
        else:
            exclude[reason] += 1
    exclude["missing_price_response"] += len(chosen) - len(returned)
    ordered = sorted(prices, key=lambda x: (-x["score"], x["ticker"]))
    shortlist = []
    by_sector = Counter()
    if profile_source is None:
        from tradingagents.dataflows.vendor_gateway import get_yahoo_ticker
        profile_source = lambda ticker: get_yahoo_ticker(ticker.replace(".", "-")).info
    clock = completion_clock or (lambda: datetime.now(timezone.utc))
    profiles_checked = 0
    earnings_checked = 0
    earnings_ok = 0
    earnings_fn = earnings_source or feeds.earnings_for
    for row in ordered[:max_profiles]:
        profiles_checked += 1
        try:
            profile, reason = _profile_eligible(profile_source(row["ticker"]), rules)
        except Exception as exc:
            profile, reason = None, "profile_unavailable_" + type(exc).__name__
        if profile is None:
            exclude[reason] += 1
            continue
        if by_sector[profile["sector"]] >= 2:
            exclude["sector_diversification_cap"] += 1
            continue
        if earnings_checked >= max_earnings_checks:
            exclude["earnings_preview_budget_exhausted"] += 1
            break
        earnings_checked += 1
        try:
            snapshot = earnings_fn(row["ticker"], session, current)
            observed = clock()
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError("Preview collection completion clock must be timezone aware")
            preview, reason = _earnings_preview(snapshot, session, current, observed)
        except Exception as exc:
            preview, reason = None, "earnings_preview_source_failed_" + type(exc).__name__
        if preview is None:
            exclude[reason] += 1
            continue
        earnings_ok += 1
        if observed.tzinfo is None or observed.utcoffset() is None:
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("Profile collection completion clock must be timezone aware")
        if observed.astimezone(timezone.utc) < current.astimezone(timezone.utc):
            raise ValueError("Profile timestamp precedes scan start")
        shortlist.append({
            **row, **profile,
            "profile_observed_utc": observed.astimezone(timezone.utc).isoformat(),
            "earnings_verification": "NOT_CAPTURED_FOR_NEXT_SESSION",
            "earnings_preview": preview,
            "execution_authority": "NONE_RESEARCH_ONLY",
        })
        by_sector[profile["sector"]] += 1
        if len(shortlist) == shortlist_size:
            break
    finished = clock()
    if finished.tzinfo is None or finished.utcoffset() is None:
        raise ValueError("Scan completion clock must be timezone aware")
    try:
        finish_session = feeds.session_cutoff(finished, "close")
    except ValueError as exc:
        raise ValueError("Full scan did not complete in the same NY close window") from exc
    if (finished.astimezone(timezone.utc) < current.astimezone(timezone.utc) or
            finish_session != session):
        raise ValueError("Full scan did not complete in the same NY close window")
    result = {
        "schema": SCHEMA, "mode": "PRICE_AND_PROFILE_RESEARCH_ONLY",
        "source_kind": "US exchange listings + Yahoo adjusted history/profile snapshots",
        "source_limitations": (
            "Vendor listings and prices are not exchange-certified historical PIT data. "
            "Market caps and financial currency are vendor snapshots, not issuer filings. "
            "Prior-session secondary-calendar previews are not future-session earnings approval."
        ),
        "session": session,
        "scan_started_utc": current.astimezone(timezone.utc).isoformat(),
        "generated_utc": finished.astimezone(timezone.utc).isoformat(),
        "listing_universe_total": len(set(names)),
        "earliest_next_weekday": _next_weekday(session),
        "next_weekday_is_not_holiday_certified": True,
        "listing_sha256": hashlib.sha256("\n".join(sorted(set(names))).encode()).hexdigest(),
        "screened_requested": len(chosen),
        "price_screen_accepted": len(ordered),
        "price_vendor_responses": len(returned),
        "price_source_coverage_fraction": round(len(returned) / len(chosen), 5),
        "profiles_considered": profiles_checked,
        "earnings_preview_checks": earnings_checked,
        "earnings_preview_accepted": earnings_ok,
        "earnings_preview_policy": "TWO_SECONDARY_CONSENSUS_PREVIEW_NOT_NEXT_SESSION_AUTHORITY",
        "selection_rotates_by_session": bool(max_symbols and len(chosen) < len(set(names))),
        "complete_daily_universe_coverage": len(chosen) == len(set(names)),
        "screening_exclusions": dict(sorted(exclude.items())),
        "listing_notes": notes,
        "candidate_count": len(shortlist),
        "next_session_research_watchlist": [r["ticker"] for r in shortlist],
        "candidates": shortlist,
        "model_calls": 0, "portfolio_reads": 0, "portfolio_writes": 0,
        "paper_orders": 0, "broker_connected": False,
        "status": "RESEARCH_QUEUE_ONLY_NO_TRADE_AUTHORITY",
        "next_gate": (
            "Capture preopen, preclose and completed NY session evidence for EXACT "
            "selected tickers in a future observation date; apply earnings and "
            "portfolio gates independently before any AI-to-paper execution."
        ),
    }
    # A failed/no-candidate scan never masquerades as a successfully selected
    # universe; persist audit only if the selection was actually possible.
    if not ordered:
        raise ValueError("No eligible completed-session price series; source or coverage failure")
    if not shortlist:
        raise ValueError("No candidates passed the earnings-preview screen; fail closed")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    fd, temp_path = tempfile.mkstemp(prefix=".swing-scan-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(temp_path, target)
        except FileExistsError:
            raise ValueError("Concurrent scan wrote this immutable session") from None
    finally:
        Path(temp_path).unlink(missing_ok=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="koral-swing-universe", description="Broad US-equities price screen, zero orders/models"
    )
    parser.add_argument("--max-symbols", type=int, default=600,
                        help="Rotating daily listing sample; 0 scans all (slow)")
    parser.add_argument("--max-profiles", type=int, default=40)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--home", type=Path, default=HOME)
    parser.add_argument("--universe-csv", type=Path)
    parser.add_argument("--max-earnings-checks", type=int, default=40)
    args = parser.parse_args(argv)
    try:
        result = build_queue(home=args.home, max_symbols=args.max_symbols,
                             max_profiles=args.max_profiles, shortlist_size=args.top,
                             universe_csv=args.universe_csv,
                             max_earnings_checks=args.max_earnings_checks)
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print(f"SWING UNIVERSE BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": result["status"], "asof": result["session"],
        "total_listed": result["listing_universe_total"],
        "sample_screened": result["screened_requested"],
        "selected": result["next_session_research_watchlist"],
        "model_calls": 0, "paper_orders": 0,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
