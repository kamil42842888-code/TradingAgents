"""Exercise Windows inspection with mocked task APIs; never register a task."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

POWERSHELL = shutil.which("powershell") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell required")

SCRIPT = r"""
param([string]$StatusScript, [string]$FixtureProject, [string]$Scenario)
$ErrorActionPreference = 'Stop'
function Register-ScheduledTask { throw 'Mutation forbidden' }
function Enable-ScheduledTask { throw 'Mutation forbidden' }
function Disable-ScheduledTask { throw 'Mutation forbidden' }
function Start-ScheduledTask { throw 'Mutation forbidden' }
function Stop-ScheduledTask { throw 'Mutation forbidden' }
function Unregister-ScheduledTask { throw 'Mutation forbidden' }
function Get-CimInstance { throw 'Read-only status must not use setup process gates' }
function Get-ScheduledTask {
    [CmdletBinding()] param([string]$TaskPath)
    if ($Scenario -eq 'unavailable') { throw 'Scheduler unavailable fixture' }
    if ($Scenario -eq 'absent') { return }
    foreach ($entry in @(
        @('KoralEquities_ResearchObserver_5min', 'KE_Observer_Scheduler_2026-10-26.py', '--run-once'),
        @('KoralEquities_PaperController_5min', 'KE_DAILY_PAPER.py', '--tick')
    )) {
        $projectPath = $FixtureProject
        if ($Scenario -eq 'moved') { $projectPath = Join-Path $FixtureProject 'old-location' }
        [pscustomobject]@{
            TaskName=$entry[0]; State='Ready'
            Actions=@([pscustomobject]@{
                Execute=(Join-Path $projectPath '.venv\Scripts\python.exe')
                Arguments=('"' + (Join-Path $projectPath $entry[1]) + '" ' + $entry[2])
                WorkingDirectory=$projectPath
            })
            Triggers=@([pscustomobject]@{
                Enabled=($Scenario -ne 'trigger_disabled')
                Repetition=[pscustomobject]@{ Interval='PT5M' }
            })
            Principal=[pscustomobject]@{
                UserId=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
                LogonType='Interactive'; RunLevel='Limited'
            }
            Settings=[pscustomobject]@{ MultipleInstances='IgnoreNew'; WakeToRun=$true }
        }
    }
}
function Get-ScheduledTaskInfo {
    [CmdletBinding()] param($InputObject)
    [pscustomobject]@{
        LastRunTime=[datetime]'2026-11-19T15:00:00'; NextRunTime=[datetime]'2026-11-19T15:05:00'
        LastTaskResult=$(if ($Scenario -eq 'failed') { 2 } else { 0 }); NumberOfMissedRuns=0
    }
}
& $StatusScript -Mode Status -Project $FixtureProject
exit $LASTEXITCODE
"""


@pytest.mark.parametrize(
    "scenario", ["healthy", "absent", "moved", "failed", "trigger_disabled", "unavailable"]
)
def test_status_handles_enabled_missing_moved_and_failed_tasks_read_only(tmp_path, scenario):
    project = tmp_path / "project"
    python = project / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture not executable")
    for name in ("KE_DAILY_PAPER.py", "KE_Observer_Scheduler_2026-10-26.py"):
        (project / name).write_text("raise AssertionError('must never execute')", encoding="utf-8")
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    wrapper = tmp_path / "inspect.ps1"
    wrapper.write_text(SCRIPT, encoding="utf-8")
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(wrapper),
            "-StatusScript",
            str(Path(__file__).parents[1] / "Register_Koral_Tasks.ps1"),
            "-FixtureProject",
            str(project),
            "-Scenario",
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == (2 if scenario == "unavailable" else 0), result.stderr
    report = json.loads(result.stdout)
    assert report["read_only"] is True and report["model_calls"] == report["account_writes"] == 0
    if scenario == "unavailable":
        assert report["status"] == "INSPECTION_UNAVAILABLE"
    elif scenario == "healthy":
        assert report["status"] == "TASK_CONFIGURATION_MATCHES"
    else:
        expected = {
            "absent": "TASK_ABSENT",
            "moved": "TASK_ACTION_MISMATCH",
            "failed": "LAST_TASK_RESULT_NONZERO",
            "trigger_disabled": "TASK_TRIGGER_DISABLED",
        }[scenario]
        assert all(expected in row["findings"] for row in report["tasks"])
    assert {
        p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()
    } == before
