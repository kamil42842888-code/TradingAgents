"""Source failures remain visible without blocking read-only diagnostics."""

import importlib.util
import json
from pathlib import Path

import pytest

from tradingagents.paper.autopilot import daily_supervisor as supervisor


def cli():
    spec = importlib.util.spec_from_file_location(
        "ke_daily_status_test", Path(__file__).parents[1] / "KE_DAILY_PAPER.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_status_reports_integrity_failure_without_running_controller(monkeypatch, capsys, tmp_path):
    def fail_integrity(project):
        raise ValueError("Missing installed manifest fixture")

    monkeypatch.setattr(supervisor, "check_integrity", fail_integrity)
    monkeypatch.setattr(supervisor, "tick", lambda *a, **k: pytest.fail("status executed tick"))
    module = cli()
    code = module.main(
        [
            "--status",
            "--operations-root",
            str(tmp_path / "missing"),
            "--as-of",
            "2026-11-19T15:00:00+00:00",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["source_integrity"]["status"] == "FAILED"
    assert report["scheduler_status"] == "NOT_INSPECTED_BY_THIS_COMMAND"
    assert report["account_changes"] == 0
    assert not (tmp_path / "missing").exists()


def test_mutating_mode_still_stops_at_integrity_failure(monkeypatch, capsys):
    def fail_integrity(project):
        raise ValueError("Source mismatch fixture")

    monkeypatch.setattr(supervisor, "check_integrity", fail_integrity)
    monkeypatch.setattr(supervisor, "tick", lambda *a, **k: pytest.fail("tick bypassed gate"))
    assert cli().main(["--tick"]) == 2
    report = json.loads(capsys.readouterr().err)
    assert report["status"] == "MANUAL_RECOVERY_SAFE_HALT"


@pytest.mark.parametrize(
    "manifest", [[], {"schema": "koral-daily-installed-sources-v1", "sha256": []}]
)
def test_status_preserves_diagnostics_for_structurally_invalid_manifest(
    tmp_path, capsys, monkeypatch, manifest
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "KE_DAILY_SOURCE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    module = cli()
    monkeypatch.setattr(module, "PROJECT", project)
    assert module.main(["--status", "--operations-root", str(tmp_path / "ops")]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["source_integrity"]["status"] == "FAILED"
    assert report["operational_health"]["configuration_file"] == "MISSING"
    assert not (tmp_path / "ops").exists()


@pytest.mark.parametrize(
    "option", [["--as-of", "2026-11-19T15:00:00+00:00"], ["--operations-root", "somewhere"]]
)
def test_status_overrides_cannot_change_execution_scope(option):
    with pytest.raises(SystemExit) as exc:
        cli().main(["--tick", *option])
    assert exc.value.code == 2


def test_status_rejects_naive_inspection_timestamp(monkeypatch, capsys):
    monkeypatch.setattr(supervisor, "check_integrity", lambda project: None)
    assert cli().main(["--status", "--as-of", "2026-11-19T15:00:00"]) == 2
    assert "Timezone-aware" in json.loads(capsys.readouterr().err)["message"]
