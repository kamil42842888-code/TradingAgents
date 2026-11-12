"""New scheduler/roster/budget unit tests; no live accounts, providers or brokers."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tradingagents.paper.autopilot import daily_roster as r, daily_support as s
from tradingagents.paper.autopilot.daily_research import BudgetStop, RequestBudget
from tradingagents.paper.autopilot.daily_supervisor import due_phase


def at(day, hour, minute=0):
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=s.NY)


def mock_state(watchlist=None, active=()):
    book = NS(positions={t: {} for t in active}, orders=[])
    return NS(
        watchlist=watchlist or ["TWST", "CRWD", "MRVL", "AVAH", "TTEK"],
        portfolio=NS(book=book, intents={}),
    )


def test_new_york_not_london_date_governs_session():
    stamp = datetime(2026, 11, 3, 0, 20, tzinfo=UTC)
    assert due_phase(stamp) == ("2026-11-02", "CLOSE_PROCESSING")


@pytest.mark.parametrize(
    "stamp,phase",
    [
        (datetime(2026, 10, 28, 21, 40, tzinfo=UTC), "CLOSE_PROCESSING"),
        (datetime(2026, 11, 2, 21, 40, tzinfo=UTC), "WAITING_FOR_OBSERVER"),
        (datetime(2026, 11, 2, 22, 40, tzinfo=UTC), "CLOSE_PROCESSING"),
        (datetime(2026, 11, 7, 23, 0, tzinfo=UTC), "NON_SESSION"),
    ],
)
def test_dst_and_weekend_phases(stamp, phase):
    assert due_phase(stamp)[1] == phase


def test_pinned_calendar_skips_weekend_and_scheduled_holiday():
    assert s.next_session("2026-10-30") == "2026-11-02"
    assert s.next_session("2026-11-25") == "2026-11-27"
    assert not s.is_session("2026-11-26")
    with pytest.raises(ValueError, match="coverage"):
        s.is_session("2028-01-03")


def test_roster_carries_held_names_without_padding_to_eight():
    assert r.choose_names(
        ["TWST", "CRWD", "MRVL", "AVAH", "TTEK"], {"TWST"}, ["AAPL", "MSFT", "NVDA"]
    ) == ["TWST", "AAPL", "MSFT", "NVDA"]


def test_roster_never_falls_back_to_pilot_trio(tmp_path):
    cfg = dict(s.DEFAULTS, managed_observer=True, effective_session="2026-11-02")
    s.atomic_json(tmp_path / "config.json", cfg)
    with pytest.raises(ValueError, match="MANAGED_ROSTER_MISSING"):
        r.observer_roster("2026-11-02", tmp_path)


def test_persistent_roster_is_frozen_and_tamper_checked(tmp_path):
    ledger = tmp_path / "account" / "portfolio.json"
    state = mock_state()
    first = r.freeze(
        ledger,
        "2026-11-02",
        at("2026-11-02", 8),
        root=tmp_path,
        queues=tmp_path / "absent",
        state=state,
    )
    second = r.freeze(
        ledger,
        "2026-11-02",
        at("2026-11-02", 8, 10),
        root=tmp_path,
        queues=tmp_path / "absent",
        state=mock_state(["AAPL"]),
    )
    assert first == second
    bad = dict(first, watchlist=["AAPL"])
    with pytest.raises(ValueError, match="integrity"):
        r.validate(bad, "2026-11-02")


def test_late_roster_declaration_refused(tmp_path):
    with pytest.raises(ValueError, match="ROSTER_WINDOW_MISSED"):
        r.freeze(
            tmp_path / "portfolio.json",
            "2026-11-02",
            at("2026-11-02", 9),
            root=tmp_path,
            state=mock_state(),
        )
    assert not (tmp_path / "rosters" / "2026-11-02.json").exists()


def test_cannot_drop_symbol_missing_from_current_account_watchlist():
    with pytest.raises(ValueError, match="missing from account"):
        r.choose_names(["CRWD"], {"TWST"}, ["AAPL"])


def test_budget_reservation_persists_before_request_and_limits_restarts(tmp_path):
    cfg = dict(s.DEFAULTS, max_model_requests_per_session=2)
    b = RequestBudget(tmp_path / "budget.json", cfg)
    b.reserve("a", 100)
    assert (
        json.loads((tmp_path / "budget.json").read_text())["requests"]["a"]["status"] == "STARTED"
    )
    restarted = RequestBudget(tmp_path / "budget.json", cfg)
    restarted.reserve("b", 100)
    with pytest.raises(BudgetStop):
        restarted.reserve("c", 100)
    assert len(restarted.data["requests"]) == 2


def test_missing_usage_retains_full_reservation(tmp_path):
    b = RequestBudget(tmp_path / "budget.json", s.DEFAULTS)
    b.reserve("a", 100)
    reserved = b.summary()["charged_token_units"]
    b.finish("a", None)
    assert b.summary()["charged_token_units"] == reserved
    assert not b.summary()["monetary_cap_enforced"]


def test_observed_usage_refund_does_not_refund_request_count(tmp_path):
    cfg = dict(s.DEFAULTS, max_model_requests_per_session=1)
    b = RequestBudget(tmp_path / "budget.json", cfg)
    b.reserve("a", 100)
    b.finish("a", 20)
    assert b.summary()["charged_token_units"] == 20
    with pytest.raises(BudgetStop):
        b.reserve("b", 1)


def test_budget_cannot_be_increased_after_first_request(tmp_path):
    b = RequestBudget(tmp_path / "budget.json", dict(s.DEFAULTS, max_model_requests_per_session=1))
    b.reserve("a", 1)
    with pytest.raises(BudgetStop, match="cannot change"):
        RequestBudget(tmp_path / "budget.json", s.DEFAULTS)


def test_duplicate_callback_same_request_does_not_double_reserve(tmp_path):
    b = RequestBudget(tmp_path / "budget.json", s.DEFAULTS)
    b.reserve("same", 100)
    b.reserve("same", 100)
    assert b.summary()["model_requests"] == 1


def test_oversized_prompt_is_blocked_before_reservation(tmp_path):
    b = RequestBudget(tmp_path / "budget.json", s.DEFAULTS)
    with pytest.raises(BudgetStop):
        b.reserve("a", 100001)
    assert b.summary()["model_requests"] == 0


def test_advisory_lock_excludes_second_instance_and_releases(tmp_path):
    path = tmp_path / ".lock"
    with s.os_lock(path), pytest.raises(RuntimeError, match="Another"), s.os_lock(path):
        pass
    with s.os_lock(path):
        pass


def test_unknown_legacy_lock_is_never_deleted(tmp_path):
    ledger = tmp_path / "portfolio.json"
    ledger.write_text("untouched")
    lock = ledger.with_suffix(".json.lock")
    lock.write_bytes(b"")
    with pytest.raises(RuntimeError, match="unknown"), s.ledger_lock(ledger):
        pass
    assert lock.exists() and ledger.read_text() == "untouched"


def test_demonstrably_dead_own_lease_is_quarantined(tmp_path, monkeypatch):
    ledger = tmp_path / "portfolio.json"
    ledger.write_text("untouched")
    lock = ledger.with_suffix(".json.lock")
    s.atomic_json(
        lock,
        {
            "schema": "koral-daily-ledger-lease-v1",
            "pid": 999999,
            "host": socket.gethostname(),
            "ledger": str(ledger.resolve()),
        },
    )
    monkeypatch.setattr(s, "process_alive", lambda pid: False)
    with s.ledger_lock(ledger):
        assert s.read_json(lock)["pid"] == os.getpid()
    assert not lock.exists()
    assert len(list((tmp_path / "_daily_lock_recovery").glob("*.json"))) == 1


def test_live_own_lease_is_not_removed(tmp_path, monkeypatch):
    ledger = tmp_path / "portfolio.json"
    ledger.write_text("untouched")
    lock = ledger.with_suffix(".json.lock")
    s.atomic_json(
        lock,
        {
            "schema": "koral-daily-ledger-lease-v1",
            "pid": 100,
            "host": socket.gethostname(),
            "ledger": str(ledger.resolve()),
        },
    )
    monkeypatch.setattr(s, "process_alive", lambda pid: True)
    with pytest.raises(RuntimeError, match="live"), s.ledger_lock(ledger):
        pass
    assert lock.exists()


def test_price_or_company_limits_cannot_be_unbounded():
    for changed in (
        {"max_companies_per_session": 4},
        {"max_model_requests_per_session": 0},
        {"armed": "true"},
    ):
        with pytest.raises(ValueError):
            s.validate_config(dict(s.DEFAULTS, **changed))


def test_real_child_crash_releases_os_lock_and_quarantines_own_lease(tmp_path):
    ledger = tmp_path / "portfolio.json"
    ledger.write_text("unchanged")
    script = (
        "from pathlib import Path\nimport os\n"
        "from tradingagents.paper.autopilot.daily_support import ledger_lock\n"
        f"with ledger_lock(Path({str(ledger)!r})):\n    os._exit(23)\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15
    )
    assert child.returncode == 23, child.stderr
    assert ledger.with_suffix(".json.lock").exists()
    with s.ledger_lock(ledger):
        pass
    assert not ledger.with_suffix(".json.lock").exists()
    assert ledger.read_text() == "unchanged"


def test_probe_cannot_call_models_or_create_account(tmp_path):
    from tradingagents.paper.autopilot.daily_supervisor import tick

    answer = tick(
        tmp_path,
        now=at("2026-11-02", 18),
        root=tmp_path / "ops",
        ledger=tmp_path / "paper" / "portfolio.json",
        probe=True,
        verify_sources=False,
    )
    assert answer["status"] == "SCHEDULED_PROBE_COMPLETED_NO_MODELS_NO_ACCOUNT_WRITES"
    assert not (tmp_path / "paper").exists()


def test_unarmed_tick_does_not_import_or_write_account(tmp_path):
    from tradingagents.paper.autopilot.daily_supervisor import tick

    answer = tick(
        tmp_path,
        now=at("2026-11-02", 18),
        root=tmp_path / "ops",
        ledger=tmp_path / "paper" / "portfolio.json",
        verify_sources=False,
    )
    assert answer["status"] == "SUPERVISOR_NOT_ARMED"
    assert not (tmp_path / "paper").exists()


def test_failed_scan_is_not_repeated_each_five_minutes(tmp_path, monkeypatch):
    from tradingagents.paper.autopilot.daily_supervisor import scan_for_next_session

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
    called = []

    def fail(*args, **kwargs):
        called.append(1)
        return NS(returncode=2)

    ops = tmp_path / "ops"
    one = scan_for_next_session(tmp_path, "2026-11-02", ops, runner=fail)
    two = scan_for_next_session(tmp_path, "2026-11-02", ops, runner=fail)
    assert called == [1] and one == two
    assert one["status"] == "SCAN_FAILED_CARRY_CURRENT_ROSTER"
