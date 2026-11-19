"""Read-only audit input must be bounded, strict, rooted and stable."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "tools/audit/snapshot.py"
spec = importlib.util.spec_from_file_location("audit_snapshot_tests", SOURCE)
snapshot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = snapshot
spec.loader.exec_module(snapshot)
Snapshot, AuditReadError = snapshot.Snapshot, snapshot.AuditReadError


def test_stable_reads_preserve_files_and_expose_only_counts(tmp_path):
    source = tmp_path / "private-name.json"
    source.write_text('{"setting": "private-value"}', encoding="utf-8")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    view = Snapshot({"data": tmp_path})
    assert view.json(source) == {"setting": "private-value"}
    assert view.exists(source)
    assert view.glob(tmp_path, "*.json") == [source]
    result = view.verify()
    assert result == {
        "status": "STABLE", "files_read": 1, "bytes_read": len(source.read_bytes()),
        "changed_count": 0, "changed_categories": [],
    }
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert "private" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"x":{"a":1,"a":2}}'])
def test_duplicate_json_keys_are_rejected(tmp_path, raw):
    path = tmp_path / "record.json"
    path.write_bytes(raw)
    with pytest.raises(AuditReadError, match="DUPLICATE_JSON_KEY"):
        Snapshot({"data": tmp_path}).json(path)


@pytest.mark.parametrize("raw", [b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}'])
def test_nonfinite_json_is_rejected(tmp_path, raw):
    path = tmp_path / "record.json"
    path.write_bytes(raw)
    with pytest.raises(AuditReadError, match="NONFINITE_JSON_NUMBER"):
        Snapshot({"data": tmp_path}).json(path)


def test_bom_json_and_malformed_encoding(tmp_path):
    path = tmp_path / "record.json"
    path.write_bytes(b'\xef\xbb\xbf{"ok": true}')
    assert Snapshot({"data": tmp_path}).json(path) == {"ok": True}
    path.write_bytes(b'\xff{"x":true}')
    with pytest.raises(AuditReadError, match="INVALID_JSON"):
        Snapshot({"data": tmp_path}).json(path)


def test_oversized_json_is_not_read_and_limit_is_explicit(tmp_path):
    path = tmp_path / "record.json"
    path.write_bytes(b'{"payload":"' + b"x" * 100 + b'"}')
    view = Snapshot({"data": tmp_path})
    with pytest.raises(AuditReadError, match="FILE_SIZE_LIMIT_EXCEEDED"):
        view.json(path, limit=20)
    assert view.verify()["files_read"] == 0
    with pytest.raises(AuditReadError, match="INVALID_READ_LIMIT"):
        view.read(path, limit=True)


def test_changed_content_is_detected_even_after_mtime_restored(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_bytes(b'{"v":1}')
    original = path.stat()
    view = Snapshot({"data": tmp_path})
    view.read(path)
    path.write_bytes(b'{"v":2}')
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    result = view.verify()
    assert result["status"] == "CONCURRENT_CHANGE_RETRY"
    assert result["changed_categories"] == ["data"]


def test_second_read_cannot_replace_first_snapshot(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_bytes(b"one")
    view = Snapshot({"data": tmp_path})
    view.read(path)
    path.write_bytes(b"two")
    with pytest.raises(AuditReadError, match="CONCURRENT_CHANGE"):
        view.read(path)
    assert view.verify()["status"] == "CONCURRENT_CHANGE_RETRY"


@pytest.mark.parametrize("method", ["exists", "read"])
def test_absent_path_appearing_after_inspection_is_a_change(tmp_path, method):
    path = tmp_path / "missing/sub/record.json"
    view = Snapshot({"data": tmp_path})
    if method == "exists":
        assert not view.exists(path)
    else:
        with pytest.raises(AuditReadError, match="MISSING_FILE"):
            view.read(path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{}")
    assert view.verify()["status"] == "CONCURRENT_CHANGE_RETRY"


@pytest.mark.parametrize("change", ["addition", "removal"])
def test_inventory_changes_are_detected(tmp_path, change):
    directory = tmp_path / "summaries"
    directory.mkdir()
    first = directory / "first.json"
    first.write_bytes(b"{}")
    view = Snapshot({"data": tmp_path})
    assert view.glob(directory, "*.json") == [first]
    if change == "addition":
        (directory / "second.json").write_bytes(b"{}")
    else:
        first.unlink()
    assert view.verify()["status"] == "CONCURRENT_CHANGE_RETRY"


def test_absent_glob_directory_is_tracked(tmp_path):
    directory = tmp_path / "summaries"
    view = Snapshot({"data": tmp_path})
    assert view.glob(directory, "*.json") == []
    directory.mkdir()
    assert view.verify()["status"] == "CONCURRENT_CHANGE_RETRY"


def test_known_nested_pattern_is_bounded_and_checked(tmp_path):
    child = tmp_path / "day/transaction.json"
    child.parent.mkdir()
    child.write_bytes(b"{}")
    view = Snapshot({"data": tmp_path})
    assert view.glob(tmp_path, "*/transaction.json") == [child]
    assert view.verify()["status"] == "STABLE"
    for pattern in ["**/*.json", "../*.json", "/etc/*", "*\\*.json", "a/b/c/d/e"]:
        with pytest.raises(AuditReadError, match="UNSAFE_GLOB_PATTERN"):
            view.glob(tmp_path, pattern)


def test_inventory_limit_counts_examined_nonmatches(tmp_path, monkeypatch):
    for i in range(4):
        (tmp_path / f"unmatched-{i}.txt").write_bytes(b"x")
    monkeypatch.setattr(snapshot, "MAX_ENTRIES", 3)
    view = Snapshot({"data": tmp_path})
    with pytest.raises(AuditReadError, match="INVENTORY_LIMIT_EXCEEDED"):
        view.glob(tmp_path, "*.json")


def test_outside_root_and_symlink_escapes_are_rejected(tmp_path):
    root, outside = tmp_path / "allowed", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    private = outside / "private.json"
    private.write_bytes(b"{}")
    view = Snapshot({"data": root})
    with pytest.raises(AuditReadError, match="PATH_OUTSIDE_ALLOWED_ROOTS"):
        view.read(private)
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation requires platform privilege")
    with pytest.raises(AuditReadError, match="REDIRECTED_PATH"):
        view.read(root / "escape/private.json")
    with pytest.raises(AuditReadError, match="REDIRECTED_PATH"):
        Snapshot({"data": root / "escape"})
    with pytest.raises(AuditReadError, match="REDIRECTED_PATH"):
        view.glob(root, "*/private.json")


def test_directory_cannot_be_read_as_file(tmp_path):
    with pytest.raises(AuditReadError, match="NOT_REGULAR_FILE"):
        Snapshot({"data": tmp_path}).read(tmp_path)
