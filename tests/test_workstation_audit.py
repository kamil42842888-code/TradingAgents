"""Independent audit boundaries: untrusted installations remain inert data."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tools/audit/audit_workstation.py"
spec = importlib.util.spec_from_file_location("ke_workstation_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
NOW = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def source_fixture(tmp_path):
    project = tmp_path / "target"
    project.mkdir()
    before = {"KE_DAILY_PAPER.py": b"OLD = True\n", "tradingagents/core.py": b"CORE = 1\n"}
    after = {**before, "KE_DAILY_PAPER.py": b"NEW = True\n", "tradingagents/health.py": b"HEALTH = 1\n"}
    reference = {"versions": {
        "recovery_2026-11-12": {"commit": "1" * 40, "sha256": {k: digest(v) for k, v in before.items()}},
        "continuation_2026-11-19": {"commit": "2" * 40, "sha256": {k: digest(v) for k, v in after.items()}},
    }}
    return project, before, after, reference


def install_sources(project, files, *, manifest=True):
    for rel, raw in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if manifest:
        write(project / "KE_DAILY_SOURCE_MANIFEST.json", {
            "schema": "koral-daily-installed-sources-v1",
            "sha256": {k: digest(v) for k, v in files.items()},
        })


@pytest.mark.parametrize("version", ["recovery_2026-11-12", "continuation_2026-11-19"])
def test_source_versions_distinguished_and_read_without_execution(source_fixture, version):
    project, before, after, reference = source_fixture
    install_sources(project, before if version.startswith("recovery") else after)
    reader = audit.Snapshot({"source": project})
    report = audit.source_report(project, reader, reference)
    assert report["matched_version"] == version
    assert report["installed_manifest"] == "CONSISTENT"
    assert report["target_code_executed"] is False
    assert reader.verify()["status"] == "STABLE"


def test_matching_git_source_without_manifest_is_not_installed_readiness(source_fixture):
    project, _, after, reference = source_fixture
    install_sources(project, after, manifest=False)
    (project / ".git").write_text("gitdir: arbitrary-target-never-opened", encoding="utf-8")
    report = audit.source_report(project, audit.Snapshot({"source": project}), reference)
    assert report["matched_version"] == "continuation_2026-11-19"
    assert report["installed_manifest"] == "MISSING"
    assert report["git_checkout_marker_present"] is True


def test_forged_self_manifest_cannot_establish_reviewed_source(source_fixture):
    project, _, after, reference = source_fixture
    edited = {**after, "tradingagents/core.py": b"UNREVIEWED = True\n"}
    install_sources(project, edited)
    report = audit.source_report(project, audit.Snapshot({"source": project}), reference)
    assert report["matched_version"] is None
    assert report["installed_manifest"] == "CONSISTENT"
    assert report["different_from_continuation"] == ["tradingagents/core.py"]


def test_unlisted_nested_application_module_invalidates_source_match(source_fixture):
    project, _, after, reference = source_fixture
    install_sources(project, after)
    rogue = project / "tradingagents/new/nested/module.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_bytes(b"UNKNOWN = True\n")
    report = audit.source_report(project, audit.Snapshot({"source": project}), reference)
    assert report["matched_version"] is None
    assert report["additional_application_modules"] == 1


def test_manifest_cannot_request_private_file_reads(source_fixture, monkeypatch):
    project, _, after, reference = source_fixture
    install_sources(project, after)
    write(project / "KE_DAILY_SOURCE_MANIFEST.json", {
        "schema": "koral-daily-installed-sources-v1", "sha256": {".env": "0" * 64},
    })
    reader = audit.Snapshot({"source": project})
    original = reader.read

    def read(path, *args, **kwargs):
        assert path.name != ".env", "private environment was read"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(reader, "read", read)
    report = audit.source_report(project, reader, reference)
    assert report["installed_manifest"] == "INVALID_OR_UNREADABLE"


def task_document():
    return {
        "schema": "koral-windows-task-status-v1", "read_only": True,
        "checked_utc": NOW.isoformat(), "status": "TASK_CONFIGURATION_MATCHES",
        "account_writes": 0, "model_calls": 0,
        "tasks": [{
            "name": name, "present": True, "state": "Ready", "findings": [],
            "action_matches_project": True, "principal_matches_user": True,
            "wake_to_run": True, "trigger_interval_seconds": 300,
            "multiple_instances": "IgnoreNew", "runtime": {"last_task_result": 0},
            "Arguments": "SECRET_ARGUMENT", "UserId": "SECRET_USER",
        } for name in audit.TASK_NAMES],
    }


def test_task_report_filters_private_fields_and_requires_real_rows(tmp_path):
    path = tmp_path / "tasks.json"
    doc = task_document()
    write(path, doc)
    result = audit.task_report(path, audit.Snapshot({"task": tmp_path}), NOW)
    assert result["status"] == "TASK_CONFIGURATION_MATCHES"
    assert "SECRET_" not in json.dumps(result)
    doc["tasks"] = []
    write(path, doc)
    result = audit.task_report(path, audit.Snapshot({"task": tmp_path}), NOW)
    assert result["status"] == "INVALID_OR_UNREADABLE_INSPECTION"


def test_task_header_cannot_hide_action_mismatch(tmp_path):
    path = tmp_path / "tasks.json"
    doc = task_document()
    doc["tasks"][0]["action_matches_project"] = False
    write(path, doc)
    result = audit.task_report(path, audit.Snapshot({"task": tmp_path}), NOW)
    assert result["status"] == "ATTENTION_REQUIRED"
    assert "TASK_ACTION_MISMATCH" in result["tasks"][0]["findings"]


def test_missing_task_presence_is_reported_without_aborting_other_diagnostics(tmp_path):
    path = tmp_path / "tasks.json"
    doc = task_document()
    del doc["tasks"][0]["present"]
    write(path, doc)
    result = audit.task_report(path, audit.Snapshot({"task": tmp_path}), NOW)
    assert result["status"] == "INVALID_OR_UNREADABLE_INSPECTION"


@pytest.mark.parametrize("checked", ["2026-11-19T15:01:00+00:00", "2026-11-19T14:45:59+00:00"])
def test_task_inspection_clock_is_bounded(tmp_path, checked):
    path = tmp_path / "tasks.json"
    doc = task_document()
    doc["checked_utc"] = checked
    write(path, doc)
    result = audit.task_report(path, audit.Snapshot({"task": tmp_path}), NOW)
    assert result["status"] == "STALE_OR_FUTURE_INSPECTION"


def test_cli_runs_without_site_packages_or_target_imports_and_preserves_inputs(tmp_path):
    project, data, hostile = (tmp_path / part for part in ("project", "private", "hostile"))
    project.mkdir()
    data.mkdir()
    hostile.mkdir()
    marker = tmp_path / "executed.txt"
    poison = f"from pathlib import Path\nPath({str(marker)!r}).write_text('EXECUTED')\n"
    install_sources(project, {"tradingagents/__init__.py": poison.encode()}, manifest=False)
    (project / ".env").write_text("KEY=SECRET_ENVIRONMENT", encoding="utf-8")
    (hostile / "sitecustomize.py").write_text(poison, encoding="utf-8")
    write(data / "paper/v3_autopilot/portfolio.json", {"balance": "SECRET_BALANCE"})
    before = {p: p.read_bytes() for root in (project, data) for p in root.rglob("*") if p.is_file()}
    output = tmp_path / "report"
    result = subprocess.run([
        sys.executable, "-I", "-S", "-B", str(SCRIPT), "--project", str(project),
        "--data-root", str(data), "--output-dir", str(output), "--as-of", NOW.isoformat(),
    ], env={**os.environ, "PYTHONPATH": str(hostile)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2, result.stderr
    report = json.loads((output / "audit.json").read_text())
    assert report["status"] == "ATTENTION_REQUIRED"
    assert report["snapshot"]["status"] == "STABLE"
    assert report["account_writes"] == report["model_calls"] == 0
    assert "SECRET_" not in json.dumps(report) + result.stdout + result.stderr
    assert not marker.exists()
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not list(project.rglob("__pycache__"))


def test_output_cannot_overlap_inputs_or_overwrite_existing_report(tmp_path):
    protected = tmp_path / "input"
    protected.mkdir()
    with pytest.raises(ValueError, match="OVERLAPS"):
        audit._output_directory(protected / "output", [protected])
    output = tmp_path / "output"
    output.mkdir()
    sentinel = output / "audit.json"
    sentinel.write_text("original", encoding="utf-8")
    with pytest.raises(ValueError, match="ALREADY_USED"):
        audit._output_directory(output, [protected])
    assert sentinel.read_text() == "original"


def test_utc_calendar_includes_dst_and_half_day_cutoffs_without_runtime_tzdata():
    calendar = json.loads((SCRIPT.parent / "calendar_utc.json").read_text())
    days = calendar["utc_days"]
    assert days["2026-03-06"]["preopen_start"] == "2026-03-06T13:45:00+00:00"
    assert days["2026-03-09"]["preopen_start"] == "2026-03-09T12:45:00+00:00"
    assert days["2026-11-19"]["processing_start"] == "2026-11-19T22:40:00+00:00"
    assert days["2026-11-27"]["processing_start"] == "2026-11-27T19:40:00+00:00"
    assert days["2026-11-27"]["due"] == "2026-11-27T20:20:00+00:00"
    source = (ROOT / "tradingagents/paper/autopilot/daily_sessions.json").read_bytes()
    assert calendar["source_sha256"] == digest(source.replace(b"\r\n", b"\n"))


def test_reviewed_runtime_inventory_matches_the_continuation_sources():
    reference = json.loads((SCRIPT.parent / "source_reference.json").read_text())
    current = reference["versions"]["continuation_2026-11-19"]
    assert current["commit"] == "ee858675b50e8bf3ada00bd2bb7a80820a3cf028"
    for rel, expected in current["sha256"].items():
        assert digest((ROOT / rel).read_bytes().replace(b"\r\n", b"\n")) == expected, rel
