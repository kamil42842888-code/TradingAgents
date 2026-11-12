#!/usr/bin/env python3
r"""Koral Equities — observation-only runner; automatic task management REMOVED.

Put this file in the existing Koral Equities project root and invoke it with
.\.venv\Scripts\python.exe. The scheduled action uses a single `observe --once`
request on the operator's installed project; it NEVER uses a trading, paper,
account-initialization, or broker command.

The short-lived scheduled task protects against a PowerShell window being
closed. It cannot operate if the computer is powered off, network unavailable,
or the user is logged out (InteractiveToken scheduling). It cannot recreate
missed NY as-of data. Do not claim a research/trading success from this alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, time as clock_time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

SCHEMA = "koral-observer-scheduler-v1"
TASK_NAME = "KoralEquities_ResearchObserver_5min"
WATCHLIST = ("COST", "JPM", "WMT")
NY = ZoneInfo("America/New_York")
PROJECT = Path(__file__).resolve().parent
OBS_HOME = Path.home() / ".tradingagents" / "observations" / "koral_v3"
ROSTER_SCHEMA = "koral-predeclared-research-roster-v1"
ROSTERS = Path.home() / ".tradingagents" / "swing_research" / "rosters"
QUEUES = Path.home() / ".tradingagents" / "swing_research" / "runs"


def watchlist_for_session(
    session: str, *, rosters: Path | None = None, queues: Path | None = None
) -> tuple[str, ...]:
    """One archived, predeclared research roster, or the original pilot trio.

    A declared but corrupt roster must fail closed rather than silently fall
    back to COST/JPM/WMT. It NEVER updates a paper portfolio or past archive.
    """
    import re
    from datetime import date

    # Managed daily PAPER roster is predeclared by the separate controller.
    # Reading this declaration never opens a portfolio or executes trading code.
    if rosters is None and queues is None:
        from tradingagents.paper.autopilot.daily_roster import observer_roster

        managed = observer_roster(session)
        if managed is not None:
            return managed
    rosters = rosters if rosters is not None else ROSTERS
    queues = queues if queues is not None else QUEUES

    try:
        day = date.fromisoformat(session)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid research roster session") from exc
    if day.isoformat() != session:
        raise ValueError("Noncanonical research roster session")
    path = rosters / (session + ".json")
    if path.is_symlink():
        raise ValueError("Unsafe saved research roster")
    if not path.exists():
        raise ValueError("DECLARED_ROSTER_MISSING; no fallback watchlist is permitted")
    if not path.is_file():
        raise ValueError("Unsafe saved research roster")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(record, dict)
        or record.get("schema") != ROSTER_SCHEMA
        or record.get("session") != session
        or record.get("mode") != "OBSERVE_ONLY"
        or record.get("approved_by_operator") is not True
        or record.get("trading_enabled") is not False
    ):
        raise ValueError("Unapproved or malformed observer-only roster")
    source_day = record.get("source_session")
    try:
        earlier = date.fromisoformat(source_day)
    except (ValueError, TypeError) as exc:
        raise ValueError("No valid earlier source scan") from exc
    if earlier.isoformat() != source_day or not earlier < day or (day - earlier).days > 4:
        raise ValueError("Research roster is not based on a recent prior session")
    approved_at = datetime.fromisoformat(record.get("approved_utc", ""))
    if approved_at.tzinfo is None or approved_at.utcoffset() != timedelta(0):
        raise ValueError("Research roster approval must have a real UTC timestamp")
    first_window = datetime.combine(day, clock_time(8, 45), tzinfo=NY)
    if approved_at >= first_window.astimezone(UTC):
        raise ValueError("Roster was approved after the preopen capture cutoff")
    queue = queues / source_day / "queue.json"
    if queue.is_symlink() or not queue.is_file():
        raise ValueError("Original immutable source queue is missing or unsafe")
    source_raw = queue.read_bytes()
    if hashlib.sha256(source_raw).hexdigest() != record.get("queue_sha256"):
        raise ValueError("Source scanner queue hash changed since roster approval")
    data = json.loads(source_raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid scanner queue structure")
    names = record.get("watchlist")
    if (
        not isinstance(names, list)
        or not 1 <= len(names) <= 8
        or any(type(t) is not str or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", t) for t in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("Invalid bounded observer watchlist")
    if (
        data.get("schema") != "koral-swing-research-queue-v1"
        or data.get("session") != source_day
        or data.get("status") != "RESEARCH_QUEUE_ONLY_NO_TRADE_AUTHORITY"
        or data.get("next_session_research_watchlist") != names
        or data.get("model_calls") != 0
        or data.get("paper_orders") != 0
        or data.get("portfolio_reads") != 0
        or data.get("portfolio_writes") != 0
        or data.get("broker_connected") is not False
        or data.get("candidate_count") != len(names)
    ):
        raise ValueError("Roster does not match a complete, source-only Swing Alpha queue")
    candidates = data.get("candidates")
    if (
        not isinstance(candidates, list)
        or [row.get("ticker") for row in candidates if isinstance(row, dict)] != names
        or any(
            not isinstance(row, dict)
            or row.get("execution_authority") != "NONE_RESEARCH_ONLY"
            or row.get("earnings_verification") != "NOT_CAPTURED_FOR_NEXT_SESSION"
            for row in candidates
        )
    ):
        raise ValueError("Roster candidates have unexpected trading authority")
    return tuple(names)


# Exact source bytes proved by the user's successful 105-test Windows installer.
APPROVED_GIT_BLOBS = {
    "tradingagents/paper/autopilot/observe.py": "4e2f052bab98b14965259a4d805f7a5133222117",
    "tradingagents/paper/autopilot/observer_lock.py": "4416ea78e64cdbe7b028c1f23c195ae183f8fc76",
    "tradingagents/paper/autopilot/feeds.py": "4146196e509bdb617baf0228abce4821aabc1780",
}


def python_exe(project: Path = PROJECT) -> Path:
    return project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def preflight(project: Path = PROJECT, *, verify_invoker: bool = True) -> None:
    root = project.resolve()
    if (root / ".git").exists():
        raise RuntimeError("This scheduler targets the verified NON-GIT Koral Equities folder")
    executable = python_exe(root)
    if not executable.is_file():
        raise RuntimeError("Existing project .venv Python missing: " + str(executable))
    if verify_invoker and Path(sys.executable).resolve() != executable.resolve():
        raise RuntimeError("Use the project's own .venv Python, not another installation")
    from tradingagents.paper.autopilot.daily_supervisor import check_integrity

    check_integrity(root)


def ny_phase(instant: datetime) -> tuple[str, str | None]:
    """True timestamp -> canonical NY session/phase. Never fake/override a date."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Scheduler clock must have a time zone")
    ny = instant.astimezone(NY)
    session = ny.date().isoformat()
    from tradingagents.paper.autopilot.daily_support import is_session, session_windows

    if not is_session(session):
        return session, None
    for phase, (start, end) in session_windows(session).items():
        if start <= ny.time() <= end:
            return session, phase
    return session, None


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".scheduler-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def append_attempt(home: Path, run: dict) -> None:
    """Separate append-only operation log; never modify market/calendar files."""
    date_ = run["session"]
    folder = home / "scheduler_runs"
    folder.mkdir(parents=True, exist_ok=True)
    data = json.dumps(run, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"
    with (folder / (date_ + ".jsonl")).open("a", encoding="utf-8") as stream:
        stream.write(data)


def saved_path(home: Path, session: str, phase: str) -> Path:
    return (
        home / "market" / (session + ".json")
        if phase == "close"
        else home / "calendar" / session / (phase + ".json")
    )


def parse_capture(output: str) -> dict:
    try:
        parsed = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Observer returned no machine-readable capture receipt") from exc
    if (
        not isinstance(parsed, dict)
        or parsed.get("mode") != "OBSERVE_ONLY"
        or parsed.get("orders") != 0
    ):
        raise RuntimeError("Observer receipt did not prove observation-only zero-order mode")
    if parsed.get("status") not in {"CAPTURED", "ALREADY_CAPTURED"}:
        raise RuntimeError(
            "Observer returned an unexpected capture status: " + str(parsed.get("status"))
        )
    return parsed


def run_tick(
    *,
    now: datetime | None = None,
    project: Path = PROJECT,
    home: Path = OBS_HOME,
    runner=None,
    require_preflight: bool = True,
) -> dict:
    """One bounded scheduled attempt; source-only and safe across missed windows."""
    now = now or datetime.now(UTC)
    session, phase = ny_phase(now)
    selected = watchlist_for_session(session) if phase is not None else ()
    tick = {
        "schema": SCHEMA,
        "session": session,
        "phase": phase,
        "utc": now.astimezone(UTC).isoformat(),
        "watchlist": list(selected),
        "trading_enabled": False,
        "broker_connected": False,
        "account_writes": 0,
        "run_id": uuid4().hex,
        "status": "SKIPPED_OUTSIDE_NY_WINDOW",
    }
    if require_preflight:
        preflight(project)
    if phase is None:
        atomic_json(home / "scheduler_heartbeat.json", tick)
        return tick
    command = [
        str(python_exe(project)),
        "-m",
        "tradingagents.paper.autopilot.cli",
        "observe",
        "--once",
        "--tickers",
        *selected,
    ]
    tick["status"] = "ATTEMPT_STARTED"
    atomic_json(home / "scheduler_heartbeat.json", tick)
    if runner is None:
        runner = subprocess.run
    try:
        outcome = runner(
            command, cwd=project, capture_output=True, text=True, timeout=145, check=False
        )
        if outcome.returncode != 0:
            raise RuntimeError(
                "Observer returned nonzero exit "
                + str(outcome.returncode)
                + ": "
                + str(outcome.stderr).strip()[-600:]
            )
        report = parse_capture(outcome.stdout)
        if report.get("session") != session or report.get("phase") != phase:
            raise RuntimeError("Observer receipt does not match scheduled NY session/phase")
        if (
            phase == "close"
            and report["status"] == "CAPTURED"
            and set(report.get("symbols", [])) != set(selected) | {"SPY"}
        ):
            raise RuntimeError("Observer receipt did not match the declared universe")
        evidence = saved_path(home, session, phase)
        if not evidence.is_file() or evidence.is_symlink():
            raise RuntimeError("Capture receipt lacked safe archived evidence")
        raw = evidence.read_bytes()
        if report.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise RuntimeError("Capture receipt hash differs from archived evidence")
        archived = json.loads(raw)
        observed = (
            set(archived.get("ohlc_usd", {}))
            if phase == "close"
            else set(archived.get("earnings", {}))
        )
        expected = set(selected) | ({"SPY"} if phase == "close" else set())
        if (
            archived.get("session") != session
            or observed != expected
            or (
                phase == "close"
                and (archived.get("orders") != [] or archived.get("mode") != "OBSERVE_ONLY")
            )
            or (phase != "close" and archived.get("phase") != phase)
        ):
            raise RuntimeError("Saved archive is not for declared research universe")
        tick["status"] = report["status"]
        tick["observation_sha256"] = report["sha256"]
        tick["observation_file_present"] = True
    except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError) as exc:
        tick["status"] = "FAILED_CLOSED"
        tick["failure"] = (type(exc).__name__ + ": " + str(exc))[-800:]
    tick["completed_utc"] = datetime.now(UTC).isoformat()
    atomic_json(home / "scheduler_heartbeat.json", tick)
    append_attempt(home, tick)
    return tick


def check_health(*, now: datetime | None = None, home: Path = OBS_HOME) -> dict:
    now = now or datetime.now(UTC)
    session, phase = ny_phase(now)
    from tradingagents.paper.autopilot.daily_support import is_session, session_windows

    if not is_session(session):
        return {"schema": SCHEMA, "session": session, "status": "NON_SESSION", "issues": []}
    selected = watchlist_for_session(session)
    result = {
        "schema": SCHEMA,
        "session": session,
        "checked_utc": now.astimezone(UTC).isoformat(),
        "tickers": list(selected),
        "required_snapshot": {},
        "issues": [],
        "paper_trading": "DISABLED_BY_THIS_SCHEDULER",
    }
    status_file = home / "scheduler_heartbeat.json"
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
        last = datetime.fromisoformat(status["utc"])
        age = now.astimezone(UTC) - last.astimezone(UTC)
        result["scheduler_tick_age_minutes"] = round(age.total_seconds() / 60, 1)
        if age > timedelta(minutes=14) or age < timedelta(minutes=-2):
            result["issues"].append("NO_RECENT_SCHEDULED_TICK")
        if status.get("watchlist") != list(selected):
            result["issues"].append("WATCHLIST_DRIFT")
        if status.get("status") == "FAILED_CLOSED":
            result["issues"].append("LAST_TICK_FAILED_CLOSED")
    except (OSError, ValueError, TypeError, KeyError):
        result["issues"].append("SCHEDULER_HEARTBEAT_MISSING_OR_CORRUPT")
    ny = now.astimezone(NY)
    minute = ny.hour * 60 + ny.minute
    # File evidence is required only AFTER the entire window closes. For the
    # market, allow 40 min after the first post-close collection opportunity.
    windows = session_windows(session)
    close_due = windows["close"][0].hour * 60 + windows["close"][0].minute + 40
    for part, is_due in (
        ("preopen", ny.time() > windows["preopen"][1]),
        ("preclose", ny.time() > windows["preclose"][1]),
        ("close", minute >= close_due),
    ):
        path = saved_path(home, session, part)
        there = path.is_file()
        result["required_snapshot"][part] = {
            "due": bool(is_due and ny.weekday() < 5),
            "present": there,
        }
        if is_due and ny.weekday() < 5 and not there:
            result["issues"].append("MISSING_" + part.upper() + "_SNAPSHOT")
        if there:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict) or doc.get("session") != session:
                    raise ValueError("Wrong or missing session")
                if part == "close":
                    if (
                        doc.get("mode") != "OBSERVE_ONLY"
                        or doc.get("orders") != []
                        or set(doc["ohlc_usd"]) != set(selected) | {"SPY"}
                    ):
                        raise ValueError("Unexpected stock or order evidence")
                else:
                    if doc.get("phase") != part or set(doc["earnings"]) != set(selected):
                        raise ValueError("Unexpected earnings watchlist")
                    result["required_snapshot"][part]["verified_tickers"] = [
                        ticker
                        for ticker in selected
                        if doc["earnings"][ticker].get("verified") is True
                    ]
            except (OSError, ValueError, TypeError, KeyError):
                result["issues"].append("CORRUPT_OR_WRONG_" + part.upper() + "_SNAPSHOT")
    result["status"] = "HEALTHY" if not result["issues"] else "ATTENTION_REQUIRED"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Observation-only runner; task management removed")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--health", action="store_true")
    group.add_argument("--run-once", action="store_true")
    args = parser.parse_args(argv)
    try:
        preflight()
        if args.check:
            print("OBSERVER_SOURCE_CHECK_PASSED; no task, model or account changes")
            return 0
        result = check_health() if args.health else run_tick()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2 if result.get("status") in {"FAILED_CLOSED", "ATTENTION_REQUIRED"} else 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("OBSERVER_STOPPED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
