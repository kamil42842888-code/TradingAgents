"""Recover a prepared paper mark without recomputing trades or calling providers.

The caller holds the account lock. All intended bytes are staged before any
public archive or ledger write. Unknown or changed files stop recovery.
"""

from pathlib import Path, PurePosixPath

from . import daily_support as s


def _relative(root: Path, path: Path) -> str:
    if path.is_symlink():
        raise ValueError("Unsafe transaction path")
    return path.resolve().relative_to(root.resolve()).as_posix()


def _path(root: Path, rel: str) -> Path:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or ":" in rel or "\\" in rel:
        raise ValueError("Invalid paper transaction path")
    result = root / rel
    if result.is_symlink() or not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Redirected paper transaction path")
    return result


def prepare(
    ledger: Path, session: str, before: bytes, after: bytes, artifacts: dict[Path, bytes]
) -> Path:
    directory = ledger.parent / "forward_transactions" / session
    journal = directory / "transaction.json"
    if journal.exists():
        raise ValueError("Existing mark transaction requires recovery first")
    record = {
        "schema": "koral-forward-transaction-v1",
        "session": session,
        "before_sha256": s.sha(before),
        "after_sha256": s.sha(after),
        "files": {},
    }
    s.atomic_bytes(directory / "before.json", before)
    s.atomic_bytes(directory / "after.json", after)
    for path, raw in artifacts.items():
        rel = _relative(ledger.parent, path)
        existing = path.read_bytes() if path.exists() else None
        if existing is not None and existing != raw:
            raise ValueError("Conflicting paper archive: " + rel)
        record["files"][rel] = {"sha256": s.sha(raw), "existed": existing is not None}
        s.atomic_bytes(directory / "files" / rel, raw)
    record["record_sha256"] = s.sha(s.canonical(record))
    s.atomic_json(journal, record)
    return journal


def _load(journal: Path):
    record = s.read_json(journal)
    plain = dict(record)
    digest = plain.pop("record_sha256", None)
    if plain.get("schema") != "koral-forward-transaction-v1" or s.sha(s.canonical(plain)) != digest:
        raise ValueError("Paper transaction integrity mismatch")
    before = (journal.parent / "before.json").read_bytes()
    after = (journal.parent / "after.json").read_bytes()
    if s.sha(before) != record["before_sha256"] or s.sha(after) != record["after_sha256"]:
        raise ValueError("Paper transaction staged account changed")
    files = {}
    for rel, info in record["files"].items():
        raw = _path(journal.parent / "files", rel).read_bytes()
        if s.sha(raw) != info["sha256"]:
            raise ValueError("Paper transaction staged evidence changed")
        files[rel] = raw
    return record, before, after, files


def commit(ledger: Path, journal: Path, *, writer=None):
    from .paper_bridge import _write_once
    from .state import read

    record, before, after, files = _load(journal)
    current = ledger.read_bytes()
    if current not in (before, after):
        raise ValueError("Account changed outside prepared mark; reconcile")
    # Check every existing artifact before making any further writes.
    for rel, raw in files.items():
        path = _path(ledger.parent, rel)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("Conflicting published transaction evidence: " + rel)
    for rel, raw in files.items():
        _write_once(_path(ledger.parent, rel), raw)
    if current == before:
        if writer:
            writer()
        else:
            s.atomic_bytes(ledger, after)
    if ledger.read_bytes() != after:
        raise ValueError("Prepared mark did not write expected account bytes")
    read(ledger)
    s.atomic_json(journal.parent / "complete.json", {"after_sha256": s.sha(after)})


def abort(ledger: Path, journal: Path):
    """Rollback only bytes demonstrably owned by this failed transaction."""
    record, before, after, files = _load(journal)
    if ledger.read_bytes() not in (before, after):
        raise ValueError("Unexpected account mutation; retain transaction for reconciliation")
    if ledger.read_bytes() == after:
        s.atomic_bytes(ledger, before)
    for rel, raw in files.items():
        path = _path(ledger.parent, rel)
        if not record["files"][rel]["existed"] and path.exists():
            if path.read_bytes() != raw:
                raise ValueError("Unexpected evidence mutation; retain transaction")
            path.unlink()
    # Preserve the full journal and staging for diagnosis; a retry uses a fresh ID.
    journal.rename(journal.with_name("aborted-" + s.utcnow().strftime("%H%M%S%f") + ".json"))


def recover_pending(ledger: Path):
    for journal in sorted((ledger.parent / "forward_transactions").glob("*/transaction.json")):
        if not (journal.parent / "complete.json").exists():
            commit(ledger, journal)
