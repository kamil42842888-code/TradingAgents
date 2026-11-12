import pytest

from tests.test_daily_paper_integration import (
    NEXT,
    account as account,
    at,
    fake_research,
    run,
    sources,
)
from tradingagents.paper.autopilot import (
    daily_roster as roster,
    daily_supervisor as sup,
    daily_support as s,
)
from tradingagents.paper.autopilot.state import read


def configure(tmp_path, ledger, day=NEXT):
    ops = tmp_path / "ops"
    s.atomic_json(
        ops / "config.json",
        dict(
            s.DEFAULTS,
            armed=True,
            managed_observer=True,
            effective_session=day,
            nightly_scan=False,
            rotate_from_latest_queue=False,
            max_companies_per_session=0,
        ),
    )
    roster.freeze(ledger, day, at(day, 8), root=ops)
    return ops


def test_repeated_supervisor_polls_mark_once_and_preserve_summary(account, tmp_path, monkeypatch):
    ledger, observer = account
    ops = configure(tmp_path, ledger)
    sources(observer, NEXT)
    monkeypatch.setattr(s, "utcnow", lambda: at(NEXT, 18))
    for _ in range(3):
        result = sup.tick(
            tmp_path,
            root=ops,
            ledger=ledger,
            observer=observer,
            now=at(NEXT, 18),
            verify_sources=False,
        )
        assert result["status"] == "FORWARD_SESSION_PROCESSED"
    assert sum(e["kind"] == "OBSERVER_FORWARD_SESSION_PROCESSED" for e in read(ledger).audit) == 1
    summary = s.read_json(ops / "summaries" / (NEXT + ".json"))
    assert summary["cash_gbp"] == 10000 and summary["data_complete"]
    assert summary["conditional_plans"] == {} and summary["orders"] == []
    assert (ops / "rosters/2026-11-04.json").is_file()


def test_missing_source_has_operational_summary_without_account_writes(account, tmp_path):
    ledger, observer = account
    ops = configure(tmp_path, ledger)
    original = ledger.read_bytes()
    result = sup.tick(
        tmp_path, root=ops, ledger=ledger, observer=observer, now=at(NEXT, 18), verify_sources=False
    )
    assert result["status"] == "WAITING_FOR_REQUIRED_OBSERVATIONS"
    summary = s.read_json(ops / "summaries" / (NEXT + ".json"))
    assert len(summary["missing_sources"]) == 3 and not summary["data_complete"]
    assert ledger.read_bytes() == original


def test_active_gap_halts_and_records_error_summary(account, tmp_path):
    ledger, observer = account
    sources(observer, NEXT)
    run(
        ledger,
        observer,
        NEXT,
        budget=1,
        research_fn=fake_research({"TWST": "Buy"}, [], positive=True),
    )
    ops = configure(tmp_path, ledger, "2026-11-05")
    original = ledger.read_bytes()
    with pytest.raises(ValueError, match="active exposure"):
        sup.tick(
            tmp_path,
            root=ops,
            ledger=ledger,
            observer=observer,
            now=at("2026-11-05", 8),
            verify_sources=False,
        )
    assert ledger.read_bytes() == original
    assert (
        s.read_json(ops / "summaries/2026-11-05_error.json")["status"]
        == "DAILY_CONTROLLER_FAILED_CLOSED"
    )


def test_empty_integrity_manifest_is_rejected(tmp_path):
    s.atomic_json(
        tmp_path / "KE_DAILY_SOURCE_MANIFEST.json",
        {"schema": "koral-daily-installed-sources-v1", "sha256": {}},
    )
    with pytest.raises(ValueError, match="Incomplete"):
        sup.check_integrity(tmp_path)


def test_incomplete_install_blocks_runtime_before_importing_account(tmp_path):
    (tmp_path / ".koral-source-transaction.json").write_text("{}")
    with pytest.raises(ValueError, match="unfinished"):
        sup.check_integrity(tmp_path)
