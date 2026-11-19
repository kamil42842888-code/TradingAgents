"""Bounded local-evidence reconciliation, using only the Python standard library.

This module never imports the installed application, changes its files, calls a
provider, or reconstructs trades. Its hashes establish consistency among local
files, not independent authenticity or correct investment performance.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from itertools import islice
from pathlib import Path

SMALL_LIMIT = 5_000_000
LEDGER_LIMIT = 50_000_000
MAX_SESSIONS = 31
MAX_EVENTS = 100_000
MAX_TRANSACTIONS = 1_024
MAX_HISTORICAL_SESSIONS = 732
MAX_HISTORICAL_FILES = 4_096
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,11}\Z")
_COMPLETE = "FORWARD_SESSION_PROCESSED"
_FAILED = "DAILY_CONTROLLER_FAILED_CLOSED"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _day(value) -> bool:
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except (ValueError, TypeError):
        return False


def _stamp(value) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("INVALID_TIMESTAMP")
    return result.astimezone(UTC)


def _digest(value) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _symbols(value) -> bool:
    return (
        isinstance(value, list)
        and 1 <= len(value) <= 8
        and all(isinstance(item, str) and _SYMBOL.fullmatch(item) for item in value)
        and len(set(value)) == len(value)
    )


def _canonical(value, *, chain=False) -> bytes:
    options = {"sort_keys": True, "allow_nan": False}
    if chain:
        options.update(separators=(",", ":"), ensure_ascii=True)
    else:
        options["indent"] = 2
    return (json.dumps(value, **options) + ("" if chain else "\n")).encode("utf-8")


def _read(reader, path: Path, *, limit=SMALL_LIMIT):
    try:
        if not reader.exists(path):
            return None, None, "MISSING"
        raw = reader.read(path, limit=limit)
        document = reader.json(path, limit=limit)
        if not isinstance(document, dict):
            return None, None, "INVALID"
        return raw, document, "READ"
    except (ValueError, OSError, TypeError):
        return None, None, "INVALID_OR_UNREADABLE"


def _calendar(calendar: dict, now: datetime):
    sessions = calendar.get("sessions")
    days = calendar.get("utc_days")
    if (
        calendar.get("schema") != "koral-xnys-schedule-v1"
        or not isinstance(sessions, list)
        or not 1 <= len(sessions) <= 1_000
        or any(not _day(day) for day in sessions)
        or sessions != sorted(set(sessions))
        or not isinstance(days, dict)
        or not 1 <= len(days) <= 732
    ):
        raise ValueError("INVALID_CALENDAR")
    parsed = {}
    today = None
    for day, values in days.items():
        if not _day(day) or not isinstance(values, dict):
            raise ValueError("INVALID_CALENDAR")
        times = {key: _stamp(value) for key, value in values.items()}
        if times["start"] >= times["end"]:
            raise ValueError("INVALID_CALENDAR")
        if day in sessions:
            ordered = [
                times[key]
                for key in (
                    "start",
                    "preopen_start",
                    "preopen_end",
                    "preclose_start",
                    "preclose_end",
                    "processing_start",
                    "due",
                    "end",
                )
            ]
            if any(left >= right for left, right in zip(ordered, ordered[1:], strict=False)):
                raise ValueError("INVALID_CALENDAR")
        if times["start"] <= now < times["end"]:
            if today is not None:
                raise ValueError("OVERLAPPING_CALENDAR")
            today = day
        parsed[day] = times
    if any(day not in parsed for day in sessions) or today is None:
        raise ValueError("OUTSIDE_CALENDAR")
    return sessions, parsed, today


def _ledger(reader, root: Path, now: datetime):
    _, document, status = _read(reader, root / "portfolio.json", limit=LEDGER_LIMIT)
    result = {"status": status, "audit_events_checked": 0, "last_marked_session": None}
    if status != "READ":
        return None, [], result
    try:
        if (
            document.get("schema") != "koral-autopaper-v3.1-isolated"
            or document.get("paper_only") is not True
            or not isinstance(document.get("audit"), list)
            or not isinstance(document.get("ai_receipts"), dict)
            or not isinstance(document.get("portfolio"), dict)
            or not isinstance(document["portfolio"].get("book"), dict)
        ):
            raise ValueError("INVALID_LEDGER")
        audit = document["audit"]
        if not audit or len(audit) > MAX_EVENTS:
            result["status"] = "AUDIT_EMPTY_OR_LIMIT_EXCEEDED"
            return None, [], result
        previous = "0" * 64
        for sequence, event in enumerate(audit, 1):
            if (
                not isinstance(event, dict)
                or set(event) != {"seq", "kind", "utc", "previous", "details", "hash"}
                or type(event["seq"]) is not int
                or event["seq"] != sequence
                or event["previous"] != previous
                or not isinstance(event["kind"], str)
                or not isinstance(event["details"], dict)
                or _stamp(event["utc"]) > now
            ):
                raise ValueError("INVALID_AUDIT")
            content = {key: value for key, value in event.items() if key != "hash"}
            if not _digest(event["hash"]) or _sha(_canonical(content, chain=True)) != event["hash"]:
                raise ValueError("INVALID_AUDIT")
            previous = event["hash"]
            result["audit_events_checked"] = sequence
        marked = document["portfolio"]["book"].get("last_session")
        if marked is not None and not _day(marked):
            raise ValueError("INVALID_LAST_SESSION")
        marked_dates = [
            event["details"].get("date") for event in audit if event["kind"] == "SESSION_MARKED"
        ]
        if any(not _day(day) for day in marked_dates) or marked != max(marked_dates, default=None):
            raise ValueError("LAST_SESSION_DOES_NOT_MATCH_AUDIT")
        result.update(status="LOCAL_HASH_CHAIN_CONSISTENT", last_marked_session=marked)
        return document, audit, result
    except (ValueError, TypeError, KeyError, OverflowError):
        result["status"] = "INVALID_LEDGER_OR_AUDIT_CHAIN"
        return None, [], result


def _transactions(reader, root: Path):
    result = {"status": "NO_PENDING_TRANSACTIONS_FOUND", "checked": 0, "pending": 0, "invalid": 0}
    try:
        journals = list(
            islice(
                reader.glob(root / "forward_transactions", "*/transaction.json"),
                MAX_TRANSACTIONS + 1,
            )
        )
    except (ValueError, OSError):
        result["status"] = "TRANSACTION_ENUMERATION_UNAVAILABLE"
        return result
    if len(journals) > MAX_TRANSACTIONS:
        result["status"] = "TRANSACTION_LIMIT_EXCEEDED"
        return result
    for path in journals:
        result["checked"] += 1
        _, doc, status = _read(reader, path)
        if status != "READ":
            result["invalid"] += 1
            continue
        try:
            plain = {key: value for key, value in doc.items() if key != "record_sha256"}
            valid = (
                doc.get("schema") == "koral-forward-transaction-v1"
                and _day(doc.get("session"))
                and path.parent.name == doc["session"]
                and _digest(doc.get("before_sha256"))
                and _digest(doc.get("after_sha256"))
                and _sha(_canonical(plain)) == doc.get("record_sha256")
            )
            _, complete, completed_status = _read(reader, path.parent / "complete.json")
            if not valid or (
                completed_status != "MISSING"
                and (
                    completed_status != "READ"
                    or complete.get("after_sha256") != doc["after_sha256"]
                )
            ):
                result["invalid"] += 1
            elif completed_status == "MISSING":
                result["pending"] += 1
        except (ValueError, TypeError, KeyError):
            result["invalid"] += 1
    if result["pending"] or result["invalid"]:
        result["status"] = "RECONCILIATION_REQUIRED"
    return result


def _historical_bound_evidence(reader, root: Path, audit: list[dict], *, ledger_verified: bool):
    """Hash all runtime-required archives, including dates before daily arming.

    This is deliberately a reference check. Detailed source/receipt semantics and
    original Observer copies are reconciled separately for the bounded due dates.
    """
    result = {
        "status": "UNVERIFIED_LEDGER",
        "scope": "ALL_LEDGER_BOUND_ARCHIVE_AND_ACCEPTED_REPORT_HASHES",
        "reference_events": 0,
        "sessions_checked": 0,
        "archive_files_checked": 0,
        "accepted_report_files_checked": 0,
        "files_expected": 0,
        "first_session": None,
        "last_session": None,
    }
    if not ledger_verified:
        return result
    references = {}
    marks, bindings, sessions = {}, {}, set()

    def add_reference(path, expected, day, role):
        if not _digest(expected):
            raise ValueError("INVALID_REFERENCE_DIGEST")
        if path in references and references[path][0] != expected:
            raise ValueError("CONFLICTING_REFERENCE_DIGEST")
        references[path] = (expected, day, role)

    try:
        for event in audit:
            kind = event["kind"]
            if kind not in {"SESSION_MARKED", "OBSERVER_SOURCE_BOUND", "V3_POLICY_ACCEPTED"}:
                continue
            result["reference_events"] += 1
            details = event["details"]
            day = details.get("date")
            if not _day(day):
                raise ValueError("INVALID_REFERENCE_DATE")
            sessions.add(day)
            if kind == "SESSION_MARKED":
                if day in marks:
                    raise ValueError("DUPLICATE_MARK")
                marks[day] = details.get("market_input_sha256")
                add_reference(
                    root / "market_evidence" / (day + ".json"),
                    marks[day],
                    day,
                    "normalized_market",
                )
            elif kind == "OBSERVER_SOURCE_BOUND":
                if day in bindings or not _digest(details.get("paper_market_sha256")):
                    raise ValueError("INVALID_OR_DUPLICATE_BINDING")
                bindings[day] = details["paper_market_sha256"]
                for name in ("market", "preopen", "preclose"):
                    expected = details.get(name + "_sha256")
                    add_reference(
                        root / "observer_sources" / day / (name + ".json"),
                        expected,
                        day,
                        name + "_archive",
                    )
                    if name != "market":
                        add_reference(
                            root / "calendar" / day / (name + ".json"),
                            expected,
                            day,
                            name + "_paper_calendar",
                        )
            else:
                ticker = details.get("ticker")
                if not isinstance(ticker, str) or not _SYMBOL.fullmatch(ticker):
                    raise ValueError("INVALID_REPORT_REFERENCE")
                add_reference(
                    root / "reports" / day / ticker / "source.json",
                    details.get("report_sha256"),
                    day,
                    "accepted_model_report",
                )
            if len(sessions) > MAX_HISTORICAL_SESSIONS or len(references) > MAX_HISTORICAL_FILES:
                result["status"] = "REFERENCE_LIMIT_EXCEEDED_NOT_VERIFIED"
                return result
        if any(marks.get(day) != expected for day, expected in bindings.items()):
            raise ValueError("BINDING_WITHOUT_MATCHING_MARK")
    except (ValueError, TypeError, KeyError):
        result["status"] = "INVALID_OR_DUPLICATE_REFERENCE_METADATA"
        return result
    result.update(
        sessions_checked=len(sessions),
        files_expected=len(references),
        first_session=min(sessions, default=None),
        last_session=max(sessions, default=None),
    )
    for path, (expected, day, role) in references.items():
        try:
            if not reader.exists(path):
                status = "REFERENCED_FILE_MISSING"
            elif _sha(reader.read(path, limit=SMALL_LIMIT)) != expected:
                status = "REFERENCED_FILE_HASH_MISMATCH"
            else:
                key = (
                    "accepted_report_files_checked"
                    if role == "accepted_model_report"
                    else "archive_files_checked"
                )
                result[key] += 1
                continue
        except (ValueError, OSError, TypeError):
            status = "REFERENCED_FILE_UNREADABLE_OR_LIMIT_EXCEEDED"
        result.update(status=status, failed_session=day, failed_reference_role=role)
        return result
    result["status"] = "LOCAL_BOUND_REFERENCES_CONSISTENT"
    return result


def _heartbeat(reader, path: Path, *, now: datetime, days: dict, observer=False):
    _, document, status = _read(reader, path)
    result = {"status": status, "utc": None, "age_minutes": None, "event": None}
    if status != "READ":
        return result
    allowed = (
        {
            "SKIPPED_OUTSIDE_NY_WINDOW",
            "ATTEMPT_STARTED",
            "CAPTURED",
            "ALREADY_CAPTURED",
            "FAILED_CLOSED",
        }
        if observer
        else {
            "SUPERVISOR_NOT_ARMED",
            "ROSTER_MAINTENANCE_PAPER_DISARMED",
            "WAITING_FOR_DECLARED_FIRST_SESSION",
            "NON_SESSION_NO_MODEL_CALLS",
            "DAILY_ROSTER_FROZEN",
            "TODAY_ROSTER_MISSED_NEXT_SESSION_PREPARED",
            "WAITING_FOR_REQUIRED_OBSERVATIONS",
            "FORWARD_SESSION_PROCESSED",
            "DAILY_CONTROLLER_FAILED_CLOSED",
        }
    )
    try:
        instant = _stamp(document.get("utc"))
        day = document.get("session" if observer else "ny_session")
        if (
            not _day(day)
            or day not in days
            or not days[day]["start"] <= instant < days[day]["end"]
            or document.get("status") not in allowed
        ):
            raise ValueError("INVALID_HEARTBEAT")
        result.update(
            utc=instant.isoformat(),
            age_minutes=round((now - instant).total_seconds() / 60, 3),
            event=document["status"],
        )
        if instant > now:
            result["status"] = "FUTURE_TIMESTAMP"
        elif now - instant > timedelta(minutes=14):
            result["status"] = "STALE"
        elif document["status"] in {"FAILED_CLOSED", "DAILY_CONTROLLER_FAILED_CLOSED"}:
            result["status"] = "RECENT_FAILURE"
        else:
            result["status"] = "RECENT"
    except (ValueError, TypeError, KeyError, OverflowError):
        result["status"] = "INVALID"
    return result


def _controls(config, now):
    result = {
        "status": "ATTENTION_REQUIRED",
        "current_armed": None,
        "managed_observer": None,
        "model_billing_mode": "UNRECOGNIZED",
        "plan_only_approval_present": False,
    }
    if not isinstance(config, dict):
        return result
    for key, source in (("current_armed", "armed"), ("managed_observer", "managed_observer")):
        value = config.get(source)
        result[key] = value if type(value) is bool else None
    if config.get("model_billing_mode") in ("provider_api", "chatgpt_plan_only"):
        result["model_billing_mode"] = config["model_billing_mode"]
    try:
        result["plan_only_approval_present"] = (
            result["model_billing_mode"] == "chatgpt_plan_only"
            and _digest(config.get("chatgpt_account_sha256"))
            and _stamp(config.get("chatgpt_credits_disabled_confirmed_at")) <= now
        )
    except (ValueError, TypeError, OverflowError):
        result["plan_only_approval_present"] = False
    if (
        result["current_armed"] is True
        and result["managed_observer"] is True
        and result["plan_only_approval_present"]
    ):
        result["status"] = "PLAN_ONLY_CONTROLS_DECLARED_NOT_REMOTELY_VERIFIED"
    return result


def _summary(reader, ops: Path, day: str, times: dict, now: datetime):
    _, document, status = _read(reader, ops / "summaries" / (day + ".json"))
    completed_at = None
    if status == "READ":
        try:
            instant = _stamp(document.get("utc"))
            if (
                document.get("ny_session") != day
                or not times["start"] <= instant < times["end"]
                or instant > now
            ):
                raise ValueError("INVALID_SUMMARY")
            if document.get("status") == _COMPLETE:
                if (
                    instant < times["processing_start"]
                    or document.get("data_complete") is not True
                    or document.get("last_mark") != day
                ):
                    raise ValueError("INVALID_COMPLETE_SUMMARY")
                status, completed_at = "REPORTED_COMPLETE", instant
            else:
                status = "INCOMPLETE"
        except (ValueError, TypeError, OverflowError):
            status = "INVALID"
    _, error, error_status = _read(reader, ops / "summaries" / (day + "_error.json"))
    if error_status == "READ":
        try:
            instant = _stamp(error.get("utc"))
            if (
                error.get("ny_session") != day
                or error.get("status") != _FAILED
                or not times["start"] <= instant < times["end"]
                or instant > now
            ):
                raise ValueError("INVALID_ERROR")
            error_status = (
                "EARLIER_ERROR_RETAINED"
                if completed_at is not None and instant < completed_at
                else "UNRESOLVED_OR_LATER_ERROR"
            )
        except (ValueError, TypeError, OverflowError):
            error_status = "INVALID"
    return {"status": status, "error_record": error_status}


def _roster(reader, ops: Path, day: str, times: dict):
    _, record, status = _read(reader, ops / "rosters" / (day + ".json"))
    if status != "READ":
        return None, status
    try:
        plain = {key: value for key, value in record.items() if key != "record_sha256"}
        if (
            record.get("schema") != "koral-managed-daily-roster-v1"
            or record.get("session") != day
            or record.get("mode") != "OBSERVE_ONLY"
            or record.get("shorts_allowed") is not False
            or not _symbols(record.get("watchlist"))
            or not isinstance(record.get("active_symbols"), list)
            or not set(record["active_symbols"]).issubset(record["watchlist"])
            or _stamp(record.get("declared_utc")) >= times["preopen_start"]
            or _sha(_canonical(plain)) != record.get("record_sha256")
        ):
            raise ValueError("INVALID_ROSTER")
        proof = record.get("scan_proof")
        if proof is not None:
            raw = bytes.fromhex(proof["raw_hex"])
            if _sha(raw) != proof["sha256"] or json.loads(raw) != proof["queue"]:
                raise ValueError("INVALID_SCAN_PROOF")
        return record, "LOCAL_SEAL_CONSISTENT"
    except (ValueError, TypeError, KeyError, OverflowError):
        return None, "INVALID"


def _event_groups(audit: list[dict], day: str):
    grouped = {}
    for event in audit:
        if event["details"].get("date") == day or event["details"].get("session") == day:
            grouped.setdefault(event["kind"], []).append(event)
    return grouped


def _mark_events(groups: dict, day: str, times: dict, roster):
    kinds = ("OBSERVER_SOURCE_BOUND", "SESSION_MARKED", "OBSERVER_FORWARD_SESSION_PROCESSED")
    rows = [groups.get(kind, []) for kind in kinds]
    if not any(rows):
        return None, "NOT_RECORDED"
    if groups.get("FORWARD_BASELINE_ONLY") and not rows[2]:
        return None, "BASELINE_ONLY_NOT_FORWARD_SESSION"
    if any(len(events) != 1 for events in rows):
        return None, "MISSING_OR_DUPLICATE_EVENTS"
    binding, mark, processed = [events[0] for events in rows]
    b, m, p = (event["details"] for event in (binding, mark, processed))
    try:
        if (
            not binding["seq"] < mark["seq"] < processed["seq"]
            or any(event["details"].get("date") != day for event in (binding, mark, processed))
            or any(
                not times["processing_start"] <= _stamp(event["utc"]) < times["end"]
                for event in (binding, mark, processed)
            )
            or not _stamp(binding["utc"]) <= _stamp(mark["utc"]) <= _stamp(processed["utc"])
            or any(
                not _digest(b.get(key))
                for key in (
                    "market_sha256",
                    "preopen_sha256",
                    "preclose_sha256",
                    "paper_market_sha256",
                )
            )
            or b.get("origin") != "IMMUTABLE_OBSERVER_SOURCE_NOT_EXCHANGE_CERTIFIED"
            or m.get("backfill") is not False
            or m.get("market_input_sha256") != b["paper_market_sha256"]
            or p.get("original_source_sha256") != b["market_sha256"]
            or p.get("broker_connected") is not False
            or not _symbols(b.get("source_roster"))
            or roster is None
            or b["source_roster"] != roster["watchlist"]
        ):
            raise ValueError("INVALID_MARK_BINDING")
        return (binding, mark, processed), "LOCAL_EVENT_LINKS_CONSISTENT"
    except (ValueError, TypeError, KeyError, OverflowError):
        return None, "INVALID_EVENT_LINKS"


def _bar(value) -> bool:
    if not isinstance(value, dict) or set(value) != {"open", "high", "low", "close"}:
        return False
    return (
        all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in value.values())
        and value["low"]
        <= min(value["open"], value["close"])
        <= max(value["open"], value["close"])
        <= value["high"]
    )


def _sources(reader, account: Path, observer: Path, day: str, times: dict, links):
    if links is None:
        return {"status": "UNVERIFIED_WITHOUT_MARK_BINDING", "files_checked": 0}
    binding, mark, _ = links
    b = binding["details"]
    names = b["source_roster"]
    result = {"status": "LOCAL_ORIGINAL_ARCHIVE_AND_MARK_CONSISTENT", "files_checked": 0}
    documents = {}
    try:
        for name in ("market", "preopen", "preclose"):
            original = (
                observer / "market" / (day + ".json")
                if name == "market"
                else observer / "calendar" / day / (name + ".json")
            )
            paths = [
                (name + "_original", original),
                (name + "_archive", account / "observer_sources" / day / (name + ".json")),
            ]
            if name != "market":
                paths.append(
                    (name + "_paper_calendar", account / "calendar" / day / (name + ".json"))
                )
            for role, path in paths:
                raw, doc, state = _read(reader, path)
                result["files_checked"] += 1
                if state != "READ":
                    result["status"] = "SOURCE_MISSING_OR_UNREADABLE"
                    result["failed_check"] = role
                    return result
                if _sha(raw) != b[name + "_sha256"]:
                    result["status"] = "ORIGINAL_OR_ARCHIVED_SOURCE_HASH_MISMATCH"
                    result["failed_check"] = role
                    return result
                documents[name] = doc
        market = documents["market"]
        started = _stamp(market.get("capture_started_utc"))
        completed = _stamp(market.get("capture_completed_utc"))
        bars = market.get("ohlc_usd")
        fx0, fx1 = market.get("fx_gbp_per_usd_open"), market.get("fx_gbp_per_usd_close")
        if (
            market.get("schema") != "koral-observation-v1"
            or market.get("mode") != "OBSERVE_ONLY"
            or market.get("session") != day
            or market.get("phase") != "close"
            or market.get("orders") != []
            or market.get("source") != "YAHOO_SECONDARY_NOT_EXCHANGE_CERTIFIED"
            or market.get("corporate_actions") != "QUERIED_NO_UNMODELLED_EVENTS_REPORTED"
            or not times["processing_start"] <= started <= completed < times["end"]
            or completed > _stamp(binding["utc"])
            or completed - started > timedelta(minutes=90)
            or not isinstance(bars, dict)
            or set(bars) != set(names) | {"SPY"}
            or not all(_bar(bar) for bar in bars.values())
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or not 0.3 <= v <= 2
                for v in (fx0, fx1)
            )
            or abs(fx1 / fx0 - 1) > 0.05
        ):
            raise ValueError("INVALID_MARKET_OBSERVATION")
        for phase in ("preopen", "preclose"):
            doc = documents[phase]
            stamp = _stamp(doc.get("observed_utc"))
            lower, upper = times[phase + "_start"], times[phase + "_end"]
            earnings = doc.get("earnings")
            if (
                doc.get("session") != day
                or doc.get("phase") != phase
                or doc.get("source_policy") != "TWO_SECONDARY_FEEDS_MUST_AGREE_OR_BLOCK"
                or not lower <= stamp <= upper
                or stamp > started
                or not isinstance(earnings, dict)
                or set(earnings) != set(names)
            ):
                raise ValueError("INVALID_EARNINGS_ENVELOPE")
            for row in earnings.values():
                if (
                    not isinstance(row, dict)
                    or set(row)
                    != {
                        "asof_date",
                        "next_earnings_date",
                        "last_earnings_date",
                        "source",
                        "verified",
                        "observed_at_utc",
                    }
                    or row["asof_date"] != day
                    or type(row["verified"]) is not bool
                    or not lower <= _stamp(row["observed_at_utc"]) <= stamp
                    or (row["verified"] and row["source"] != "YAHOO+NASDAQ_CONSENSUS_SECONDARY")
                ):
                    raise ValueError("INVALID_EARNINGS_ROW")
        raw, normalized, state = _read(reader, account / "market_evidence" / (day + ".json"))
        result["files_checked"] += 1
        if state != "READ":
            result["status"] = "NORMALIZED_MARKET_EVIDENCE_MISSING_OR_UNREADABLE"
            return result
        if _sha(raw) != b["paper_market_sha256"]:
            result["status"] = "NORMALIZED_MARKET_EVIDENCE_HASH_MISMATCH"
            return result
        if (
            normalized.get("schema") != "koral-autopilot-daily-input-v1"
            or normalized.get("session") != day
            or normalized.get("bars") != {name: bars[name] for name in names}
            or normalized.get("gbp_per_usd_open") != fx0
            or normalized.get("gbp_per_usd_close") != fx1
            or normalized.get("spy_price_only_close_usd") != bars["SPY"]["close"]
            or not completed <= _stamp(normalized.get("captured_at_utc")) <= _stamp(mark["utc"])
        ):
            raise ValueError("NORMALIZED_MARKET_DIFFERS_FROM_SOURCE")
    except (ValueError, TypeError, KeyError, OverflowError):
        result["status"] = "INVALID_SOURCE_CONTENT_OR_TIMING"
    return result


def _research(reader, account: Path, day: str, times: dict, state, groups, links):
    result = {
        "status": "UNVERIFIED_WITHOUT_LEDGER_BINDING",
        "attempts_started": 0,
        "terminal_receipts": 0,
        "reports_verified": 0,
        "failures_recorded": 0,
        "incomplete_attempts": 0,
        "accepted_plans": 0,
    }
    if state is None or links is None:
        return result
    binding, _, processed = links
    digest = binding["details"]["market_sha256"]
    names = binding["details"]["source_roster"]
    starts = groups.get("AI_RESEARCH_STARTED", [])
    receipts = groups.get("AI_RESEARCH_RECEIPT", [])
    accepted = groups.get("V3_POLICY_ACCEPTED", [])
    slate = groups.get("DAILY_RESEARCH_SLATE", [])
    saved = {
        key: value
        for key, value in state["ai_receipts"].items()
        if isinstance(key, str) and key.startswith(day + ":")
    }
    result.update(
        attempts_started=len(starts), terminal_receipts=len(receipts), accepted_plans=len(accepted)
    )
    if any(event["details"].get("date") != day for event in starts + receipts + accepted + slate):
        result["status"] = "INVALID_OR_INCOMPLETE_RESEARCH_EVIDENCE"
        return result
    if not starts and not receipts and not accepted and not saved:
        if slate:
            detail = slate[0]["details"]
            if (
                len(slate) != 1
                or detail.get("market_sha256") != digest
                or detail.get("candidates") != []
            ):
                result["status"] = "INVALID_OR_INCOMPLETE_RESEARCH_EVIDENCE"
                return result
        result["status"] = "NO_ATTEMPTS_RECORDED"
        return result
    try:
        tickers = [event["details"].get("ticker") for event in starts]
        if (
            len(starts) > 3
            or len(set(tickers)) != len(tickers)
            or not set(tickers).issubset(names)
            or len(slate) != 1
            or len(accepted) > 1
        ):
            raise ValueError("INVALID_RESEARCH_COUNTS")
        candidates = slate[0]["details"].get("candidates")
        limit = slate[0]["details"].get("daily_company_limit")
        if (
            type(limit) is not int
            or not 0 <= limit <= 3
            or len(starts) > limit
            or not isinstance(candidates, list)
            or len(candidates) > limit
            or len(set(candidates)) != len(candidates)
            or not set(candidates).issubset(names)
            or not set(tickers).issubset(candidates)
            or slate[0]["details"].get("market_sha256") != digest
            or slate[0]["seq"] <= processed["seq"]
            or not _stamp(processed["utc"]) <= _stamp(slate[0]["utc"]) < times["end"]
        ):
            raise ValueError("INVALID_RESEARCH_SLATE")
        if set(saved) != {day + ":" + ticker for ticker in tickers}:
            raise ValueError("ORPHAN_RESEARCH_RECEIPT")
        by_ticker = {}
        for event in receipts:
            ticker = event["details"].get("ticker")
            if ticker not in tickers or ticker in by_ticker:
                raise ValueError("DUPLICATE_OR_ORPHAN_RECEIPT")
            by_ticker[ticker] = event
        for event in starts:
            ticker = event["details"]["ticker"]
            if (
                event["details"].get("source_sha256") != digest
                or event["seq"] <= slate[0]["seq"]
                or not _stamp(slate[0]["utc"]) <= _stamp(event["utc"]) < times["end"]
            ):
                raise ValueError("RESEARCH_SOURCE_CHANGED")
            received = by_ticker.get(ticker)
            current = saved[day + ":" + ticker]
            if received is None:
                if (
                    not isinstance(current, dict)
                    or current.get("status") != "IN_PROGRESS"
                    or current.get("ticker") != ticker
                    or current.get("session") != day
                ):
                    raise ValueError("INVALID_IN_PROGRESS_RECEIPT")
                result["incomplete_attempts"] += 1
                continue
            detail = received["details"]
            receipt = detail.get("receipt")
            if (
                not isinstance(receipt, dict)
                or detail.get("source_sha256") != digest
                or received["seq"] <= event["seq"]
                or _stamp(received["utc"]) < _stamp(event["utc"])
                or receipt.get("session") != day
                or receipt.get("decision") not in {"NO_TRADE", "CONDITIONAL_LONG"}
                or current != {**receipt, "status": "COMPLETE", "ticker": ticker}
            ):
                raise ValueError("RECEIPT_AUDIT_MISMATCH")
            if "source_file" not in receipt and "source_sha256" not in receipt:
                if (
                    receipt.get("research_complete") is not False
                    or receipt.get("decision") != "NO_TRADE"
                ):
                    raise ValueError("REPORT_BINDING_MISSING")
                result["failures_recorded"] += 1
                continue
            expected = f"reports/{day}/{ticker}/source.json"
            if (
                not isinstance(receipt.get("source_file"), str)
                or receipt["source_file"].replace("\\", "/") != expected
                or not _digest(receipt.get("source_sha256"))
            ):
                raise ValueError("INVALID_REPORT_REFERENCE")
            raw, report, read_status = _read(
                reader, account / "reports" / day / ticker / "source.json"
            )
            if (
                read_status != "READ"
                or _sha(raw) != receipt["source_sha256"]
                or report.get("ticker") != ticker
                or report.get("session") != day
                or _stamp(received["utc"]) >= times["end"]
                or not _stamp(event["utc"])
                <= _stamp(report.get("generated_utc"))
                <= _stamp(received["utc"])
            ):
                raise ValueError("REPORT_EVIDENCE_MISMATCH")
            result["reports_verified"] += 1
            if receipt.get("decision") == "CONDITIONAL_LONG" and not any(
                accepted_event["details"].get("ticker") == ticker
                and accepted_event["details"].get("intent_id") == receipt.get("intent_id")
                for accepted_event in accepted
            ):
                raise ValueError("CONDITIONAL_RECEIPT_WITHOUT_ACCEPTED_PLAN")
        for event in accepted:
            detail = event["details"]
            ticker = detail.get("ticker")
            received = by_ticker.get(ticker)
            receipt = received["details"]["receipt"] if received else {}
            if (
                detail.get("verified_policy") is not True
                or receipt.get("decision") != "CONDITIONAL_LONG"
                or detail.get("report_sha256") != receipt.get("source_sha256")
                or detail.get("intent_id") != receipt.get("intent_id")
                or not processed["seq"] < event["seq"] < received["seq"]
            ):
                raise ValueError("ACCEPTED_PLAN_REPORT_MISMATCH")
        _, budget, budget_status = _read(reader, account / "model_budgets" / (day + ".json"))
        if (
            budget_status != "READ"
            or budget.get("schema") != "koral-request-budget-v1"
            or not isinstance(budget.get("requests"), dict)
        ):
            raise ValueError("RESEARCH_BUDGET_MISSING")
        requests = budget["requests"]
        policy = budget.get("policy")
        bounds = {
            "max_model_requests_per_session": (1, 60),
            "max_reserved_tokens_per_session": (1000, 250000),
            "max_output_tokens_per_request": (256, 2048),
            "max_prompt_bytes_per_request": (1000, 100000),
        }
        if (
            not isinstance(policy, dict)
            or any(
                type(policy.get(key)) is not int or not low <= policy[key] <= high
                for key, (low, high) in bounds.items()
            )
            or (
                "model_billing_mode" in policy
                and policy["model_billing_mode"] != "chatgpt_plan_only"
            )
        ):
            raise ValueError("INVALID_BUDGET_POLICY")
        if len(requests) > policy["max_model_requests_per_session"] or any(
            not isinstance(row, dict)
            or type(row.get("charged_units")) is not int
            or row["charged_units"] < 0
            for row in requests.values()
        ):
            raise ValueError("INVALID_REQUEST_BUDGET")
        if any(
            type(event["details"].get("request_budget", {}).get("model_requests")) is not int
            or event["details"]["request_budget"]["model_requests"] > len(requests)
            for event in receipts
        ):
            raise ValueError("REQUEST_BUDGET_REGRESSED")
        result["model_requests_recorded"] = len(requests)
        result["unknown_usage_or_unfinished_requests"] = sum(
            row.get("status") != "COMPLETE_WITH_USAGE" for row in requests.values()
        )
        result["reservations_exceeded"] = sum(
            bool(row.get("reservation_exceeded")) for row in requests.values()
        )
        result["status"] = (
            "INCOMPLETE_OR_USAGE_UNRESOLVED"
            if result["incomplete_attempts"]
            or result["unknown_usage_or_unfinished_requests"]
            or result["reservations_exceeded"]
            else "FAILED_ATTEMPTS_RECORDED"
            if result["failures_recorded"]
            else "LOCAL_RECEIPT_AND_REPORT_LINKS_CONSISTENT"
        )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        result["status"] = "INVALID_OR_INCOMPLETE_RESEARCH_EVIDENCE"
    return result


def audit_evidence(data_root: Path, *, now: datetime, reader, calendar: dict) -> dict:
    """Return sanitized session evidence; the caller owns stable reads and output."""
    instant = _stamp(now)
    result = {
        "schema": "koral-local-session-evidence-v1",
        "checked_utc": instant.isoformat(),
        "status": "ATTENTION_REQUIRED",
        "session_evidence_status": "NOT_ASSESSED",
        "scope": "LOCAL_FILE_CONSISTENCY_ONLY",
        "calendar_status": "VALID",
        "configuration_status": "NOT_CHECKED",
        "effective_session": None,
        "due_session_count": 0,
        "unreviewed_due_sessions": 0,
        "sessions": [],
        "limits": {
            "max_sessions": MAX_SESSIONS,
            "max_audit_events": MAX_EVENTS,
            "max_transactions": MAX_TRANSACTIONS,
            "max_historical_sessions": MAX_HISTORICAL_SESSIONS,
            "max_historical_reference_files": MAX_HISTORICAL_FILES,
        },
        "independent_authenticity_verified": False,
        "accounting_or_performance_verified": False,
        "unattended_execution_verified": False,
        "historical_arming_verified": False,
        "transaction_staged_account_verification": "NOT_PERFORMED",
    }
    try:
        sessions, times, today = _calendar(calendar, instant)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        result["calendar_status"] = "INVALID_OR_OUTSIDE_COVERAGE"
        return result
    ops = data_root / "paper_operations" / "daily_v1"
    account = data_root / "paper" / "v3_autopilot"
    observer = data_root / "observations" / "koral_v3"
    _, cfg, cfg_status = _read(reader, ops / "config.json")
    result["configuration_status"] = cfg_status
    result["current_controls"] = _controls(cfg, instant)
    result["heartbeats"] = {
        "controller": _heartbeat(reader, ops / "heartbeat.json", now=instant, days=times),
        "observer": _heartbeat(
            reader, observer / "scheduler_heartbeat.json", now=instant, days=times, observer=True
        ),
    }
    state, audit, result["ledger"] = _ledger(reader, account, instant)
    result["historical_bound_evidence"] = _historical_bound_evidence(
        reader, account, audit, ledger_verified=state is not None
    )
    result["transactions"] = _transactions(reader, account)
    if cfg_status != "READ":
        return result
    if (
        cfg.get("schema") != "koral-daily-paper-v1"
        or cfg.get("effective_session") not in sessions
        or type(cfg.get("armed")) is not bool
    ):
        result["configuration_status"] = "INVALID_OR_NO_DECLARED_EFFECTIVE_SESSION"
        return result
    result["configuration_status"] = "VALID_DECLARED_EFFECTIVE_SESSION"
    result["effective_session"] = effective = cfg["effective_session"]
    due = [day for day in sessions if effective <= day <= today and times[day]["due"] <= instant]
    result["due_session_count"] = len(due)
    result["unreviewed_due_sessions"] = max(0, len(due) - MAX_SESSIONS)
    for day in due[-MAX_SESSIONS:]:
        row = {"session": day, "status": "ATTENTION_REQUIRED"}
        row["summary"] = _summary(reader, ops, day, times[day], instant)
        roster, row["roster_status"] = _roster(reader, ops, day, times[day])
        groups = _event_groups(audit, day)
        links, row["mark_events_status"] = _mark_events(groups, day, times[day], roster)
        if state is None:
            row["mark_events_status"] = "UNVERIFIED_LEDGER"
        row["sources"] = _sources(reader, account, observer, day, times[day], links)
        row["research"] = _research(reader, account, day, times[day], state, groups, links)
        if (
            row["summary"]["status"] == "REPORTED_COMPLETE"
            and row["summary"]["error_record"] in {"MISSING", "EARLIER_ERROR_RETAINED"}
            and row["roster_status"] == "LOCAL_SEAL_CONSISTENT"
            and row["mark_events_status"] == "LOCAL_EVENT_LINKS_CONSISTENT"
            and row["sources"]["status"] == "LOCAL_ORIGINAL_ARCHIVE_AND_MARK_CONSISTENT"
            and row["research"]["status"]
            in {"NO_ATTEMPTS_RECORDED", "LOCAL_RECEIPT_AND_REPORT_LINKS_CONSISTENT"}
        ):
            row["status"] = "LOCAL_EVIDENCE_CONSISTENT"
        result["sessions"].append(row)
    if (
        result["ledger"]["status"] == "LOCAL_HASH_CHAIN_CONSISTENT"
        and result["historical_bound_evidence"]["status"] == "LOCAL_BOUND_REFERENCES_CONSISTENT"
        and result["transactions"]["status"] == "NO_PENDING_TRANSACTIONS_FOUND"
        and not result["unreviewed_due_sessions"]
        and all(row["status"] == "LOCAL_EVIDENCE_CONSISTENT" for row in result["sessions"])
    ):
        result["session_evidence_status"] = (
            "NO_SESSIONS_DUE" if not due else "LOCAL_EVIDENCE_CONSISTENT"
        )
        if result["current_controls"][
            "status"
        ] == "PLAN_ONLY_CONTROLS_DECLARED_NOT_REMOTELY_VERIFIED" and all(
            item["status"] == "RECENT" for item in result["heartbeats"].values()
        ):
            result["status"] = result["session_evidence_status"]
    else:
        result["session_evidence_status"] = "ATTENTION_REQUIRED"
    return result
