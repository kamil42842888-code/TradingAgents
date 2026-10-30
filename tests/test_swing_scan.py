"""Source-only Swing Alpha selection. No network, model, paper or broker calls."""
from __future__ import annotations

import json
from datetime import datetime, date, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradingagents.paper import swing_scan

ASOF = "2026-10-28"
NY = ZoneInfo("America/New_York")


def clock(hour=18, minute=10):
    return datetime(2026, 10, 28, hour, minute, tzinfo=NY)


def rows_for(ticker: str, gain: float = .55):
    last = date.fromisoformat(ASOF)
    day = last
    days = []
    while len(days) < 260:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    return [(d.isoformat(), float(30*(1+gain*i/259)), 2_000_000.)
            for i, d in enumerate(days)]


def market_fixture(names, *, gains=None, missing=None):
    gains = gains or {}
    missing = set(missing or [])
    def prices(symbols, end):
        assert end == "2026-10-29"
        for ticker in symbols:
            if ticker not in missing:
                yield ticker, rows_for(ticker, gains.get(ticker, .55))
    return prices


def profile(ticker):
    sectors = {"AAA": "Technology", "BBB": "Financial Services", "CCC": "Healthcare",
               "DDD": "Energy", "EEE": "Consumer Defensive", "FFF": "Utilities"}
    return {"quoteType": "EQUITY", "currency": "USD", "financialCurrency": "USD",
            "marketCap": 10_000_000_000, "sector": sectors.get(ticker,"Industrials")}


def earnings(ticker, session, now):
    return SimpleNamespace(asof_date=session, verified=True,
         source="YAHOO+NASDAQ_CONSENSUS_SECONDARY",
         last_earnings_date="2026-09-15", next_earnings_date="2026-12-15",
         observed_at_utc=now.astimezone(timezone.utc).isoformat())


def params(tmp_path, names=None, price_source=None, profile_source=profile, now=None):
    names = names or ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    return dict(home=tmp_path/"swing", now=now or clock(),
                max_symbols=0, max_profiles=20, shortlist_size=5,
                universe_source=lambda:(names,["MOCK listing universe"]),
                price_source=price_source or market_fixture(names),
                profile_source=profile_source,
                earnings_source=earnings,
                completion_clock=lambda: clock(18,11))


def test_rotation_covers_listed_names_across_successive_sessions_without_stale_three():
    universe=["S"+str(i) for i in range(13)]
    dates=["2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31"]
    daily=[swing_scan.rotating_symbols(universe,day,4) for day in dates]
    assert all(len(batch)==4 for batch in daily)
    assert len(set(tuple(batch) for batch in daily)) > 1
    assert set().union(*(set(batch) for batch in daily)) == set(universe)
    assert swing_scan.rotating_symbols(universe,ASOF,0) == swing_scan.rotating_symbols(universe,ASOF,20)


def test_full_screen_returns_diversified_future_watchlist_without_model_or_orders(tmp_path):
    names=["AAA","BBB","CCC","DDD","EEE","FFF"]
    data=swing_scan.build_queue(**params(tmp_path, names,
                          price_source=market_fixture(names, gains={"AAA": .9,"BBB": .8,"CCC": .7,"DDD": .6,"EEE": .55,"FFF": .5})))
    assert data["listing_universe_total"] == 6
    assert data["screened_requested"] == 6
    assert len(data["next_session_research_watchlist"])==5
    assert data["next_session_research_watchlist"][0] == "AAA"
    assert data["model_calls"] == data["paper_orders"] == data["portfolio_reads"] == data["portfolio_writes"] == 0
    assert data["status"] == "RESEARCH_QUEUE_ONLY_NO_TRADE_AUTHORITY"
    assert all(item["execution_authority"]=="NONE_RESEARCH_ONLY" for item in data["candidates"])
    assert all(item["earnings_verification"]=="NOT_CAPTURED_FOR_NEXT_SESSION" for item in data["candidates"])
    saved=tmp_path/"swing"/"runs"/ASOF/"queue.json"
    assert json.loads(saved.read_text())["next_session_research_watchlist"] == data["next_session_research_watchlist"]
    assert not (tmp_path/".tradingagents"/"paper").exists()


def test_rejected_missing_and_future_prices_do_not_qualify(tmp_path):
    bad=rows_for("AAA")+[('2026-10-29',50.,2_000_000.)]
    provider=lambda syms,end: ((t,bad if t=="AAA" else rows_for(t)) for t in syms if t!="CCC")
    out=swing_scan.build_queue(**params(tmp_path, price_source=provider))
    assert "AAA" not in out["next_session_research_watchlist"]
    assert "CCC" not in out["next_session_research_watchlist"]
    assert out["screening_exclusions"]["missing_or_future_completed_session"]==1
    assert out["screening_exclusions"]["missing_price_response"]==1


def test_unverified_financial_currency_or_cap_never_reaches_candidate_list(tmp_path):
    def profiles(ticker):
        p=profile(ticker)
        if ticker=="AAA":p["financialCurrency"]="ZAR"
        if ticker=="BBB":p["marketCap"]=1_000_000
        return p
    data=swing_scan.build_queue(**params(tmp_path, profile_source=profiles))
    assert not {"AAA","BBB"} & set(data["next_session_research_watchlist"])
    assert data["screening_exclusions"]["unverified_usd_currency_alignment"]==1
    assert data["screening_exclusions"]["unverified_market_cap_or_below_floor"]==1


def test_sector_concentration_and_model_budget_are_not_a_trade_signal(tmp_path):
    data=swing_scan.build_queue(**params(tmp_path,profile_source=lambda t:dict(profile(t), sector="Technology")))
    assert data["next_session_research_watchlist"] and len(data["next_session_research_watchlist"])==2
    assert data["screening_exclusions"]["sector_diversification_cap"]>=1


def test_repeat_session_refused_before_any_vendor_call(tmp_path):
    options=params(tmp_path)
    swing_scan.build_queue(**options)
    def forbidden(*_):raise AssertionError("Duplicate scan must not fetch vendors")
    options["price_source"]=forbidden
    with pytest.raises(ValueError,match="already exists"):
        swing_scan.build_queue(**options)


def test_earlier_session_or_slow_cross_midnight_never_fabricates_completed_scan(tmp_path):
    opts=params(tmp_path,now=clock(16,25))
    with pytest.raises(ValueError,match="outside"):
        swing_scan.build_queue(**opts)
    assert not list((tmp_path/"swing").rglob("*.json"))
    opts=params(tmp_path)
    opts["completion_clock"]=lambda:datetime(2026,10,29,0,1,tzinfo=NY)
    with pytest.raises(ValueError,match="same NY close window"):
        swing_scan.build_queue(**opts)
    assert not list((tmp_path/"swing").rglob("*.json"))


def test_no_valid_price_data_fails_without_manufacturing_candidates(tmp_path):
    opts=params(tmp_path,price_source=lambda syms,end:iter([]))
    with pytest.raises(ValueError,match="No eligible completed-session price"):
        swing_scan.build_queue(**opts)


def test_vendor_duplicate_or_unlisted_ticker_halts_without_archiving(tmp_path):
    def vendor(symbols,end):
        yield "AAA",rows_for("AAA")
        yield "AAA",rows_for("AAA")
    with pytest.raises(ValueError,match="outside declared universe or twice"):
        swing_scan.build_queue(**params(tmp_path,price_source=vendor))
    assert not list((tmp_path/"swing").rglob("*.json"))


def test_next_week_earnings_never_enter_future_roster(tmp_path):
    names=["AAA", "BBB", "CCC", "DDD"]
    def source(ticker,session,now):
        event=earnings(ticker,session,now)
        if ticker=="AAA":event.next_earnings_date="2026-10-30"
        return event
    p=params(tmp_path,names)
    p["earnings_source"]=source
    result=swing_scan.build_queue(**p)
    assert "AAA" not in result["next_session_research_watchlist"]
    assert result["screening_exclusions"]["earnings_blackout_preview"]==1
    assert result["paper_orders"]==0


def test_missing_secondary_confirmation_fails_closed_without_fabrication(tmp_path):
    names=["AAA", "BBB", "CCC", "DDD"]
    def source(ticker,session,now):
        event=earnings(ticker,session,now)
        if ticker in ("AAA","BBB"):
            event.verified=False
        return event
    p=params(tmp_path,names)
    p["earnings_source"]=source
    result=swing_scan.build_queue(**p)
    assert not set(["AAA", "BBB"]) & set(result["next_session_research_watchlist"])
    assert result["earnings_preview_checks"]==4
    assert result["earnings_preview_accepted"]==2
    assert result["screening_exclusions"]["unverified_secondary_earnings_preview"]==2
    assert all(r["earnings_verification"]=="NOT_CAPTURED_FOR_NEXT_SESSION" for r in result["candidates"])


def test_no_valid_earnings_previews_rejects_new_queue(tmp_path):
    p=params(tmp_path)
    p["earnings_source"]=lambda t,d,n:None
    with pytest.raises(ValueError,match="No candidates passed the earnings-preview"):
        swing_scan.build_queue(**p)
    assert not list((tmp_path/"swing").rglob("*.json"))


def test_preview_does_not_upgrade_to_future_earnings_approval(tmp_path):
    result=swing_scan.build_queue(**params(tmp_path))
    assert all(r["earnings_verification"]=="NOT_CAPTURED_FOR_NEXT_SESSION" for r in result["candidates"])
    assert all(r["earnings_preview"]["source"].endswith("PREVIEW_ONLY") for r in result["candidates"])
    assert result["model_calls"] == result["paper_orders"] == 0


def test_earnings_preview_uses_next_business_weekday_not_friday_close():
    now=datetime(2026,10,30,18,10,tzinfo=NY)
    event=earnings("AAA","2026-10-30",now)
    event.next_earnings_date="2026-11-08"
    preview,reason=swing_scan._earnings_preview(event,"2026-10-30",now,
                                                 now+timedelta(minutes=1))
    assert preview is None and reason=="earnings_blackout_preview"
    assert swing_scan._next_weekday("2026-10-30")=="2026-11-02"


def test_future_dated_earnings_preview_is_rejected():
    now=clock()
    event=earnings("AAA",ASOF,now)
    event.observed_at_utc=(now+timedelta(hours=2)).astimezone(timezone.utc).isoformat()
    preview,reason=swing_scan._earnings_preview(event,ASOF,now,
                                                 now+timedelta(minutes=1))
    assert preview is None and reason=="invalid_earnings_preview_provenance"
