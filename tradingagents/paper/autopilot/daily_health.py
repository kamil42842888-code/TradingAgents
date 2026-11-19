"""Read-only operational evidence; never import or change the paper account.

Session completion here is a claim from saved summaries, not verification of
the ledger, original observations, scheduler, execution, or investment results.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from . import daily_support as s

_SUMMARY = re.compile(r"(\d{4}-\d{2}-\d{2})(_error)?\.json\Z")
_FAILED = "DAILY_CONTROLLER_FAILED_CLOSED"
_COMPLETE = "FORWARD_SESSION_PROCESSED"


def _read(path: Path):
    try:
        value = s.read_json(path)
        if not isinstance(value, dict):
            return None, "JSON_OBJECT_REQUIRED"
        return value, None
    except (OSError, ValueError, TypeError):
        return None, "MISSING_UNSAFE_OR_MALFORMED_JSON"


def _reference(event: dict | None):
    if event is None:
        return None
    # Do not duplicate arbitrary document content, account values or errors
    # that might contain private configuration in the new diagnostic fields.
    return {
        "ny_session": event["day"],
        "utc": event["at"].isoformat(),
        "status": event["document"]["status"],
        "file": event["file"],
    }


def _event_key(event: dict):
    # Equal timestamps cannot establish recovery; prefer the failure on a tie.
    return event["at"], event["document"]["status"] == _FAILED, event["file"]


def _complete(event: dict) -> bool:
    doc = event["document"]
    return (
        not event["error_file"]
        and doc["status"] == _COMPLETE
        and doc.get("data_complete") is True
        and doc.get("last_mark") == event["day"]
    )


def _summaries(root: Path, now: datetime):
    events, issues, seen = [], [], set()
    directory = root / "summaries"
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        return [], [{"file": "summaries", "reason": "DIRECTORY_UNREADABLE"}], set()
    for path in files:
        name = _SUMMARY.fullmatch(path.name)
        if not name:
            issues.append({"file": "summaries/" + path.name, "reason": "UNRECOGNISED_NAME"})
            continue
        day, error_file = name.groups()
        seen.add(day)
        doc, reason = _read(path)
        if reason is None:
            try:
                at = s.utc(doc.get("utc"))
                if (
                    date.fromisoformat(day).isoformat() != day
                    or doc.get("ny_session") != day
                    or at.astimezone(s.NY).date().isoformat() != day
                    or not isinstance(doc.get("status"), str)
                    or not doc["status"]
                    or (error_file and doc["status"] != _FAILED)
                    or (
                        doc["status"] == _COMPLETE
                        and (
                            not s.is_session(day)
                            or at.astimezone(s.NY).time() < s.session_windows(day)["close"][0]
                        )
                    )
                ):
                    reason = "INVALID_SESSION_RECORD"
                elif at > now:
                    reason = "FUTURE_TIMESTAMP"
            except (OSError, ValueError, TypeError, AttributeError):
                reason = "INVALID_SESSION_OR_TIMESTAMP"
        if reason is not None:
            issues.append({"file": "summaries/" + path.name, "reason": reason})
            continue
        events.append(
            {
                "day": day,
                "at": at,
                "document": doc,
                "error_file": bool(error_file),
                "file": "summaries/" + path.name,
            }
        )
    return sorted(events, key=_event_key), issues, seen


def _heartbeat(root: Path, now: datetime, stale_after_minutes: float):
    path = root / "heartbeat.json"
    health = {
        "status": "MISSING",
        "utc": None,
        "age_minutes": None,
        "stale_after_minutes": stale_after_minutes,
    }
    if not path.exists() and not path.is_symlink():
        return None, health
    doc, error = _read(path)
    if error:
        health["status"] = "INVALID"
        return None, health
    try:
        at = s.utc(doc.get("utc"))
        if not isinstance(doc.get("status"), str) or not doc["status"]:
            raise ValueError("Heartbeat status required")
        if doc.get("ny_session") != at.astimezone(s.NY).date().isoformat():
            raise ValueError("Heartbeat session must match its New York timestamp date")
        age = (now - at).total_seconds() / 60
        health.update(utc=at.isoformat(), age_minutes=round(age, 3))
        health["status"] = (
            "FUTURE" if age < 0 else "STALE" if age > stale_after_minutes else "RECENT"
        )
    except (ValueError, TypeError, AttributeError):
        health["status"] = "INVALID"
    return doc, health


def _coverage(cfg: dict | None, now: datetime, events: list[dict], seen: set[str]):
    result = {
        "basis": "LOCAL_SUMMARY_CLAIMS_ONLY",
        "status": "NOT_ASSESSED",
        "effective_session": None,
        "latest_due_session": None,
        "today_status": "NOT_ASSESSED",
        "reported_complete_sessions": [],
        "missing_sessions": [],
        "incomplete_sessions": [],
        "sessions_with_later_errors": [],
        "sessions": [],
    }
    if cfg is None or cfg["effective_session"] is None:
        result["reason"] = "VALID_DECLARED_EFFECTIVE_SESSION_REQUIRED"
        return result
    result["effective_session"] = effective = cfg["effective_session"]
    today = now.astimezone(s.NY).date().isoformat()
    try:
        is_today = s.is_session(today)
        processing = (
            datetime.combine(
                date.fromisoformat(today), s.session_windows(today)["close"][0], tzinfo=s.NY
            )
            if is_today
            else None
        )
        # Match the Observer's 40-minute allowance after the first possible
        # market capture. A current run in progress is not a missing session.
        today_due = processing is not None and now >= processing + timedelta(minutes=40)
        result["today_status"] = (
            "NON_SESSION"
            if not is_today
            else "DUE"
            if today_due
            else "PROCESSING_WINDOW"
            if now >= processing
            else "NOT_YET_DUE"
        )
        due = [
            day for day in s.calendar() if effective <= day <= today and (day < today or today_due)
        ]
    except (OSError, ValueError, TypeError, KeyError):
        result["reason"] = "CALENDAR_INVALID_OR_OUTSIDE_COVERAGE"
        return result
    result["latest_due_session"] = due[-1] if due else None
    by_day = {}
    for event in events:
        by_day.setdefault(event["day"], []).append(event)
    for day in due:
        day_events = by_day.get(day, [])
        completions = [event for event in day_events if _complete(event)]
        if completions:
            complete = max(completions, key=_event_key)
            later_error = any(
                event["document"]["status"] == _FAILED and event["at"] >= complete["at"]
                for event in day_events
            )
            result["reported_complete_sessions"].append(day)
            status = "REPORTED_COMPLETE_WITH_LATER_ERROR" if later_error else "REPORTED_COMPLETE"
            if later_error:
                result["sessions_with_later_errors"].append(day)
        elif not day_events and day not in seen:
            result["missing_sessions"].append(day)
            status = "MISSING"
        else:
            result["incomplete_sessions"].append(day)
            status = (
                "INVALID_EVIDENCE"
                if not day_events
                else "FAILED"
                if day_events[-1]["document"]["status"] == _FAILED
                else "INCOMPLETE"
            )
        result["sessions"].append({"ny_session": day, "status": status})
    result["status"] = (
        "NO_SESSIONS_DUE"
        if not due
        else "GAPS_OR_ERRORS"
        if any(
            result[key]
            for key in ("missing_sessions", "incomplete_sessions", "sessions_with_later_errors")
        )
        else "ALL_DUE_SESSIONS_REPORTED_COMPLETE"
    )
    return result


def report(root: Path, *, now: datetime | None = None, stale_after_minutes: float = 14.0) -> dict:
    """Inspect saved local evidence without locks, writes, providers or account imports."""
    if (
        isinstance(stale_after_minutes, bool)
        or not isinstance(stale_after_minutes, (int, float))
        or not math.isfinite(stale_after_minutes)
        or stale_after_minutes <= 0
    ):
        raise ValueError("Positive finite heartbeat threshold required")
    instant = s.utc(now or s.utcnow())
    config_file = root / "config.json"
    config_status, cfg = "VALID", None
    if not config_file.exists() and not config_file.is_symlink():
        config_status = "MISSING"
        # Preserve the old status field, not proof of an installed configuration.
        configuration = dict(s.DEFAULTS)
    else:
        try:
            document, reason = _read(config_file)
            if reason:
                raise ValueError("Invalid configuration file")
            cfg = configuration = s.validate_config(document)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            config_status, configuration = "INVALID", None
    heartbeat, heartbeat_health = _heartbeat(root, instant, stale_after_minutes)
    events, evidence_issues, seen = _summaries(root, instant)
    completed = [event for event in events if _complete(event)]
    errors = [event for event in events if event["document"]["status"] == _FAILED]
    coverage = _coverage(cfg, instant, events, seen)
    needs_attention = (
        config_status != "VALID"
        or heartbeat_health["status"] != "RECENT"
        or (heartbeat or {}).get("status") == _FAILED
        or bool(evidence_issues)
        or coverage["status"] in {"NOT_ASSESSED", "GAPS_OR_ERRORS"}
    )
    return {
        "schema": s.SCHEMA,
        "configuration": configuration,
        "heartbeat": heartbeat,
        "account_changes": 0,
        "broker_connected": False,
        "latest_session": events[-1]["document"] if events else None,
        "latest_successful_session": _reference(completed[-1] if completed else None),
        "latest_error": _reference(errors[-1] if errors else None),
        "operational_health": {
            "checked_utc": instant.isoformat(),
            "status": "ATTENTION_REQUIRED" if needs_attention else "LOCAL_EVIDENCE_CURRENT",
            "configuration_file": config_status,
            "heartbeat": heartbeat_health,
            "session_coverage": coverage,
            "evidence_issues": evidence_issues,
            "ledger_verification": "NOT_PERFORMED",
            "source_observation_verification": "NOT_PERFORMED",
            "scheduler_verification": "NOT_PERFORMED",
            "continuous_schedule_verification": "NOT_PERFORMED",
            "performance_verified": False,
        },
    }
