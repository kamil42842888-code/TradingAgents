#!/usr/bin/env python3
"""Restore whitelisted Koral Equities sources. No task management or activation.

Standard library only. Does not import the project, start a subprocess, contact
any service, change permissions/antivirus settings, or write the paper ledger.
Windows scheduled tasks must already be disabled by the operator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

PACKAGE = Path(__file__).resolve().parent
MANIFEST_SHA256 = "BUILD_REPLACES_MANIFEST_SHA256"
TRANSACTION = ".koral-source-transaction.json"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def blob(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def no_redirect(path: Path) -> None:
    """Refuse symlinks/junctions instead of writing through them."""
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("Symlink path refused: " + str(item))
        try:
            if getattr(item.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                raise ValueError("Reparse-point path refused: " + str(item))
        except FileNotFoundError:
            pass


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    no_redirect(path)
    fd, temporary = tempfile.mkstemp(prefix=".ke-source-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def original_bytes(path: Path) -> bytes | None:
    no_redirect(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("Expected a regular file: " + str(path))
    return path.read_bytes()


def payload(package: Path = PACKAGE) -> tuple[dict, dict[str, bytes]]:
    raw = (package / "MANIFEST.json").read_bytes()
    if sha(raw) != MANIFEST_SHA256:
        raise ValueError("Recovery manifest differs from this release")
    manifest = json.loads(raw)
    if manifest.get("schema") != "ke-manual-source-recovery-v1":
        raise ValueError("Unknown recovery release")
    files = {}
    for rel, expected in manifest["files"].items():
        p = PurePosixPath(rel)
        if p.is_absolute() or ".." in p.parts or ":" in rel or "\\" in rel:
            raise ValueError("Unsafe payload path")
        if not rel.endswith((".py", ".json", ".ps1")):
            raise ValueError("Unexpected payload type")
        source = package / "payload" / rel
        no_redirect(source)
        data = source.read_bytes()
        if sha(data) != expected:
            raise ValueError("Payload hash mismatch: " + rel)
        if rel.endswith(".py"):
            compile(data, rel, "exec")  # Syntax check only; no code execution.
        files[rel] = data
    return manifest, files


def inspect(project: Path, home: Path, manifest: dict, files: dict) -> dict:
    no_redirect(project)
    if (project / TRANSACTION).exists():
        raise ValueError("Interrupted source transaction; keep tasks disabled and use --rollback")
    if not project.is_dir() or not (project / ".venv" / "Scripts" / "python.exe").is_file():
        raise ValueError("Existing Windows Koral Equities project/venv not found")
    if (project / ".git").exists():
        raise ValueError("This recovery targets the existing non-Git installation")
    ledger = home / ".tradingagents" / "paper" / "v3_autopilot" / "portfolio.json"
    if ledger.with_suffix(ledger.suffix + ".lock").exists():
        raise ValueError("Account lock exists; stop normally and investigate, do not delete it")
    account = original_bytes(ledger)
    if account is None:
        raise ValueError("Existing account is missing; recovery never creates a new account")
    document = json.loads(account)
    if (
        document.get("schema") != "koral-autopaper-v3.1-isolated"
        or document.get("paper_only") is not True
    ):
        raise ValueError("Unrecognised paper-account schema")
    book = document["portfolio"]["book"]
    active = [
        x
        for x in document["portfolio"]["intents"].values()
        if x.get("status") in {"WATCHING", "QUEUED"}
    ]
    if book["positions"] or book["orders"] or active:
        raise ValueError("Active exposure/plans exist; flat-account recovery refused")
    config = home / ".tradingagents" / "paper_operations" / "daily_v1" / "config.json"
    config_bytes = original_bytes(config)
    if config_bytes is not None and json.loads(config_bytes).get("armed") is not False:
        raise ValueError(
            "Daily research configuration is armed/unknown; do not restore active runtime"
        )
    prerequisite_hashes = {}
    for rel, allowed in manifest["prerequisites"].items():
        data = original_bytes(project / rel)
        if data is None or blob(data.replace(b"\r\n", b"\n")) not in allowed:
            raise ValueError("Core source missing/changed; preserved: " + rel)
        prerequisite_hashes[rel] = sha(data)
    statuses, originals = {}, {}
    for rel, data in files.items():
        before = original_bytes(project / rel)
        originals[rel] = before
        if before is None:
            statuses[rel] = "RESTORE_MISSING"
        elif before == data:
            statuses[rel] = "CURRENT"
        elif sha(before) in manifest["known_sha256"].get(rel, []) or blob(
            before.replace(b"\r\n", b"\n")
        ) in manifest["known_git_blobs"].get(rel, []):
            statuses[rel] = "REPLACE_KNOWN_SOURCE"
        else:
            raise ValueError("Unknown local source revision; not overwritten: " + rel)
    guard = {
        ledger: account,
        project / ".env": original_bytes(project / ".env"),
        config: config_bytes,
    }
    return {
        "statuses": statuses,
        "originals": originals,
        "guard": guard,
        "prerequisite_hashes": prerequisite_hashes,
    }


def unchanged(guard: dict) -> None:
    for path, before in guard.items():
        if original_bytes(path) != before:
            raise RuntimeError("Protected data changed concurrently; NOT overwritten: " + str(path))


@contextmanager
def source_lock(project: Path):
    """A persistent OS lock excludes concurrent updaters and releases on exit."""
    path = project / ".koral-source-update.lock"
    no_redirect(path)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def restore(project: Path, check: dict, files: dict) -> Path:
    with source_lock(project):
        return _restore(project, check, files)


def _restore(project: Path, check: dict, files: dict) -> Path:
    """Source-only transaction; no portfolio/config/scheduler mutation."""
    unchanged(check["guard"])
    if (project / TRANSACTION).exists():
        raise ValueError("Unfinished source transaction; use --rollback")
    backup = (
        project
        / "_Koral_Equities_Source_Backups"
        / (
            "MANUAL_SOURCE_"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            + "_"
            + uuid.uuid4().hex[:8]
        )
    )
    backup.mkdir(parents=True, exist_ok=False)
    target_manifest = "KE_DAILY_SOURCE_MANIFEST.json"
    previous = dict(check["originals"])
    previous[target_manifest] = original_bytes(project / target_manifest)
    hashes = {rel: sha(data) for rel, data in files.items() if not rel.startswith("tests/")}
    hashes.update(check["prerequisite_hashes"])
    new_manifest = (
        json.dumps(
            {"schema": "koral-daily-installed-sources-v1", "sha256": hashes},
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()
    replacements = {rel: data for rel, data in files.items() if previous[rel] != data}
    if previous[target_manifest] != new_manifest:
        replacements[target_manifest] = new_manifest
    for rel in replacements:
        if previous[rel] is not None:
            atomic_write(backup / rel, previous[rel])
    record = {
        "schema": "ke-manual-recovery-backup-v1",
        "project": str(project.absolute()),
        "protected": {
            str(p): sha(raw) if raw is not None else None for p, raw in check["guard"].items()
        },
        "before_sha256": {
            r: sha(previous[r]) if previous[r] is not None else None for r in replacements
        },
        "after_sha256": {r: sha(d) for r, d in replacements.items()},
    }
    record_raw = (json.dumps(record, indent=2) + "\n").encode()
    atomic_write(backup / "RESTORE_RECORD.json", record_raw)
    atomic_write(
        project / TRANSACTION,
        json.dumps(
            {"backup": backup.relative_to(project).as_posix(), "record_sha256": sha(record_raw)}
        ).encode(),
    )
    written = []
    try:
        for rel, data in replacements.items():
            dest = project / rel
            if original_bytes(dest) != previous[rel]:
                raise RuntimeError("Source changed concurrently; preserved: " + rel)
            atomic_write(dest, data)
            written.append(rel)
            if original_bytes(dest) != data:
                raise RuntimeError("Restored source disappeared/changed: " + rel)
        for rel, data in files.items():
            if original_bytes(project / rel) != data:
                raise RuntimeError("Final source verification failed: " + rel)
        unchanged(check["guard"])
        atomic_write(backup / "COMPLETE.json", b'{"status":"SOURCE_RECOVERY_COMPLETE_NOT_ARMED"}\n')
        (project / TRANSACTION).unlink()
    except BaseException:
        print("RECOVERY_FAILED; backup=" + str(backup), file=sys.stderr)
        try:
            _rollback(project)
            print("SOURCE_ROLLBACK_COMPLETED", file=sys.stderr)
        except Exception as exc:
            print("SOURCE_ROLLBACK_INCOMPLETE; transaction retained: " + str(exc), file=sys.stderr)
        raise
    return backup


def rollback(project: Path) -> Path:
    with source_lock(project):
        return _rollback(project)


def _rollback(project: Path) -> Path:
    """Recover even a hard termination between replacement and bookkeeping."""
    marker = project / TRANSACTION
    transaction = json.loads(original_bytes(marker))
    relative = PurePosixPath(transaction["backup"])
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or ":" in str(relative)
        or "\\" in str(relative)
        or relative.parts[0] != "_Koral_Equities_Source_Backups"
    ):
        raise ValueError("Unsafe recovery backup path")
    backup = project / relative
    raw = original_bytes(backup / "RESTORE_RECORD.json")
    if raw is None or sha(raw) != transaction["record_sha256"]:
        raise ValueError("Source rollback record changed")
    record = json.loads(raw)
    if record.get("project") != str(project.absolute()):
        raise ValueError("Source rollback belongs to another project")
    # Account/config changes are never silently undone by source rollback.
    for name, digest in record["protected"].items():
        value = original_bytes(Path(name))
        if (sha(value) if value is not None else None) != digest:
            raise ValueError("Protected data changed; manual reconciliation required: " + name)
    restore = []
    for rel, before_hash in record["before_sha256"].items():
        p = PurePosixPath(rel)
        if p.is_absolute() or ".." in p.parts or ":" in rel or "\\" in rel:
            raise ValueError("Unsafe rollback filename")
        current = original_bytes(project / rel)
        digest = sha(current) if current is not None else None
        if digest == before_hash:
            continue
        if digest != record["after_sha256"][rel]:
            raise ValueError("External source change preserved: " + rel)
        old = original_bytes(backup / rel) if before_hash is not None else None
        if old is not None and sha(old) != before_hash:
            raise ValueError("Source backup changed: " + rel)
        if before_hash is not None and old is None:
            raise ValueError("Source backup missing: " + rel)
        restore.append((rel, old))
    for rel, old in reversed(restore):
        if old is None:
            (project / rel).unlink()
        else:
            atomic_write(project / rel, old)
    marker.unlink()
    return backup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Source-only recovery; no tasks, antivirus changes or trading"
    )
    parser.add_argument("--project", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    parser.add_argument(
        "--tasks-disabled",
        action="store_true",
        help="Confirm both Koral Equities tasks are disabled and its Python commands stopped",
    )
    args = parser.parse_args(argv)
    try:
        if os.name != "nt":
            raise ValueError("Use the existing Windows project virtual environment")
        project = args.project.absolute()
        expected = project / ".venv" / "Scripts" / "python.exe"
        if Path(sys.executable).resolve() != expected.resolve():
            raise ValueError("Use this project's .venv\\Scripts\\python.exe")
        if args.rollback:
            if not args.tasks_disabled:
                raise ValueError("Rollback requires stopped processes and --tasks-disabled")
            backup = rollback(project)
            print("SOURCE_ROLLBACK_COMPLETED; BACKUP=" + str(backup))
            return 0
        manifest, files = payload()
        check = inspect(project, Path.home(), manifest, files)
        for rel, status in check["statuses"].items():
            print(status + " " + rel)
        print("SOURCE_RECOVERY_PREFLIGHT_OK")
        if not args.apply:
            print("CHECK_ONLY; NO_CHANGES")
            return 0
        if not args.tasks_disabled:
            raise ValueError(
                "Disable the two Koral Equities tasks and stop its processes first; then acknowledge --tasks-disabled"
            )
        backup = restore(project, check, files)
        print("SOURCE_RECOVERY_COMPLETE_NOT_ARMED")
        print("BACKUP=" + str(backup))
        print("ACCOUNT_AND_ENV_AND_OPERATION_CONFIG_BYTES_UNCHANGED")
        print("NO_TASKS_CREATED_OR_ENABLED; NO_MODEL_CALLS; NO_PROJECT_CODE_EXECUTED")
        print("KEEP_TASKS_DISABLED: runtime tests and manual task configuration remain pending")
        return 0
    except Exception as exc:
        print("RECOVERY_STOPPED: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
