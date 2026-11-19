"""Synthetic, offline workstation evidence: no account or provider operations."""

import hashlib
import importlib.util
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.audit.session_evidence import audit_evidence
from tools.audit.snapshot import Snapshot

DAY = "2026-11-13"
NOW = datetime(2026, 11, 14, 1, tzinfo=UTC)


class Reader:
    def exists(self, path):
        return path.exists()

    def read(self, path, *, limit):
        value = path.read_bytes()
        if len(value) > limit:
            raise ValueError("LIMIT_EXCEEDED")
        return value

    def json(self, path, *, limit):
        return json.loads(self.read(path, limit=limit))

    def glob(self, directory, pattern):
        return directory.glob(pattern)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_calendar(first="2026-11-12", count=6):
    days, sessions = {}, []
    begin = date.fromisoformat(first)
    for offset in range(count):
        day = begin + timedelta(days=offset)
        start = datetime(day.year, day.month, day.day, 5, tzinfo=UTC)
        entry = {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()}
        if day.weekday() < 5:
            sessions.append(day.isoformat())
            for key, hours, minutes in [
                ("preopen_start", 8, 45),
                ("preopen_end", 9, 25),
                ("preclose_start", 17, 0),
                ("preclose_end", 17, 25),
                ("processing_start", 17, 40),
                ("due", 18, 20),
            ]:
                entry[key] = (start + timedelta(hours=hours, minutes=minutes)).isoformat()
        days[day.isoformat()] = entry
    return {"schema": "koral-xnys-schedule-v1", "sessions": sessions, "utc_days": days}


def event(kind, details, utc="2026-11-13T22:46:00+00:00"):
    return {"kind": kind, "details": details, "utc": utc}


def save_ledger(path, document):
    previous = "0" * 64
    for index, item in enumerate(document["audit"], 1):
        item.update(seq=index, previous=previous)
        item.pop("hash", None)
        raw = json.dumps(
            item, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
        previous = item["hash"] = hashlib.sha256(raw).hexdigest()
    write(path, document)


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "data"
    ops = root / "paper_operations/daily_v1"
    account = root / "paper/v3_autopilot"
    observer = root / "observations/koral_v3"
    write(
        ops / "config.json",
        {
            "schema": "koral-daily-paper-v1",
            "effective_session": DAY,
            "armed": True,
            "managed_observer": True,
            "model_billing_mode": "chatgpt_plan_only",
            "chatgpt_account_sha256": "d" * 64,
            "chatgpt_credits_disabled_confirmed_at": "2026-11-12T12:00:00+00:00",
            "private_key": "PRIVATE_CREDENTIAL",
        },
    )
    write(
        ops / "heartbeat.json",
        {"ny_session": DAY, "utc": NOW.isoformat(), "status": "FORWARD_SESSION_PROCESSED"},
    )
    write(
        observer / "scheduler_heartbeat.json",
        {"session": DAY, "utc": NOW.isoformat(), "status": "ALREADY_CAPTURED"},
    )
    roster = {
        "schema": "koral-managed-daily-roster-v1",
        "session": DAY,
        "mode": "OBSERVE_ONLY",
        "shorts_allowed": False,
        "watchlist": ["KXDEMO"],
        "active_symbols": [],
        "declared_utc": "2026-11-12T23:00:00+00:00",
        "scan_proof": None,
    }
    raw = (json.dumps(roster, sort_keys=True, indent=2) + "\n").encode()
    roster["record_sha256"] = hashlib.sha256(raw).hexdigest()
    write(ops / "rosters" / (DAY + ".json"), roster)
    bar = {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}
    market = {
        "schema": "koral-observation-v1",
        "mode": "OBSERVE_ONLY",
        "session": DAY,
        "phase": "close",
        "orders": [],
        "source": "YAHOO_SECONDARY_NOT_EXCHANGE_CERTIFIED",
        "corporate_actions": "QUERIED_NO_UNMODELLED_EVENTS_REPORTED",
        "capture_started_utc": "2026-11-13T22:40:00+00:00",
        "capture_completed_utc": "2026-11-13T22:41:00+00:00",
        "ohlc_usd": {"KXDEMO": bar, "SPY": bar},
        "fx_gbp_per_usd_open": 0.8,
        "fx_gbp_per_usd_close": 0.8,
    }
    hashes = {"market_sha256": write(observer / "market" / (DAY + ".json"), market)}
    write(account / "observer_sources" / DAY / "market.json", market)
    for phase, instant in [
        ("preopen", "2026-11-13T14:00:00+00:00"),
        ("preclose", "2026-11-13T22:00:00+00:00"),
    ]:
        data = {
            "session": DAY,
            "phase": phase,
            "source_policy": "TWO_SECONDARY_FEEDS_MUST_AGREE_OR_BLOCK",
            "observed_utc": instant,
            "earnings": {
                "KXDEMO": {
                    "asof_date": DAY,
                    "next_earnings_date": "2027-02-01",
                    "last_earnings_date": "2026-10-01",
                    "source": "YAHOO+NASDAQ_CONSENSUS_SECONDARY",
                    "verified": True,
                    "observed_at_utc": instant,
                }
            },
        }
        hashes[phase + "_sha256"] = write(observer / "calendar" / DAY / (phase + ".json"), data)
        write(account / "observer_sources" / DAY / (phase + ".json"), data)
        write(account / "calendar" / DAY / (phase + ".json"), data)
    normalized = {
        "schema": "koral-autopilot-daily-input-v1",
        "session": DAY,
        "captured_at_utc": "2026-11-13T22:45:00+00:00",
        "bars": {"KXDEMO": bar},
        "gbp_per_usd_open": 0.8,
        "gbp_per_usd_close": 0.8,
        "spy_price_only_close_usd": 101.0,
    }
    hashes["paper_market_sha256"] = write(account / "market_evidence" / (DAY + ".json"), normalized)
    state = {
        "schema": "koral-autopaper-v3.1-isolated",
        "paper_only": True,
        "ai_receipts": {},
        "portfolio": {
            "book": {
                "last_session": DAY,
                "cash_gbp": "PRIVATE_BALANCE",
                "positions": {"PRIVATE_HOLDING": {}},
            }
        },
        "audit": [
            event("V3_CREATED", {"date": "2026-11-12"}, "2026-11-12T12:00:00+00:00"),
            event(
                "OBSERVER_SOURCE_BOUND",
                {
                    "date": DAY,
                    **hashes,
                    "source_roster": ["KXDEMO"],
                    "origin": "IMMUTABLE_OBSERVER_SOURCE_NOT_EXCHANGE_CERTIFIED",
                },
            ),
            event(
                "SESSION_MARKED",
                {
                    "date": DAY,
                    "market_input_sha256": hashes["paper_market_sha256"],
                    "backfill": False,
                },
                "2026-11-13T22:46:01+00:00",
            ),
            event(
                "OBSERVER_FORWARD_SESSION_PROCESSED",
                {
                    "date": DAY,
                    "original_source_sha256": hashes["market_sha256"],
                    "broker_connected": False,
                },
                "2026-11-13T22:46:02+00:00",
            ),
        ],
    }
    save_ledger(account / "portfolio.json", state)
    write(
        ops / "summaries" / (DAY + ".json"),
        {
            "ny_session": DAY,
            "utc": "2026-11-13T22:50:00+00:00",
            "status": "FORWARD_SESSION_PROCESSED",
            "data_complete": True,
            "last_mark": DAY,
            "equity_gbp": "PRIVATE_EQUITY",
        },
    )
    return root, ops, account, observer, state, hashes


def inspect(installation, *, now=NOW, calendar=None, reader=None):
    return audit_evidence(
        installation[0], now=now, reader=reader or Reader(), calendar=calendar or make_calendar()
    )


def add_research(installation, *, incomplete=False, failed=False):
    _, _, account, _, state, hashes = installation
    state["audit"].extend(
        [
            event(
                "DAILY_RESEARCH_SLATE",
                {
                    "date": DAY,
                    "candidates": ["KXDEMO"],
                    "daily_company_limit": 3,
                    "market_sha256": hashes["market_sha256"],
                },
                "2026-11-13T22:50:00+00:00",
            ),
            event(
                "AI_RESEARCH_STARTED",
                {"date": DAY, "ticker": "KXDEMO", "source_sha256": hashes["market_sha256"]},
                "2026-11-13T22:51:00+00:00",
            ),
        ]
    )
    if incomplete:
        state["ai_receipts"][DAY + ":KXDEMO"] = {
            "status": "IN_PROGRESS",
            "session": DAY,
            "ticker": "KXDEMO",
        }
    else:
        receipt = {"session": DAY, "decision": "NO_TRADE"}
        if failed:
            receipt.update(research_complete=False, error_detail="PRIVATE_PROVIDER_ERROR")
        else:
            report_path = account / "reports" / DAY / "KXDEMO/source.json"
            source_hash = write(
                report_path,
                {
                    "ticker": "KXDEMO",
                    "session": DAY,
                    "generated_utc": "2026-11-13T22:52:00+00:00",
                    "portfolio_manager": "PRIVATE_PROMPT",
                },
            )
            receipt.update(
                source_file=f"reports/{DAY}/KXDEMO/source.json", source_sha256=source_hash
            )
        state["audit"].append(
            event(
                "AI_RESEARCH_RECEIPT",
                {
                    "date": DAY,
                    "ticker": "KXDEMO",
                    "source_sha256": hashes["market_sha256"],
                    "receipt": receipt,
                    "request_budget": {"model_requests": 1},
                },
                "2026-11-13T22:53:00+00:00",
            )
        )
        state["ai_receipts"][DAY + ":KXDEMO"] = {
            **receipt,
            "status": "COMPLETE",
            "ticker": "KXDEMO",
        }
    write(
        account / "model_budgets" / (DAY + ".json"),
        {
            "schema": "koral-request-budget-v1",
            "policy": {
                "max_model_requests_per_session": 60,
                "max_reserved_tokens_per_session": 250000,
                "max_output_tokens_per_request": 2048,
                "max_prompt_bytes_per_request": 100000,
            },
            "requests": {
                "PRIVATE_REQUEST_ID": {
                    "charged_units": 100,
                    "status": "STARTED" if incomplete else "COMPLETE_WITH_USAGE",
                }
            },
        },
    )
    save_ledger(account / "portfolio.json", state)


def test_consistent_mark_does_not_claim_performance_or_unattended_run(installation):
    result = inspect(installation)
    assert result["status"] == "LOCAL_EVIDENCE_CONSISTENT"
    assert result["sessions"][0]["sources"]["files_checked"] == 9
    assert result["sessions"][0]["research"]["status"] == "NO_ATTEMPTS_RECORDED"
    assert result["accounting_or_performance_verified"] is False
    assert result["unattended_execution_verified"] is False
    assert result["independent_authenticity_verified"] is False


@pytest.mark.parametrize("role", ["original", "archive", "calendar", "normalized"])
def test_hash_change_is_not_hidden_by_success_summary(installation, role):
    _, _, account, observer, _, _ = installation
    paths = {
        "original": observer / "market" / (DAY + ".json"),
        "archive": account / "observer_sources" / DAY / "market.json",
        "calendar": account / "calendar" / DAY / "preopen.json",
        "normalized": account / "market_evidence" / (DAY + ".json"),
    }
    paths[role].write_bytes(paths[role].read_bytes() + b" ")
    row = inspect(installation)["sessions"][0]
    assert row["summary"]["status"] == "REPORTED_COMPLETE"
    assert row["status"] == "ATTENTION_REQUIRED"
    assert "HASH_MISMATCH" in row["sources"]["status"]


def test_ledger_tampering_blocks_use_of_untrusted_audit_events(installation):
    _, _, account, _, state, _ = installation
    state["audit"][1]["details"]["market_sha256"] = "0" * 64
    write(account / "portfolio.json", state)
    result = inspect(installation)
    assert result["ledger"]["status"] == "INVALID_LEDGER_OR_AUDIT_CHAIN"
    assert result["sessions"][0]["mark_events_status"] == "UNVERIFIED_LEDGER"


def test_duplicate_session_mark_rejected_even_with_valid_chain(installation):
    _, _, account, _, state, _ = installation
    state["audit"].append(deepcopy(state["audit"][2]))
    save_ledger(account / "portfolio.json", state)
    assert (
        inspect(installation)["sessions"][0]["mark_events_status"] == "MISSING_OR_DUPLICATE_EVENTS"
    )


def test_earlier_processing_timestamp_is_not_a_valid_mark(installation):
    _, _, account, _, state, _ = installation
    for entry in state["audit"][1:]:
        entry["utc"] = "2026-11-13T14:00:00+00:00"
    save_ledger(account / "portfolio.json", state)
    assert inspect(installation)["sessions"][0]["mark_events_status"] == "INVALID_EVENT_LINKS"


def test_missing_summary_is_reported_separately_from_valid_mark(installation):
    (installation[1] / "summaries" / (DAY + ".json")).unlink()
    row = inspect(installation)["sessions"][0]
    assert row["summary"]["status"] == "MISSING"
    assert row["mark_events_status"] == "LOCAL_EVENT_LINKS_CONSISTENT"
    assert row["status"] == "ATTENTION_REQUIRED"


def test_bad_roster_seal_blocks_source_alignment(installation):
    path = installation[1] / "rosters" / (DAY + ".json")
    data = json.loads(path.read_bytes())
    data["declared_utc"] = "2026-11-13T15:00:00+00:00"
    write(path, data)
    row = inspect(installation)["sessions"][0]
    assert row["roster_status"] == "INVALID"
    assert row["mark_events_status"] == "INVALID_EVENT_LINKS"


def test_receipts_and_original_report_bytes_reconcile(installation):
    add_research(installation)
    result = inspect(installation)
    assert result["status"] == "LOCAL_EVIDENCE_CONSISTENT"
    assert result["sessions"][0]["research"]["reports_verified"] == 1
    serialized = json.dumps(result)
    assert "PRIVATE_" not in serialized
    assert "KXDEMO" not in serialized
    assert str(installation[0]) not in serialized


@pytest.mark.parametrize("fault", ["report", "receipt", "budget"])
def test_research_corruption_is_visible(installation, fault):
    add_research(installation)
    _, _, account, _, state, _ = installation
    if fault == "report":
        (account / "reports" / DAY / "KXDEMO/source.json").write_text("{}")
    elif fault == "budget":
        (account / "model_budgets" / (DAY + ".json")).unlink()
    else:
        state["ai_receipts"][DAY + ":KXDEMO"]["decision"] = "CHANGED"
        save_ledger(account / "portfolio.json", state)
    assert (
        inspect(installation)["sessions"][0]["research"]["status"]
        == "INVALID_OR_INCOMPLETE_RESEARCH_EVIDENCE"
    )


@pytest.mark.parametrize(
    ("incomplete", "failed", "expected"),
    [(True, False, "INCOMPLETE_OR_USAGE_UNRESOLVED"), (False, True, "FAILED_ATTEMPTS_RECORDED")],
)
def test_research_failures_and_interruptions_are_not_successful_research(
    installation, incomplete, failed, expected
):
    add_research(installation, incomplete=incomplete, failed=failed)
    result = inspect(installation)
    assert result["sessions"][0]["research"]["status"] == expected
    assert result["status"] == "ATTENTION_REQUIRED"


def test_pending_transaction_is_detected_without_recovery(installation):
    _, _, account, _, _, _ = installation
    journal = {
        "schema": "koral-forward-transaction-v1",
        "session": DAY,
        "before_sha256": "a" * 64,
        "after_sha256": "b" * 64,
        "files": {},
    }
    journal["record_sha256"] = hashlib.sha256(
        (json.dumps(journal, sort_keys=True, indent=2) + "\n").encode()
    ).hexdigest()
    path = account / "forward_transactions" / DAY / "transaction.json"
    write(path, journal)
    result = inspect(installation)
    assert result["transactions"]["pending"] == 1
    assert result["status"] == "ATTENTION_REQUIRED"
    assert not (path.parent / "complete.json").exists()


def test_missing_configuration_does_not_invent_expected_dates(installation):
    (installation[1] / "config.json").unlink()
    result = inspect(installation)
    assert result["configuration_status"] == "MISSING"
    assert result["sessions"] == []
    assert result["status"] == "ATTENTION_REQUIRED"


def test_history_bound_is_explicit(installation):
    calendar = make_calendar(count=80)
    result = inspect(installation, calendar=calendar, now=datetime(2027, 1, 28, 1, tzinfo=UTC))
    assert len(result["sessions"]) == 31
    assert result["unreviewed_due_sessions"] > 0
    assert result["status"] == "ATTENTION_REQUIRED"


def test_current_processing_window_is_not_a_missing_session(installation):
    result = inspect(installation, now=datetime(2026, 11, 13, 23, tzinfo=UTC))
    assert result["due_session_count"] == 0
    assert result["sessions"] == []


def test_invalid_calendar_and_future_clock_are_explicit(installation):
    result = inspect(installation, now=datetime(2028, 1, 1, tzinfo=UTC))
    assert result["calendar_status"] == "INVALID_OR_OUTSIDE_COVERAGE"
    assert result["status"] == "ATTENTION_REQUIRED"


def test_read_failures_do_not_export_error_text(installation):
    class BrokenReader(Reader):
        def read(self, path, *, limit):
            raise ValueError("PRIVATE_PROVIDER_ERROR should never be serialized")

    result = inspect(installation, reader=BrokenReader())
    assert result["status"] == "ATTENTION_REQUIRED"
    assert "PRIVATE_" not in json.dumps(result)


def test_audit_changes_no_input_file(installation):
    root = installation[0]
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    inspect(installation)
    assert before == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_shared_snapshot_handles_the_complete_audit(installation):
    snapshot = Snapshot({"data": installation[0]})
    result = inspect(installation, reader=snapshot)
    assert result["status"] == "LOCAL_EVIDENCE_CONSISTENT"
    assert snapshot.verify()["status"] == "STABLE"


def test_shared_snapshot_rejects_duplicate_json_keys(installation):
    path = installation[1] / "config.json"
    raw = path.read_bytes().decode()
    path.write_text(raw.replace('"armed": true', '"armed": false, "armed": true'))
    result = inspect(installation, reader=Snapshot({"data": installation[0]}))
    assert result["configuration_status"] == "INVALID_OR_UNREADABLE"
    assert result["status"] == "ATTENTION_REQUIRED"


def test_last_mark_field_must_match_audit(installation):
    _, _, account, _, state, _ = installation
    state["portfolio"]["book"]["last_session"] = "2026-11-16"
    write(account / "portfolio.json", state)
    assert inspect(installation)["ledger"]["status"] == "INVALID_LEDGER_OR_AUDIT_CHAIN"


def test_request_budget_policy_is_required(installation):
    add_research(installation)
    path = installation[2] / "model_budgets" / (DAY + ".json")
    doc = json.loads(path.read_bytes())
    del doc["policy"]
    write(path, doc)
    assert (
        inspect(installation)["sessions"][0]["research"]["status"]
        == "INVALID_OR_INCOMPLETE_RESEARCH_EVIDENCE"
    )


def test_conditional_receipt_requires_matching_acceptance(installation):
    add_research(installation)
    _, _, account, _, state, _ = installation
    receipt = state["audit"][-1]["details"]["receipt"]
    receipt.update(decision="CONDITIONAL_LONG", intent_id="PRIVATE_INTENT")
    state["ai_receipts"][DAY + ":KXDEMO"] = {**receipt, "status": "COMPLETE", "ticker": "KXDEMO"}
    save_ledger(account / "portfolio.json", state)
    assert (
        inspect(installation)["sessions"][0]["research"]["status"]
        == "INVALID_OR_INCOMPLETE_RESEARCH_EVIDENCE"
    )


@pytest.mark.parametrize("fault", ["missing", "stale", "future", "wrong_session"])
def test_controller_heartbeat_diagnostics_are_sanitized(installation, fault):
    path = installation[1] / "heartbeat.json"
    if fault == "missing":
        path.unlink()
    else:
        document = json.loads(path.read_bytes())
        if fault == "stale":
            document["utc"] = (NOW - timedelta(minutes=15)).isoformat()
        elif fault == "future":
            document["utc"] = (NOW + timedelta(minutes=1)).isoformat()
        else:
            document["ny_session"] = "2026-11-12"
        write(path, document)
    result = inspect(installation)
    expected = {
        "missing": "MISSING",
        "stale": "STALE",
        "future": "FUTURE_TIMESTAMP",
        "wrong_session": "INVALID",
    }
    assert result["heartbeats"]["controller"]["status"] == expected[fault]
    assert result["status"] == "ATTENTION_REQUIRED"
    assert result["session_evidence_status"] == "LOCAL_EVIDENCE_CONSISTENT"


def test_disabled_or_api_routing_is_visible_without_changing_history(installation):
    path = installation[1] / "config.json"
    config = json.loads(path.read_bytes())
    config.update(armed=False, model_billing_mode="provider_api")
    write(path, config)
    result = inspect(installation)
    assert result["current_controls"]["current_armed"] is False
    assert result["current_controls"]["model_billing_mode"] == "provider_api"
    assert result["status"] == "ATTENTION_REQUIRED"
    assert result["session_evidence_status"] == "LOCAL_EVIDENCE_CONSISTENT"


def test_malformed_billing_mode_is_reported_without_private_text(installation):
    path = installation[1] / "config.json"
    config = json.loads(path.read_bytes())
    config["model_billing_mode"] = {"PRIVATE_CREDENTIAL": []}
    write(path, config)
    result = inspect(installation)
    assert result["current_controls"]["model_billing_mode"] == "UNRECOGNIZED"
    assert result["status"] == "ATTENTION_REQUIRED"
    assert "PRIVATE_" not in json.dumps(result)


def add_historical_references(installation):
    """Add a pre-arming baseline and accepted report with no original Observer copies."""
    _, _, account, _, state, hashes = installation
    old_day = "2026-11-12"
    old_hashes = {}
    files = {
        "market_sha256": ("observer_sources", "market.json"),
        "preopen_sha256": ("observer_sources", "preopen.json"),
        "preclose_sha256": ("observer_sources", "preclose.json"),
    }
    for key, (directory, filename) in files.items():
        source = account / directory / DAY / filename
        doc = json.loads(source.read_bytes().decode().replace(DAY, old_day))
        old_hashes[key] = write(account / directory / old_day / filename, doc)
        if filename != "market.json":
            write(account / "calendar" / old_day / filename, doc)
    source = account / "market_evidence" / (DAY + ".json")
    doc = json.loads(source.read_bytes().decode().replace(DAY, old_day))
    old_hashes["paper_market_sha256"] = write(
        account / "market_evidence" / (old_day + ".json"), doc
    )
    report_hash = write(
        account / "reports" / old_day / "KXDEMO/source.json",
        {"ticker": "KXDEMO", "session": old_day, "private_report": "PRIVATE_PROMPT"},
    )
    utc = "2026-11-12T22:46:00+00:00"
    state["audit"][1:1] = [
        event("OBSERVER_SOURCE_BOUND", {"date": old_day, **old_hashes}, utc),
        event(
            "SESSION_MARKED",
            {"date": old_day, "market_input_sha256": old_hashes["paper_market_sha256"]},
            utc,
        ),
        event(
            "V3_POLICY_ACCEPTED",
            {"date": old_day, "ticker": "KXDEMO", "report_sha256": report_hash},
            utc,
        ),
    ]
    save_ledger(account / "portfolio.json", state)
    return old_day


def test_all_historical_runtime_references_are_hashed_without_old_originals(installation):
    old_day = add_historical_references(installation)
    result = inspect(installation)
    history = result["historical_bound_evidence"]
    assert history["status"] == "LOCAL_BOUND_REFERENCES_CONSISTENT"
    assert history["first_session"] == old_day
    assert history["archive_files_checked"] == 12
    assert history["accepted_report_files_checked"] == 1
    assert result["status"] == "LOCAL_EVIDENCE_CONSISTENT"
    assert not (installation[3] / "market" / (old_day + ".json")).exists()
    assert "KXDEMO" not in json.dumps(result)


@pytest.mark.parametrize(
    ("relative", "role"),
    [
        ("market_evidence/2026-11-12.json", "normalized_market"),
        ("observer_sources/2026-11-12/market.json", "market_archive"),
        ("calendar/2026-11-12/preopen.json", "preopen_paper_calendar"),
        ("reports/2026-11-12/KXDEMO/source.json", "accepted_model_report"),
    ],
)
def test_missing_pre_effective_runtime_evidence_prevents_all_clear(installation, relative, role):
    add_historical_references(installation)
    (installation[2] / relative).unlink()
    result = inspect(installation)
    assert result["sessions"][0]["status"] == "LOCAL_EVIDENCE_CONSISTENT"
    assert result["historical_bound_evidence"]["status"] == "REFERENCED_FILE_MISSING"
    assert result["historical_bound_evidence"]["failed_reference_role"] == role
    assert result["status"] == "ATTENTION_REQUIRED"


def test_duplicate_historical_mark_prevents_all_clear(installation):
    add_historical_references(installation)
    state = installation[4]
    state["audit"].append(deepcopy(state["audit"][2]))
    save_ledger(installation[2] / "portfolio.json", state)
    result = inspect(installation)
    assert (
        result["historical_bound_evidence"]["status"] == "INVALID_OR_DUPLICATE_REFERENCE_METADATA"
    )
    assert result["status"] == "ATTENTION_REQUIRED"


def test_historical_reference_bound_is_explicit(installation, monkeypatch):
    monkeypatch.setattr("tools.audit.session_evidence.MAX_HISTORICAL_FILES", 1)
    result = inspect(installation)
    assert result["historical_bound_evidence"]["status"] == "REFERENCE_LIMIT_EXCEEDED_NOT_VERIFIED"
    assert result["status"] == "ATTENTION_REQUIRED"


def test_real_v3_producers_are_compatible_with_read_only_auditor(tmp_path, monkeypatch):
    from tests.test_daily_paper_integration import NAMES, sources
    from tradingagents.paper.autopilot import (
        daily_roster,
        daily_supervisor,
        daily_support as support,
        paper_bridge,
        state,
    )

    clock = datetime(2026, 11, 12, 23, 15, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz) if tz is not None else clock.replace(tzinfo=None)

    monkeypatch.setattr(state, "datetime", FrozenDateTime)
    monkeypatch.setattr(support, "utcnow", lambda: clock)
    root = tmp_path / "real_producer_data"
    account = root / "paper/v3_autopilot"
    observer = root / "observations/koral_v3"
    ops = root / "paper_operations/daily_v1"
    ledger = account / "portfolio.json"
    state.create_new(ledger, "2026-10-22", ["AAPL", "MSFT", "NVDA"])
    sources(observer, "2026-11-12")
    paper_bridge.bootstrap(ledger, observer, "2026-11-12", NAMES, clock)
    document = state.read(ledger)
    document.set_running(True)
    state.atomic_write(ledger, document)
    config = dict(
        support.DEFAULTS,
        armed=True,
        managed_observer=True,
        effective_session=DAY,
        nightly_scan=False,
        rotate_from_latest_queue=False,
        max_companies_per_session=0,
        model_billing_mode="chatgpt_plan_only",
        chatgpt_account_sha256="d" * 64,
        chatgpt_credits_disabled_confirmed_at=clock.isoformat(),
    )
    support.atomic_json(ops / "config.json", config)
    clock = datetime(2026, 11, 13, 13, tzinfo=UTC)
    daily_roster.freeze(ledger, DAY, clock, root=ops)
    sources(observer, DAY)
    clock = datetime(2026, 11, 13, 23, 30, tzinfo=UTC)
    produced = daily_supervisor.tick(
        tmp_path, root=ops, ledger=ledger, observer=observer, now=clock, verify_sources=False
    )
    assert produced["status"] == "FORWARD_SESSION_PROCESSED"
    # Real scheduler serialization, with a fake subprocess returning an already
    # captured synthetic source. No network/model request or OS scheduler call.
    spec = importlib.util.spec_from_file_location(
        "audit_test_observer", Path(__file__).parents[1] / "KE_Observer_Scheduler_2026-10-26.py"
    )
    scheduler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scheduler)
    monkeypatch.setattr(scheduler, "datetime", FrozenDateTime)
    monkeypatch.setattr(scheduler, "watchlist_for_session", lambda session: tuple(NAMES))
    capture = {
        "status": "ALREADY_CAPTURED",
        "session": DAY,
        "phase": "close",
        "mode": "OBSERVE_ONLY",
        "orders": 0,
        "sha256": digest(observer / "market" / (DAY + ".json")),
    }
    scheduler.run_tick(
        now=clock,
        project=tmp_path,
        home=observer,
        require_preflight=False,
        runner=lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(capture), stderr=""
        ),
    )
    reader = Snapshot({"data": root})
    result = audit_evidence(root, now=clock, reader=reader, calendar=make_calendar())
    assert result["status"] == "LOCAL_EVIDENCE_CONSISTENT"
    assert result["sessions"][0]["mark_events_status"] == "LOCAL_EVENT_LINKS_CONSISTENT"
    assert result["transactions"]["pending"] == 0
    assert result["heartbeats"]["observer"]["status"] == "RECENT"
    assert reader.verify()["status"] == "STABLE"
