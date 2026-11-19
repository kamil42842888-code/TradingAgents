"""Windows-only wrapper checks: metadata mocks, real isolated stdlib subprocess."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

POWERSHELL = shutil.which("powershell") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell required")
WRAPPER = Path(__file__).parents[1] / "tools/audit/Invoke_Koral_ReadOnly_Audit.ps1"
TASK_NAMES = ["KoralEquities_ResearchObserver_5min", "KoralEquities_PaperController_5min"]

MOCK_SCHEDULER = r"""
param([string]$AuditWrapper, [string]$ProjectPath, [string]$DataPath,
      [string]$ReportPath, [string]$AuditPython, [string]$Scenario)
$ErrorActionPreference = 'Stop'
function Register-ScheduledTask { throw 'Mutation forbidden' }
function Set-ScheduledTask { throw 'Mutation forbidden' }
function Enable-ScheduledTask { throw 'Mutation forbidden' }
function Disable-ScheduledTask { throw 'Mutation forbidden' }
function Start-ScheduledTask { throw 'Mutation forbidden' }
function Stop-ScheduledTask { throw 'Mutation forbidden' }
function Unregister-ScheduledTask { throw 'Mutation forbidden' }
function Get-CimInstance { throw 'Process scan forbidden' }
function Get-ScheduledTask {
    [CmdletBinding()] param([string]$TaskPath, [string]$TaskName)
    if ($TaskPath -cne '\') { throw 'Unexpected task path' }
    $definitions = @{
        KoralEquities_ResearchObserver_5min=@('KE_Observer_Scheduler_2026-10-26.py', '--run-once')
        KoralEquities_PaperController_5min=@('KE_DAILY_PAPER.py', '--tick')
    }
    if (-not $definitions.ContainsKey($TaskName)) { throw 'Unexpected task inventory' }
    if ($Scenario -eq 'unavailable') { throw 'SECRET_SCHEDULER_EXCEPTION' }
    $definition = $definitions[$TaskName]
    $arguments = '"' + (Join-Path $ProjectPath $definition[0]) + '" ' + $definition[1]
    if ($Scenario -eq 'redacted') { $arguments += ' --api-key SECRET_TASK_ARGUMENT' }
    [pscustomobject]@{
        TaskName=$TaskName; State='Ready'; Description='SECRET_TASK_DESCRIPTION'
        Actions=[pscustomobject]@{
            Execute=(Join-Path $ProjectPath '.venv\Scripts\python.exe')
            Arguments=$arguments; WorkingDirectory=$ProjectPath
        }
        Triggers=[pscustomobject]@{
            Enabled=$true; Repetition=[pscustomobject]@{Interval='PT300S'}
        }
        Principal=[pscustomobject]@{
            UserId=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            LogonType='Interactive'; RunLevel='Limited'
        }
        Settings=[pscustomobject]@{MultipleInstances='IgnoreNew'; WakeToRun=$true}
    }
}
function Get-ScheduledTaskInfo {
    [CmdletBinding()] param($InputObject)
    [pscustomobject]@{
        LastRunTime=[datetime]'2026-11-19T15:00:00'; NextRunTime=[datetime]'2026-11-19T15:05:00'
        LastTaskResult=0; NumberOfMissedRuns=0
    }
}
$parameters = @{
    Project=$ProjectPath; DataRoot=$DataPath; OutputDirectory=$ReportPath
    AsOf='2026-11-19T15:00:00+00:00'
}
if ($AuditPython) { $parameters.Python=$AuditPython }
& $AuditWrapper @parameters
exit $LASTEXITCODE
"""

AUDITOR_STUB = '''\
import argparse
import json
import sys
from pathlib import Path

assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
parser = argparse.ArgumentParser()
for name in ("project", "data-root", "task-status", "output-dir", "as-of"):
    parser.add_argument("--" + name, required=True)
args = parser.parse_args()
assert args.as_of == "2026-11-19T15:00:00+00:00"
tasks = json.loads(Path(args.task_status).read_text(encoding="utf-8"))
assert tasks["read_only"] is True
out = Path(args.output_dir)
with (out / "audit.json").open("x", encoding="utf-8") as stream:
    json.dump({"fixture_isolated_auditor": True, "task_count": len(tasks["tasks"])}, stream)
with (out / "SUMMARY.md").open("x", encoding="utf-8") as stream:
    stream.write("Fixture audit completed without target application imports.\\n")
'''


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def audit_paths(tmp_path):
    review = tmp_path / "review package"
    scripts = review / "tools/audit"
    scripts.mkdir(parents=True)
    wrapper = scripts / WRAPPER.name
    shutil.copyfile(WRAPPER, wrapper)
    (scripts / "audit_workstation.py").write_text(AUDITOR_STUB, encoding="utf-8")
    project = tmp_path / "target project"
    project.mkdir()
    for name in ("KE_DAILY_PAPER.py", "KE_Observer_Scheduler_2026-10-26.py"):
        (project / name).write_text("raise AssertionError('target must never execute')", encoding="utf-8")
    (project / ".env").write_text("PRIVATE_KEY=SECRET_PRIVATE_CONFIG\n", encoding="utf-8")
    data = tmp_path / "private data"
    data.mkdir()
    (data / "portfolio.json").write_text('{"private":"SECRET_ACCOUNT"}', encoding="utf-8")
    mock = tmp_path / "mock_scheduler.ps1"
    mock.write_text(MOCK_SCHEDULER, encoding="utf-8")
    return {"project": project, "data": data, "review": review, "wrapper": wrapper, "mock": mock}


def invoke(paths, output, scenario="healthy", python=sys.executable):
    command = [
        POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(paths["mock"]),
        "-AuditWrapper", str(paths["wrapper"]), "-ProjectPath", str(paths["project"]),
        "-DataPath", str(paths["data"]), "-ReportPath", str(output), "-Scenario", scenario,
    ]
    if python:
        command += ["-AuditPython", python]
    return subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)


@pytest.mark.parametrize("scenario", ["healthy", "redacted", "unavailable"])
def test_launches_bundled_auditor_and_sanitizes_exact_task_inventory(audit_paths, tmp_path, scenario):
    paths = audit_paths
    target_python = paths["project"] / ".venv/Scripts/python.exe"
    target_python.parent.mkdir(parents=True)
    target_python.write_bytes(b"not executable; explicit audit interpreter must be used")
    before = {key: snapshot(paths[key]) for key in ("project", "data", "review")}
    output = tmp_path / "new report"
    result = invoke(paths, output, scenario)
    assert result.returncode == 0, result.stderr
    launch = json.loads(result.stdout)
    assert launch["status"] == "AUDIT_COMPLETED"
    assert launch["target_application_executed"] is False
    tasks = json.loads((output / "tasks.json").read_text(encoding="utf-8"))
    assert [row["name"] for row in tasks["tasks"]] == TASK_NAMES
    if scenario == "healthy":
        assert tasks["status"] == "TASK_CONFIGURATION_MATCHES"
        assert all(row["trigger_interval_seconds"] == 300 for row in tasks["tasks"])
        assert all(row["principal_matches_user"] for row in tasks["tasks"])
    else:
        expected = "TASK_ACTION_MISMATCH" if scenario == "redacted" else "TASK_INSPECTION_UNAVAILABLE"
        assert all(expected in row["findings"] for row in tasks["tasks"])
    assert json.loads((output / "audit.json").read_text())["fixture_isolated_auditor"] is True
    assert (output / "SUMMARY.md").is_file() and (output / "launcher.json").is_file()
    exported_launcher = json.loads((output / "launcher.json").read_text())
    assert "report_directory" not in exported_launcher
    emitted = result.stdout + result.stderr + "".join(p.read_text(encoding="utf-8") for p in output.iterdir())
    assert "SECRET_" not in emitted
    assert all(snapshot(paths[key]) == value for key, value in before.items())


def test_missing_target_python_preserves_task_report_without_running_auditor(audit_paths, tmp_path):
    output = tmp_path / "missing python report"
    result = invoke(audit_paths, output, python=None)
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["status"] == "PYTHON_INTERPRETER_MISSING"
    assert (output / "tasks.json").is_file() and (output / "launcher.json").is_file()
    assert not (output / "audit.json").exists()


def test_concurrent_change_exit_is_preserved_with_both_audit_reports(audit_paths, tmp_path):
    auditor = audit_paths["wrapper"].with_name("audit_workstation.py")
    auditor.write_text(AUDITOR_STUB + "\nsys.exit(3)\n", encoding="utf-8")
    output = tmp_path / "concurrent change report"
    result = invoke(audit_paths, output)
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["status"] == "AUDIT_CONCURRENT_CHANGE_RETRY"
    launch = json.loads((output / "launcher.json").read_text())
    assert launch["status"] == "AUDIT_CONCURRENT_CHANGE_RETRY"
    assert launch["python_exit_code"] == 3
    assert (output / "audit.json").is_file() and (output / "SUMMARY.md").is_file()


def test_output_directory_cannot_be_reused(audit_paths, tmp_path):
    output = tmp_path / "existing report"
    output.mkdir()
    sentinel = output / "retain.txt"
    sentinel.write_text("original report", encoding="utf-8")
    result = invoke(audit_paths, output)
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["status"] == "OUTPUT_DIRECTORY_ALREADY_EXISTS"
    assert snapshot(output) == {"retain.txt": b"original report"}


@pytest.mark.parametrize("root", ["project", "data", "review"])
def test_output_must_be_outside_all_input_and_review_roots(audit_paths, root):
    output = audit_paths[root] / "new report"
    result = invoke(audit_paths, output)
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["status"] == "OUTPUT_DIRECTORY_UNSAFE"
    assert not output.exists()


@pytest.mark.parametrize("root", ["project", "data", "review"])
def test_input_directory_junction_is_rejected_before_resolution(audit_paths, tmp_path, root):
    target = audit_paths[root]
    junction = tmp_path / (root + " junction")
    setup = tmp_path / "create_junction.ps1"
    setup.write_text(
        "param([string]$Link, [string]$Target)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n",
        encoding="utf-8",
    )
    created = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(setup),
         "-Link", str(junction), "-Target", str(target)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("Local temporary directory junctions are unavailable on this Windows host")
    try:
        before = snapshot(target)
        paths = dict(audit_paths)
        if root == "review":
            paths["wrapper"] = junction / "tools/audit" / WRAPPER.name
        else:
            paths[root] = junction
        output = tmp_path / (root + " redirected report")
        result = invoke(paths, output)
        assert result.returncode == 2, result.stderr
        assert json.loads(result.stdout)["status"] == "INPUT_DIRECTORY_UNSAFE"
        assert not output.exists()
        assert snapshot(target) == before
    finally:
        # Remove the directory junction itself, never traverse/delete its target.
        junction.rmdir()
