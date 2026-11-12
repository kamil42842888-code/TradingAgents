import importlib.util
from pathlib import Path

import pytest

from tests.test_daily_paper_integration import NAMES, NEXT, account as account, at, run, sources
from tradingagents.paper.autopilot import (
    daily_supervisor,
    daily_support as s,
    daily_transaction as tx,
)
from tradingagents.paper.autopilot.daily_research import BudgetStop, RequestBudget, model_callback
from tradingagents.paper.autopilot.feeds import session_cutoff
from tradingagents.paper.autopilot.state import read

spec = importlib.util.spec_from_file_location(
    "recovery", Path(__file__).parents[1] / "tools/recovery/restore_sources.py"
)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture
def installation(tmp_path):
    project, home = tmp_path / "project", tmp_path / "home"
    (project / ".venv/Scripts").mkdir(parents=True)
    (project / ".venv/Scripts/python.exe").write_bytes(b"fixture")
    ledger = home / ".tradingagents/paper/v3_autopilot/portfolio.json"
    s.atomic_json(
        ledger,
        {
            "schema": "koral-autopaper-v3.1-isolated",
            "paper_only": True,
            "portfolio": {"book": {"positions": {}, "orders": []}, "intents": {}},
            "audit": ["preserve original audit"],
            "auto_paper_enabled": True,
        },
    )
    (project / ".env").write_bytes(b"PRIVATE_TEST_VALUE=preserve\n")
    files = {"controller.py": b'print("source only")\n', "support.py": b"VALUE = 2\n"}
    manifest = {
        "prerequisites": {},
        "known_sha256": {"support.py": [recovery.sha(b"VALUE = 1\n")]},
        "known_git_blobs": {},
    }
    (project / "support.py").write_bytes(b"VALUE = 1\n")
    return project, home, manifest, files, ledger


def test_source_restore_preserves_account_config_env_and_is_idempotent(installation):
    project, home, manifest, files, ledger = installation
    cfg = home / ".tradingagents/paper_operations/daily_v1/config.json"
    s.atomic_json(cfg, {"armed": False, "other": "preserve"})
    protected = {p: p.read_bytes() for p in (cfg, ledger, project / ".env")}
    check = recovery.inspect(project, home, manifest, files)
    backup = recovery.restore(project, check, files)
    assert (backup / "support.py").read_bytes() == b"VALUE = 1\n"
    assert all(p.read_bytes() == raw for p, raw in protected.items())
    assert set(recovery.inspect(project, home, manifest, files)["statuses"].values()) == {"CURRENT"}
    assert not (project / recovery.TRANSACTION).exists()


def test_unknown_source_and_unknown_lock_are_preserved(installation):
    project, home, manifest, files, ledger = installation
    (project / "support.py").write_bytes(b"USER_EDIT = 3\n")
    with pytest.raises(ValueError, match="Unknown local"):
        recovery.inspect(project, home, manifest, files)
    lock = ledger.with_suffix(".json.lock")
    lock.write_bytes(b"")
    with pytest.raises(ValueError, match="lock exists"):
        recovery.inspect(project, home, manifest, files)
    assert lock.exists()
    assert (project / "support.py").read_bytes() == b"USER_EDIT = 3\n"


def test_partial_restore_failure_rolls_back(installation, monkeypatch):
    project, home, manifest, files, ledger = installation
    check = recovery.inspect(project, home, manifest, files)
    original = recovery.atomic_write

    def fail(path, raw):
        if path == project / "support.py" and raw == files["support.py"]:
            raise PermissionError("endpoint block fixture")
        original(path, raw)

    monkeypatch.setattr(recovery, "atomic_write", fail)
    with pytest.raises(PermissionError):
        recovery.restore(project, check, files)
    assert not (project / "controller.py").exists()
    assert (project / "support.py").read_bytes() == b"VALUE = 1\n"


def test_interrupted_source_journal_restores_files_even_without_written_list(
    installation, monkeypatch
):
    project, home, manifest, files, ledger = installation
    check = recovery.inspect(project, home, manifest, files)
    original = recovery.atomic_write

    def simulate_crash(path, raw):
        original(path, raw)
        if path == project / "controller.py":
            # Simulate process termination by preserving journal and replaced file.
            raise SystemExit("interrupted")

    monkeypatch.setattr(recovery, "atomic_write", simulate_crash)
    with pytest.raises(SystemExit):
        recovery.restore(project, check, files)
    if (project / recovery.TRANSACTION).exists():
        monkeypatch.setattr(recovery, "atomic_write", original)
        recovery.rollback(project)
    assert not (project / "controller.py").exists()
    assert (project / "support.py").read_bytes() == b"VALUE = 1\n"


@pytest.mark.parametrize("day", ["2026-11-26", "2026-12-25", "2027-12-24"])
def test_observer_and_controller_reject_exchange_holidays(day):
    with pytest.raises(ValueError):
        session_cutoff(at(day, 18), "close")
    assert daily_supervisor.due_phase(at(day, 18))[1] == "NON_SESSION"


def test_early_close_and_normal_day_use_same_calendar():
    assert session_cutoff(at("2026-11-27", 14, 10), "preclose") == "2026-11-27"
    assert daily_supervisor.due_phase(at("2026-11-27", 14, 40))[1] == "CLOSE_PROCESSING"
    with pytest.raises(ValueError):
        session_cutoff(at("2026-11-27", 13, 30), "close")
    assert daily_supervisor.due_phase(at("2026-11-30", 14, 40))[1] == "WAITING_FOR_OBSERVER"


def test_real_langchain_callback_prevents_second_provider_invocation(tmp_path):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    guard = RequestBudget(
        tmp_path / "budget.json", dict(s.DEFAULTS, max_model_requests_per_session=1)
    )
    model = FakeListChatModel(responses=["mock"], callbacks=[model_callback(guard)])
    assert model.invoke("first").content == "mock"
    with pytest.raises(BudgetStop):
        model.invoke("second")
    assert guard.summary()["model_requests"] == 1


def test_budget_overrun_remains_stopped_after_restart(tmp_path):
    path = tmp_path / "budget.json"
    guard = RequestBudget(path, s.DEFAULTS)
    guard.reserve("a", 1)
    guard.finish("a", 999999)
    with pytest.raises(BudgetStop):
        RequestBudget(path, s.DEFAULTS).reserve("b", 1)


def test_mark_prepared_crash_can_finish_once_without_research(account, monkeypatch):
    ledger, observer = account
    sources(observer, NEXT)
    real_commit = tx.commit

    def crash(ledger, journal, **kwargs):
        _, _, _, files = tx._load(journal)
        rel, raw = next(iter(files.items()))
        s.atomic_bytes(ledger.parent / rel, raw)
        raise SystemExit("power failure")

    monkeypatch.setattr(tx, "commit", crash)
    # A killed process cannot enter abort/finally; preserve its durable journal.
    monkeypatch.setattr(tx, "abort", lambda *args: None)
    with pytest.raises(SystemExit):
        run(ledger, observer, NEXT, budget=0)
    monkeypatch.setattr(tx, "commit", real_commit)
    result = run(ledger, observer, NEXT, budget=0)
    assert result["mark"] == "ALREADY_MARKED"
    assert read(ledger).portfolio.book.last_session == NEXT
    assert sum(e["kind"] == "OBSERVER_FORWARD_SESSION_PROCESSED" for e in read(ledger).audit) == 1
    before = ledger.read_bytes()
    run(ledger, observer, NEXT, budget=0)
    assert ledger.read_bytes() == before


def test_ai_total_failure_does_not_prevent_mark_or_duplicate_attempts(account):
    ledger, observer = account
    sources(observer, NEXT)
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs["ticker"])
        raise RuntimeError("mock provider unavailable")

    run(ledger, observer, NEXT, research_fn=fail)
    assert read(ledger).portfolio.book.last_session == NEXT
    assert calls == NAMES[:3]
    run(ledger, observer, NEXT, research_fn=fail)
    assert calls == NAMES[:3]


def test_deleted_budget_after_interrupted_attempt_blocks_new_spend(account):
    ledger, observer = account
    sources(observer, NEXT)

    def crash(*args, **kwargs):
        raise KeyboardInterrupt("lost provider response")

    with pytest.raises(KeyboardInterrupt):
        run(ledger, observer, NEXT, research_fn=crash)
    (ledger.parent / "model_budgets" / (NEXT + ".json")).unlink()
    with pytest.raises(BudgetStop, match="ledger missing"):
        run(ledger, observer, NEXT, research_fn=lambda *a, **k: pytest.fail("new paid request"))


def test_missing_legacy_roster_never_uses_pilot_trio(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "observer_scheduler", Path(__file__).parents[1] / "KE_Observer_Scheduler_2026-10-26.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(ValueError, match="DECLARED_ROSTER_MISSING"):
        mod.watchlist_for_session(NEXT, rosters=tmp_path, queues=tmp_path)
