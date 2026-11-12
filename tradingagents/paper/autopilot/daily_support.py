"""Shared, brokerless runtime support. No model or account imports at module load."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import tempfile
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SCHEMA = "koral-daily-paper-v1"
SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,11}\Z")
DEFAULTS = {
    "schema": SCHEMA,
    "armed": False,
    "managed_observer": False,
    "effective_session": None,
    "max_companies_per_session": 3,
    "max_model_requests_per_session": 60,
    "max_reserved_tokens_per_session": 250000,
    "max_output_tokens_per_request": 2048,
    "max_prompt_bytes_per_request": 100000,
    "rotate_from_latest_queue": True,
    "nightly_scan": True,
    "scan_max_symbols": 600,
    "scan_max_profiles": 80,
    "scan_max_earnings_checks": 80,
    "billing_note": "Request/token admission limits, NOT a guaranteed GBP/USD invoice cap",
}


def home() -> Path:
    return Path.home() / ".tradingagents" / "paper_operations" / "daily_v1"


def utcnow() -> datetime:
    return datetime.now(UTC)


def utc(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return result.astimezone(UTC)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def read_json(path: Path, limit: int = 5_000_000):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or unsafe JSON: " + str(path))
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError("Oversized JSON: " + str(path))
    return json.loads(raw)


def atomic_bytes(path: Path, raw: bytes):
    if path.is_symlink():
        raise ValueError("Refuse symlink: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ke-daily-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def atomic_json(path: Path, value):
    atomic_bytes(path, canonical(value))


def write_once(path: Path, value) -> bool:
    """Fully written immutable publication; never replace earlier declarations."""
    raw = canonical(value)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("Immutable record already exists and differs: " + str(path))
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ke-publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return True


def names(value, cap: int = 8) -> list[str]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= cap
        or any(type(t) is not str or not SYMBOL.fullmatch(t) for t in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("Expected 1-8 distinct canonical stock symbols")
    return list(value)


def calendar() -> list[str]:
    data = read_json(Path(__file__).with_name("daily_sessions.json"))
    sessions = data.get("sessions")
    if (
        data.get("schema") != "koral-xnys-schedule-v1"
        or not isinstance(sessions, list)
        or sessions != sorted(set(sessions))
        or not sessions
    ):
        raise ValueError("Pinned session calendar malformed")
    return sessions


def is_session(session: str) -> bool:
    days = calendar()
    if date.fromisoformat(session).isoformat() != session:
        raise ValueError("Noncanonical session date")
    if not "2026-01-01" <= session <= "2027-12-31":
        raise ValueError("Session calendar coverage ends 2027-12-31; update required")
    return session in days


def next_session(session: str) -> str:
    is_session(session)  # validate coverage, including non-trading dates
    for item in calendar():
        if item > session:
            return item
    raise ValueError("No next session in pinned calendar")


def session_windows(session: str) -> dict:
    """Keep the established post-close data delay on normal and shortened days.

    The legacy archive name 'preclose' means the earnings gate preceding the
    controller's daily mark, not a claim that collection preceded exchange close.
    """
    if not is_session(session):
        raise ValueError("Not an XNYS session")
    data = read_json(Path(__file__).with_name("daily_sessions.json"))
    close = time.fromisoformat(data.get("early_closes", {}).get(session, "16:00"))
    end = datetime.combine(date.fromisoformat(session), close, tzinfo=NY)
    return {
        "preopen": (time(8, 45), time(9, 25)),
        "preclose": ((end + timedelta(minutes=60)).time(), (end + timedelta(minutes=85)).time()),
        "close": ((end + timedelta(minutes=100)).time(), time(23, 59, 59)),
    }


def between(previous: str, current: str) -> list[str]:
    if not is_session(current):
        raise ValueError("Current date is not a scheduled XNYS session")
    return [day for day in calendar() if previous < day < current]


def validate_config(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) != set(DEFAULTS) or data.get("schema") != SCHEMA:
        raise ValueError("Daily-controller configuration schema mismatch")
    if data["effective_session"] is not None and not is_session(data["effective_session"]):
        raise ValueError("Effective session must be within pinned XNYS calendar")
    for key in ("armed", "managed_observer", "rotate_from_latest_queue", "nightly_scan"):
        if type(data[key]) is not bool:
            raise ValueError("Invalid boolean control: " + key)
    for key, low, high in (
        ("max_companies_per_session", 0, 3),
        ("max_model_requests_per_session", 1, 60),
        ("max_reserved_tokens_per_session", 1000, 250000),
        ("max_output_tokens_per_request", 256, 2048),
        ("max_prompt_bytes_per_request", 1000, 100000),
        ("scan_max_symbols", 1, 600),
        ("scan_max_profiles", 1, 80),
        ("scan_max_earnings_checks", 1, 80),
    ):
        if type(data[key]) is not int or not low <= data[key] <= high:
            raise ValueError("Invalid bounded setting: " + key)
    if data["billing_note"] != DEFAULTS["billing_note"]:
        raise ValueError("Billing limits must not claim a monetary guarantee")
    return dict(data)


def load_config(root: Path | None = None) -> dict:
    path = (root or home()) / "config.json"
    return validate_config(read_json(path)) if path.exists() else dict(DEFAULTS)


def active_symbols(state) -> set[str]:
    book = state.portfolio.book
    return (
        set(book.positions)
        | {o["ticker"] for o in book.orders}
        | {
            r["plan"]["ticker"]
            for r in state.portfolio.intents.values()
            if r["status"] in {"WATCHING", "QUEUED"}
        }
    )


@contextmanager
def os_lock(path: Path):
    """OS lock releases after an unclean process exit; keep the inode on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Unsafe lock symlink")
    with path.open("a+b") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another daily-controller process holds this lock") from exc
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def process_alive(pid: int) -> bool:
    """Access denied / ambiguity means alive; never take another owner's lock."""
    if type(pid) is not int or pid < 1:
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            code = wintypes.DWORD()
            try:
                ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                return not ok or code.value == 259  # STILL_ACTIVE; ambiguity stays locked
            finally:
                kernel.CloseHandle(handle)
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: PID gone
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def ledger_lock(ledger: Path):
    """Coordinate with legacy CLI .lock; recover ONLY this runtime's dead leases."""
    path = ledger.with_suffix(ledger.suffix + ".lock")
    lease = {
        "schema": "koral-daily-ledger-lease-v1",
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "ledger": str(ledger.resolve()),
    }
    with os_lock(ledger.parent / ".daily-controller.lock"):
        if path.exists():
            try:
                old = read_json(path, 4096)
            except Exception as exc:
                raise RuntimeError(
                    "Legacy/unknown ledger lock exists; do not delete blindly"
                ) from exc
            if (
                old.get("schema") != lease["schema"]
                or old.get("host") != lease["host"]
                or old.get("ledger") != lease["ledger"]
                or process_alive(old.get("pid"))
            ):
                raise RuntimeError("Existing ledger lock is live or not demonstrably ours")
            recovery = ledger.parent / "_daily_lock_recovery"
            recovery.mkdir(exist_ok=True)
            os.replace(path, recovery / (utcnow().strftime("%Y%m%dT%H%M%S%f") + ".json"))
        try:
            with path.open("xb") as f:
                f.write(canonical(lease))
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError as exc:
            raise RuntimeError("Another paper command acquired the account lock") from exc
        try:
            yield
        finally:
            if path.is_file() and not path.is_symlink() and path.read_bytes() == canonical(lease):
                path.unlink()
