"""Operational progress survives normal preopen polls and repeated closes."""

from tests.test_daily_paper_integration import NEXT, account as account, at, sources
from tests.test_daily_supervisor_release import configure
from tradingagents.paper.autopilot import daily_supervisor as sup, daily_support as s


def test_first_processed_time_is_set_after_a_preopen_summary(account, tmp_path, monkeypatch):
    ledger, observer = account
    ops = configure(tmp_path, ledger)
    common = {"root": ops, "ledger": ledger, "observer": observer, "verify_sources": False}
    sup.tick(tmp_path, now=at(NEXT, 9), **common)
    path = ops / "summaries" / (NEXT + ".json")
    assert "first_processed_utc" not in s.read_json(path)

    sources(observer, NEXT)
    first_close = at(NEXT, 18)
    monkeypatch.setattr(s, "utcnow", lambda: first_close)
    sup.tick(tmp_path, now=first_close, **common)
    recorded = s.read_json(path)["first_processed_utc"]
    assert s.utc(recorded) == s.utc(first_close)

    later = at(NEXT, 18, 20)
    monkeypatch.setattr(s, "utcnow", lambda: later)
    sup.tick(tmp_path, now=later, **common)
    assert s.read_json(path)["first_processed_utc"] == recorded
