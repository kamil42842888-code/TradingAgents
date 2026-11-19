"""A transient Windows sharing denial must not lose a durable ledger write."""
import errno
from pathlib import Path

import pytest

from tradingagents.paper.autopilot import state


def denial(code=5):
    error = PermissionError(errno.EACCES, "fixture sharing denial")
    error.winerror = code
    return error


def test_transient_replacement_retries_same_synced_bytes_once_committed(tmp_path, monkeypatch):
    path = tmp_path / "portfolio.json"
    book = state.create_new(path, "2026-11-02", ["TWST"])
    before = path.read_bytes()
    book.set_running(True)
    actual = state.os.replace
    attempts = []
    def replace(source, target):
        attempts.append((str(source), Path(source).read_bytes()))
        assert target == path
        if len(attempts) < 3:
            assert path.read_bytes() == before
            raise denial(32)
        actual(source, target)
    monkeypatch.setattr(state.os, "replace", replace)
    monkeypatch.setattr(state, "_replacement_pause", lambda delay: None, raising=False)
    state.atomic_write(path, book)
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert state.read(path).auto_paper_enabled is True
    assert not list(tmp_path.glob(".v3-*.tmp"))


@pytest.mark.parametrize("error,retries", [(denial(), 6), (PermissionError("real permissions"), 1),
                                          (OSError(errno.ENOSPC, "disk full"), 1)])
def test_persistent_or_unrelated_failure_preserves_old_account(tmp_path, monkeypatch, error, retries):
    path = tmp_path / "portfolio.json"
    book = state.create_new(path, "2026-11-02", ["TWST"])
    before = path.read_bytes()
    attempts = []
    def replace(*args):
        attempts.append(args)
        raise error
    monkeypatch.setattr(state.os, "replace", replace)
    monkeypatch.setattr(state, "_replacement_pause", lambda delay: None, raising=False)
    with pytest.raises(type(error)):
        state.atomic_write(path, book)
    assert len(attempts) == retries
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".v3-*.tmp"))
