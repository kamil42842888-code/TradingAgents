"""Bounded, read-only snapshots of explicitly allowed filesystem roots.

No application code is imported. Verification detects concurrent changes; it
does not lock a running installation or claim an atomic filesystem transaction.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_ENTRIES = 5_000
MAX_READ_BYTES = 50_000_000
MAX_SNAPSHOT_BYTES = 128_000_000
_REPARSE_POINT = 0x400


class AuditReadError(ValueError):
    """A static diagnostic code, never a private path or underlying OS message."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _File:
    signature: tuple
    digest: bytes
    size: int
    limit: int


def _absolute(path: Path) -> Path:
    try:
        path = Path(path)
        if ".." in path.parts:
            raise AuditReadError("UNSAFE_PATH")
        return Path(os.path.abspath(path))
    except (TypeError, OSError, ValueError) as exc:
        if isinstance(exc, AuditReadError):
            raise
        raise AuditReadError("INVALID_PATH") from None


def _signature(info, *, directory_contents: bool = False, path_and_handle: bool = False):
    if info is None:
        return None
    identity = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
    if stat.S_ISDIR(info.st_mode) and not directory_contents:
        return identity
    # Windows can return different ctime meanings for a path and an open handle.
    # Keep ctime when comparing like with like, and use creation time across APIs.
    metadata_time = info.st_ctime_ns
    if path_and_handle and os.name == "nt":
        metadata_time = getattr(info, "st_birthtime_ns", None)
    return (*identity, info.st_size, info.st_mtime_ns, metadata_time)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditReadError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _nonfinite(_value):
    raise AuditReadError("NONFINITE_JSON_NUMBER")


class Snapshot:
    """Track stable bytes, existence checks and shallow bounded glob inventories."""

    def __init__(self, roots: dict[str, Path]):
        if not isinstance(roots, dict) or not roots or len(roots) > 32:
            raise AuditReadError("INVALID_ROOTS")
        self.roots = {}
        for category, path in roots.items():
            if not isinstance(category, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", category):
                raise AuditReadError("INVALID_ROOT_CATEGORY")
            self.roots[category] = _absolute(path)
        self._files: dict[Path, _File] = {}
        self._probes: dict[Path, tuple | None] = {}
        self._globs: dict[tuple[Path, str], tuple] = {}
        self._ancestors: dict[Path, tuple] = {}
        self._changed: set[Path] = set()
        self._bytes = 0
        self._inventory_entries = 0
        for path in self.roots.values():
            info = self._stat(path)
            if info is not None and not stat.S_ISDIR(info.st_mode):
                raise AuditReadError("ROOT_NOT_DIRECTORY")
            self._probes[path] = _signature(info)

    def _path(self, path: Path) -> Path:
        path = _absolute(path)
        if not any(path.is_relative_to(root) for root in self.roots.values()):
            raise AuditReadError("PATH_OUTSIDE_ALLOWED_ROOTS")
        return path

    def _stat(self, path: Path):
        """Reject redirects in every existing component, including root ancestors."""
        leaf = None
        for component in reversed((path, *path.parents)):
            try:
                info = component.lstat()
            except FileNotFoundError:
                return None
            except OSError:
                raise AuditReadError("UNREADABLE_PATH") from None
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
                raise AuditReadError("REDIRECTED_PATH")
            if component != path:
                if not stat.S_ISDIR(info.st_mode):
                    raise AuditReadError("ANCESTOR_NOT_DIRECTORY")
                identity = _signature(info)
                previous = self._ancestors.setdefault(component, identity)
                if previous != identity:
                    self._changed.add(path)
                    raise AuditReadError("CONCURRENT_CHANGE")
            leaf = info
        return leaf

    def _probe(self, path: Path, signature):
        if path in self._probes and self._probes[path] != signature:
            self._changed.add(path)
            raise AuditReadError("CONCURRENT_CHANGE")
        if path not in self._probes and len(self._probes) >= MAX_ENTRIES:
            raise AuditReadError("SNAPSHOT_ENTRY_LIMIT_EXCEEDED")
        self._probes[path] = signature

    def exists(self, path: Path) -> bool:
        path = self._path(path)
        info = self._stat(path)
        self._probe(path, _signature(info))
        return info is not None

    def _read(self, path: Path, limit: int):
        before = self._stat(path)
        if before is None:
            self._probe(path, None)
            raise AuditReadError("MISSING_FILE")
        self._probe(path, _signature(before))
        if not stat.S_ISREG(before.st_mode):
            raise AuditReadError("NOT_REGULAR_FILE")
        if before.st_size > limit:
            raise AuditReadError("FILE_SIZE_LIMIT_EXCEEDED")
        signature = _signature(before)
        try:
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if _signature(opened, path_and_handle=True) != _signature(before, path_and_handle=True):
                    self._changed.add(path)
                    raise AuditReadError("CONCURRENT_CHANGE")
                raw = stream.read(limit + 1)
                if _signature(os.fstat(stream.fileno())) != _signature(opened):
                    self._changed.add(path)
                    raise AuditReadError("CONCURRENT_CHANGE")
        except OSError:
            self._changed.add(path)
            raise AuditReadError("FILE_READ_FAILED") from None
        if len(raw) > limit:
            self._changed.add(path)
            raise AuditReadError("FILE_SIZE_LIMIT_EXCEEDED")
        if len(raw) != before.st_size or _signature(self._stat(path)) != signature:
            self._changed.add(path)
            raise AuditReadError("CONCURRENT_CHANGE")
        return raw, signature

    def read(self, path: Path, limit: int = 5_000_000) -> bytes:
        if type(limit) is not int or not 0 < limit <= MAX_READ_BYTES:
            raise AuditReadError("INVALID_READ_LIMIT")
        path = self._path(path)
        raw, signature = self._read(path, limit)
        self._probe(path, signature)
        record = _File(signature, hashlib.sha256(raw).digest(), len(raw), limit)
        previous = self._files.get(path)
        if previous is not None and (
            previous.signature != signature or previous.digest != record.digest
        ):
            self._changed.add(path)
            raise AuditReadError("CONCURRENT_CHANGE")
        if previous is None:
            if len(self._files) >= MAX_ENTRIES:
                raise AuditReadError("SNAPSHOT_ENTRY_LIMIT_EXCEEDED")
            if self._bytes + len(raw) > MAX_SNAPSHOT_BYTES:
                raise AuditReadError("SNAPSHOT_BYTE_LIMIT_EXCEEDED")
            self._bytes += len(raw)
            self._files[path] = record
        return raw

    def json(self, path: Path, limit: int = 5_000_000) -> Any:
        try:
            # utf-8-sig accepts normal Windows UTF-8 BOM output.
            return json.loads(
                self.read(path, limit).decode("utf-8-sig"),
                object_pairs_hook=_strict_object,
                parse_constant=_nonfinite,
                parse_float=self._finite_float,
            )
        except AuditReadError:
            raise
        except (UnicodeError, ValueError, RecursionError):
            raise AuditReadError("INVALID_JSON") from None

    @staticmethod
    def _finite_float(value):
        import math

        result = float(value)
        if not math.isfinite(result):
            raise AuditReadError("NONFINITE_JSON_NUMBER")
        return result

    @staticmethod
    def _pattern(pattern: str) -> tuple[str, ...]:
        if not isinstance(pattern, str) or not pattern or "\\" in pattern or ":" in pattern:
            raise AuditReadError("UNSAFE_GLOB_PATTERN")
        parts = pattern.split("/")
        if len(parts) > 4 or any(part in {"", ".", ".."} or "**" in part for part in parts):
            raise AuditReadError("UNSAFE_GLOB_PATTERN")
        return tuple(parts)

    def _scan(self, directory: Path, parts: tuple[str, ...]):
        pending = [directory]
        inventory, matches, examined = [], [], 0
        for depth, pattern in enumerate(parts):
            following = []
            for folder in pending:
                before = self._stat(folder)
                inventory.append((folder, _signature(before)))
                if before is None:
                    continue
                if not stat.S_ISDIR(before.st_mode):
                    raise AuditReadError("GLOB_ROOT_NOT_DIRECTORY")
                try:
                    with os.scandir(folder) as entries:
                        names = []
                        for entry in entries:
                            examined += 1
                            if examined > MAX_ENTRIES:
                                raise AuditReadError("INVENTORY_LIMIT_EXCEEDED")
                            if fnmatch.fnmatchcase(entry.name, pattern):
                                names.append(entry.name)
                except OSError:
                    raise AuditReadError("DIRECTORY_READ_FAILED") from None
                if _signature(self._stat(folder), directory_contents=True) != _signature(
                    before, directory_contents=True
                ):
                    self._changed.add(directory)
                    raise AuditReadError("CONCURRENT_CHANGE")
                for name in sorted(names):
                    child = folder / name
                    info = self._stat(child)
                    if info is None:
                        self._changed.add(directory)
                        raise AuditReadError("CONCURRENT_CHANGE")
                    inventory.append((child, _signature(info)))
                    if depth == len(parts) - 1:
                        matches.append(child)
                    elif stat.S_ISDIR(info.st_mode):
                        following.append(child)
            pending = following
        return sorted(matches), tuple(inventory)

    def glob(self, directory: Path, pattern: str) -> list[Path]:
        directory = self._path(directory)
        parts = self._pattern(pattern)
        paths, inventory = self._scan(directory, parts)
        key = (directory, pattern)
        if key in self._globs and self._globs[key] != inventory:
            self._changed.add(directory)
            raise AuditReadError("CONCURRENT_CHANGE")
        if key not in self._globs and len(self._globs) >= MAX_ENTRIES:
            raise AuditReadError("SNAPSHOT_ENTRY_LIMIT_EXCEEDED")
        if key not in self._globs:
            if self._inventory_entries + len(inventory) > MAX_ENTRIES:
                raise AuditReadError("SNAPSHOT_ENTRY_LIMIT_EXCEEDED")
            self._inventory_entries += len(inventory)
        self._globs[key] = inventory
        return paths

    def verify(self) -> dict:
        """Re-read consumed evidence; return only counts and caller-defined categories."""
        for path, expected in tuple(self._probes.items()):
            try:
                if _signature(self._stat(path)) != expected:
                    self._changed.add(path)
            except AuditReadError:
                self._changed.add(path)
        for path, expected in tuple(self._files.items()):
            try:
                raw, signature = self._read(path, expected.limit)
                if signature != expected.signature or hashlib.sha256(raw).digest() != expected.digest:
                    self._changed.add(path)
            except AuditReadError:
                self._changed.add(path)
        for (directory, pattern), expected in tuple(self._globs.items()):
            try:
                _, actual = self._scan(directory, self._pattern(pattern))
                if actual != expected:
                    self._changed.add(directory)
            except AuditReadError:
                self._changed.add(directory)
        categories = set()
        for path in self._changed:
            matches = [
                (len(root.parts), name)
                for name, root in self.roots.items()
                if path.is_relative_to(root)
            ]
            if matches:
                categories.add(max(matches)[1])
        return {
            "status": "CONCURRENT_CHANGE_RETRY" if self._changed else "STABLE",
            "files_read": len(self._files),
            "bytes_read": self._bytes,
            "changed_count": len(self._changed),
            "changed_categories": sorted(categories),
        }
