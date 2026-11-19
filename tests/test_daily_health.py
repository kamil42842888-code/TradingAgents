"""Saved status evidence must be ordered correctly and remain read-only."""

import builtins
from datetime import datetime, timedelta

import pytest

from tradingagents.paper.autopilot import (
    daily_health as health,
    daily_supervisor as supervisor,
    daily_support as s,
)


def at(day="2026-11-13", hour=19, minute=0):
    return datetime.fromisoformat(f"{day}T{hour:02}:{minute:02}:00").replace(tzinfo=s.NY)


def configure(root, day="2026-11-13"):
    s.atomic_json(root / "config.json", dict(s.DEFAULTS, armed=True, effective_session=day))


def heartbeat(root, now):
    s.atomic_json(
        root / "heartbeat.json",
        {
            "utc": now.isoformat(),
            "ny_session": now.date().isoformat(),
            "status": "WAITING_FOR_REQUIRED_OBSERVATIONS",
        },
    )


def summary(root, now, *, error=False, complete=True, **extra):
    day = now.astimezone(s.NY).date().isoformat()
    document = {
        "ny_session": day,
        "utc": now.isoformat(),
        "status": "DAILY_CONTROLLER_FAILED_CLOSED" if error else "FORWARD_SESSION_PROCESSED",
        "last_mark": day,
        "data_complete": complete,
        **extra,
    }
    path = root / "summaries" / (day + ("_error" if error else "") + ".json")
    s.atomic_json(path, document)
    return path


def coverage(report):
    return report["operational_health"]["session_coverage"]


@pytest.mark.parametrize("error_later", [False, True])
def test_status_orders_actual_times_and_retains_success_and_error(tmp_path, error_later):
    configure(tmp_path)
    heartbeat(tmp_path, at())
    summary(tmp_path, at(hour=18, minute=30))
    summary(tmp_path, at(hour=18, minute=35 if error_later else 25), error=True)
    result = supervisor.status(tmp_path, root=tmp_path, now=at())
    expected = "DAILY_CONTROLLER_FAILED_CLOSED" if error_later else "FORWARD_SESSION_PROCESSED"
    assert result["latest_session"]["status"] == expected
    assert result["latest_successful_session"]["status"] == "FORWARD_SESSION_PROCESSED"
    assert result["latest_error"]["status"] == "DAILY_CONTROLLER_FAILED_CLOSED"
    assert coverage(result)["reported_complete_sessions"] == ["2026-11-13"]
    assert coverage(result)["sessions_with_later_errors"] == (["2026-11-13"] if error_later else [])


def test_tied_timestamps_do_not_claim_recovery(tmp_path):
    configure(tmp_path)
    summary(tmp_path, at())
    summary(tmp_path, at(), error=True)
    result = health.report(tmp_path, now=at())
    assert result["latest_session"]["status"] == "DAILY_CONTROLLER_FAILED_CLOSED"
    assert coverage(result)["sessions_with_later_errors"] == ["2026-11-13"]


@pytest.mark.parametrize(
    ("hour", "minute", "today_status", "missing"),
    [
        (17, 39, "NOT_YET_DUE", []),
        (17, 40, "PROCESSING_WINDOW", []),
        (18, 19, "PROCESSING_WINDOW", []),
        (18, 20, "DUE", ["2026-11-13"]),
    ],
)
def test_current_day_is_not_missing_before_processing_deadline(
    tmp_path, hour, minute, today_status, missing
):
    configure(tmp_path)
    result = health.report(tmp_path, now=at(hour=hour, minute=minute))
    assert coverage(result)["today_status"] == today_status
    assert coverage(result)["missing_sessions"] == missing


def test_fresh_heartbeat_does_not_hide_prior_session_gap(tmp_path):
    configure(tmp_path)
    heartbeat(tmp_path, at("2026-11-17", 10))
    summary(tmp_path, at("2026-11-16"))
    result = health.report(tmp_path, now=at("2026-11-17", 10))
    assert result["operational_health"]["heartbeat"]["status"] == "RECENT"
    assert coverage(result)["missing_sessions"] == ["2026-11-13"]
    assert coverage(result)["reported_complete_sessions"] == ["2026-11-16"]
    assert coverage(result)["today_status"] == "NOT_YET_DUE"


@pytest.mark.parametrize(
    ("now", "today_status", "missing"),
    [
        (at("2026-11-14"), "NON_SESSION", ["2026-11-13"]),
        (at("2026-11-26"), "NON_SESSION", ["2026-11-25"]),
        (at("2026-11-27", 14, 40), "PROCESSING_WINDOW", ["2026-11-25"]),
        (at("2026-11-27", 15, 20), "DUE", ["2026-11-25", "2026-11-27"]),
    ],
)
def test_weekends_holidays_and_early_close_use_pinned_calendar(
    tmp_path, now, today_status, missing
):
    configure(tmp_path, "2026-11-13" if now.day == 14 else "2026-11-25")
    result = health.report(tmp_path, now=now)
    assert coverage(result)["today_status"] == today_status
    assert coverage(result)["missing_sessions"] == missing


@pytest.mark.parametrize(
    ("minutes_old", "status"),
    [(None, "MISSING"), (0, "RECENT"), (14, "RECENT"), (15, "STALE"), (-1, "FUTURE")],
)
def test_heartbeat_freshness_and_absence(tmp_path, minutes_old, status):
    if minutes_old is not None:
        heartbeat(tmp_path, at() - timedelta(minutes=minutes_old))
    result = health.report(tmp_path, now=at())
    assert result["operational_health"]["heartbeat"]["status"] == status
    assert result["operational_health"]["configuration_file"] == "MISSING"
    assert coverage(result)["status"] == "NOT_ASSESSED"


def test_explicit_staleness_threshold_and_invalid_threshold(tmp_path):
    heartbeat(tmp_path, at() - timedelta(minutes=20))
    assert (
        health.report(tmp_path, now=at(), stale_after_minutes=30)["operational_health"][
            "heartbeat"
        ]["status"]
        == "RECENT"
    )
    for threshold in [0, -1, True, float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="threshold"):
            health.report(tmp_path, now=at(), stale_after_minutes=threshold)


@pytest.mark.parametrize("raw", ["{broken", "[]", '{"utc":"2026-11-13T18:30:00"}'])
def test_invalid_configuration_heartbeat_and_summary_are_findings(tmp_path, raw):
    (tmp_path / "summaries").mkdir()
    for path in [
        tmp_path / "config.json",
        tmp_path / "heartbeat.json",
        tmp_path / "summaries/2026-11-13.json",
    ]:
        path.write_text(raw)
    result = health.report(tmp_path, now=at())
    assert result["configuration"] is None
    assert result["latest_session"] is None
    assert result["operational_health"]["configuration_file"] == "INVALID"
    assert result["operational_health"]["heartbeat"]["status"] == "INVALID"
    assert result["operational_health"]["evidence_issues"]


@pytest.mark.parametrize(
    "changes",
    [
        {"utc": "2026-11-13T23:30:00"},
        {"utc": "2026-11-14T03:00:00Z"},
        {"ny_session": "2026-11-12"},
        {"utc": "nonsense"},
    ],
)
def test_invalid_or_future_summary_cannot_be_selected_as_success(tmp_path, changes):
    configure(tmp_path)
    summary(tmp_path, at(), **changes)
    result = health.report(tmp_path, now=at())
    assert result["latest_session"] is None
    assert result["latest_successful_session"] is None
    assert coverage(result)["incomplete_sessions"] == ["2026-11-13"]
    assert coverage(result)["sessions"][0]["status"] == "INVALID_EVIDENCE"


@pytest.mark.parametrize("changes", [{"data_complete": False}, {"last_mark": "2026-11-12"}])
def test_success_label_without_completed_mark_is_incomplete(tmp_path, changes):
    configure(tmp_path)
    summary(tmp_path, at(), **changes)
    result = health.report(tmp_path, now=at())
    assert result["latest_successful_session"] is None
    assert coverage(result)["incomplete_sessions"] == ["2026-11-13"]


def test_status_is_read_only_and_does_not_import_account_or_provider(tmp_path, monkeypatch):
    configure(tmp_path)
    heartbeat(tmp_path, at())
    summary(tmp_path, at(), private_setting="must not enter diagnostic metadata")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    original_import = builtins.__import__

    def no_runtime_import(name, *args, **kwargs):
        assert name.rsplit(".", 1)[-1] not in {
            "state",
            "paper_forward",
            "daily_research",
            "observe",
        }
        return original_import(name, *args, **kwargs)

    def no_write(*args, **kwargs):
        pytest.fail("status attempted a filesystem mutation")

    monkeypatch.setattr(builtins, "__import__", no_runtime_import)
    monkeypatch.setattr(s, "atomic_json", no_write)
    monkeypatch.setattr(s, "atomic_bytes", no_write)
    monkeypatch.setattr(s, "os_lock", no_write)
    result = health.report(tmp_path, now=at())
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert "private_setting" not in result["latest_successful_session"]
    assert result["operational_health"]["ledger_verification"] == "NOT_PERFORMED"
    assert result["operational_health"]["scheduler_verification"] == "NOT_PERFORMED"
    assert result["operational_health"]["performance_verified"] is False


def test_calendar_coverage_failure_is_not_a_healthy_empty_history(tmp_path):
    configure(tmp_path)
    result = health.report(tmp_path, now=at("2028-01-03"))
    assert coverage(result)["status"] == "NOT_ASSESSED"
    assert coverage(result)["reason"] == "CALENDAR_INVALID_OR_OUTSIDE_COVERAGE"


@pytest.mark.parametrize(
    ("day", "hour", "minute"),
    [("2026-11-13", 9, 0), ("2026-11-13", 17, 39), ("2026-11-27", 14, 39)],
)
def test_reported_complete_before_collection_window_is_invalid(tmp_path, day, hour, minute):
    configure(tmp_path, day)
    summary(tmp_path, at(day, hour, minute))
    result = health.report(tmp_path, now=at(day))
    assert result["latest_successful_session"] is None
    assert result["latest_session"] is None
    assert coverage(result)["incomplete_sessions"] == [day]
    assert coverage(result)["sessions"][0]["status"] == "INVALID_EVIDENCE"


@pytest.mark.parametrize("session", [None, "2026-11-12"])
def test_heartbeat_missing_or_mismatched_session_is_invalid(tmp_path, session):
    heartbeat(tmp_path, at())
    document = s.read_json(tmp_path / "heartbeat.json")
    if session is None:
        del document["ny_session"]
    else:
        document["ny_session"] = session
    s.atomic_json(tmp_path / "heartbeat.json", document)
    result = health.report(tmp_path, now=at())
    assert result["operational_health"]["heartbeat"]["status"] == "INVALID"
