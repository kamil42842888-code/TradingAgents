"""Predeclared daily observer universe; carry all live risk, never fall back silently.

This module makes NO model requests and never edits captured market/earnings
files. The scheduler reads only the sealed declaration. Portfolio adoption is
an explicit audited forward-only operation after same-session evidence validates.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

from . import daily_support as s

SCHEMA = "koral-managed-daily-roster-v1"


def choose_names(current: list[str], active: set[str], ranked: list[str] | None) -> list[str]:
    current = s.names(current)
    if not active.issubset(set(current)):
        # An account that already lost a held name requires reconciliation;
        # never hide this by substituting whatever was in the latest scan.
        raise ValueError("Active paper symbol is missing from account watchlist")
    carry = [t for t in current if t in active]
    proposed = s.names(ranked) if ranked else current
    chosen = list(carry)
    for ticker in proposed:
        if ticker not in chosen:
            chosen.append(ticker)
        if len(chosen) == 8:
            break
    if not active.issubset(chosen):
        raise ValueError("Cannot remove held, pending or WATCHING symbol")
    return s.names(chosen)


def latest_queue(queues: Path, session: str):
    """Use a valid recent source queue; provider failure means carry the account."""
    candidates = sorted(queues.glob("*/queue.json"), reverse=True) if queues.exists() else []
    for file in candidates:
        try:
            day = date.fromisoformat(file.parent.name)
        except ValueError:
            continue
        lag = (date.fromisoformat(session) - day).days
        if not 1 <= lag <= 4:
            continue
        q = s.read_json(file)
        if (
            q.get("schema") != "koral-swing-research-queue-v1"
            or q.get("session") != day.isoformat()
            or q.get("status") != "RESEARCH_QUEUE_ONLY_NO_TRADE_AUTHORITY"
            or q.get("model_calls") != 0
            or q.get("paper_orders") != 0
            or q.get("broker_connected") is not False
        ):
            raise ValueError("Candidate queue is not source-only research")
        ranked = s.names(q.get("next_session_research_watchlist"))
        rows = q.get("candidates")
        if (
            q.get("candidate_count") != len(ranked)
            or not isinstance(rows, list)
            or [r.get("ticker") for r in rows] != ranked
            or any(
                r.get("execution_authority") != "NONE_RESEARCH_ONLY"
                or r.get("earnings_verification") != "NOT_CAPTURED_FOR_NEXT_SESSION"
                for r in rows
            )
        ):
            raise ValueError("Candidate queue does not match its declared symbols")
        generated = s.utc(q.get("generated_utc"))
        cutoff = datetime.combine(date.fromisoformat(session), time(8, 45), tzinfo=s.NY)
        if generated >= cutoff:
            raise ValueError("Scan was not available before next session preopen")
        return ranked, {"path": str(file), "sha256": s.sha(file.read_bytes()), "queue": q}
    return None, None


def seal(record: dict) -> dict:
    result = dict(record)
    result["record_sha256"] = s.sha(s.canonical(record))
    return result


def validate(record: dict, session: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError("Managed roster is not an object")
    plain = dict(record)
    digest = plain.pop("record_sha256", None)
    if s.sha(s.canonical(plain)) != digest:
        raise ValueError("Managed roster integrity mismatch")
    if (
        plain.get("schema") != SCHEMA
        or plain.get("session") != session
        or plain.get("mode") != "OBSERVE_ONLY"
        or plain.get("shorts_allowed") is not False
    ):
        raise ValueError("Managed roster schema/session/authority mismatch")
    s.names(plain.get("watchlist"))
    if not set(plain.get("active_symbols", [])).issubset(plain["watchlist"]):
        raise ValueError("Managed roster drops active exposure")
    cutoff = datetime.combine(date.fromisoformat(session), time(8, 45), tzinfo=s.NY)
    if s.utc(plain.get("declared_utc")) >= cutoff:
        raise ValueError("Managed roster cannot be declared after first observation window")
    proof = plain.get("scan_proof")
    if proof is not None:
        if not isinstance(proof, dict) or not isinstance(proof.get("queue"), dict):
            raise ValueError("Managed roster scan proof malformed")
        # Preserve original bytes separately: not just a digest of a parsed queue.
        raw = bytes.fromhex(proof["raw_hex"])
        if s.sha(raw) != proof["sha256"] or json.loads(raw) != proof["queue"]:
            raise ValueError("Managed roster scan proof changed")
    return record


def observer_roster(session: str, root: Path | None = None):
    root = root or s.home()
    cfg = s.load_config(root)
    if (
        not cfg["managed_observer"]
        or not cfg["effective_session"]
        or session < cfg["effective_session"]
    ):
        return None  # Existing approved legacy declarations continue unchanged.
    if not s.is_session(session):
        # No captures on a scheduled non-session. Return current carried names
        # only for scheduler status; its due-window check still applies.
        records = sorted((root / "rosters").glob("*.json"))
        if records:
            r = s.read_json(records[-1])
            validate(r, r["session"])
            return tuple(r["watchlist"])
        raise ValueError("No managed observation roster declared")
    path = root / "rosters" / (session + ".json")
    if not path.exists():
        raise ValueError("MANAGED_ROSTER_MISSING; refusing silent COST/JPM/WMT fallback")
    return tuple(validate(s.read_json(path), session)["watchlist"])


def freeze(
    ledger: Path,
    session: str,
    now: datetime,
    *,
    root: Path | None = None,
    queues: Path | None = None,
    state=None,
) -> dict:
    root = root or s.home()
    file = root / "rosters" / (session + ".json")
    if file.exists():
        return validate(s.read_json(file), session)
    if not s.is_session(session):
        raise ValueError("Cannot declare roster for a non-session")
    cutoff = datetime.combine(date.fromisoformat(session), time(8, 45), tzinfo=s.NY)
    if s.utc(now) >= cutoff:
        raise ValueError("ROSTER_WINDOW_MISSED; no retrospective declaration")
    if state is None:
        from tradingagents.paper.autopilot.state import read

        st = read(ledger)
    else:
        st = state
    cfg = s.load_config(root)
    ranked = proof = None
    if cfg["rotate_from_latest_queue"]:
        queues = queues or Path.home() / ".tradingagents" / "swing_research" / "runs"
        ranked, proof = latest_queue(queues, session)
        if proof:
            proof["raw_hex"] = Path(proof["path"]).read_bytes().hex()
    active = s.active_symbols(st)
    picked = choose_names(st.watchlist, active, ranked)
    record = seal(
        {
            "schema": SCHEMA,
            "session": session,
            "mode": "OBSERVE_ONLY",
            "declared_utc": s.utc(now).isoformat(),
            "watchlist": picked,
            "active_symbols": sorted(active),
            "previous_watchlist": st.watchlist,
            "selection": "LATEST_SOURCE_QUEUE_PLUS_ACTIVE_RISK"
            if ranked
            else "PERSIST_CURRENT_ACCOUNT_ROSTER",
            "scan_proof": proof,
            "shorts_allowed": False,
        }
    )
    validate(record, session)
    s.write_once(file, record)
    return record


def adopt(ledger: Path, session: str, record: dict, *, root: Path | None = None):
    """Called only after all current source files passed controller validation."""
    from tradingagents.paper.autopilot.state import atomic_write, read

    validate(record, session)
    with s.ledger_lock(ledger):
        st = read(ledger)
        picked = record["watchlist"]
        if not s.active_symbols(st).issubset(picked):
            raise ValueError("Frozen roster no longer covers active account exposure")
        if st.watchlist == picked:
            return {"watchlist": picked, "changed": False}
        original = ledger.read_bytes()
        backup = (
            ledger.parent
            / "_daily_backups"
            / (session + "_roster_" + s.sha(original)[:12] + ".json")
        )
        if not backup.exists():
            s.atomic_bytes(backup, original)
        st.event(
            "FORWARD_ROSTER_ADOPTED",
            date=session,
            previous=st.watchlist,
            current=picked,
            roster_sha256=record["record_sha256"],
            active_symbols=sorted(s.active_symbols(st)),
        )
        st.watchlist = list(picked)
        try:
            atomic_write(ledger, st)
            read(ledger)
        except BaseException:
            s.atomic_bytes(ledger, original)
            raise
        return {"watchlist": picked, "changed": True}
