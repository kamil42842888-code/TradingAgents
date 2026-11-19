[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Project,
    [string]$DataRoot = (Join-Path $env:USERPROFILE '.tradingagents'),
    [string]$Python,
    [string]$OutputDirectory,
    [string]$AsOf
)
$ErrorActionPreference = 'Stop'
$script:ReportDirectory = $null

function Write-NewJson([string]$Path, $Value) {
    $raw = [System.Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 12) + "`n")
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
    try { $stream.Write($raw, 0, $raw.Length) } finally { $stream.Dispose() }
}

function Finish-Audit([string]$Status, [int]$Code, [string]$NextStep, $PythonExitCode = $null) {
    $record = [ordered]@{
        schema='koral-readonly-audit-launch-v1'; read_only=$true
        checked_utc=[DateTime]::UtcNow.ToString('o'); status=$Status
        next_step=$NextStep
        python_exit_code=$PythonExitCode; target_application_executed=$false
        task_mutations=0; account_writes=0; model_calls=0
    }
    if ($script:ReportDirectory) {
        Write-NewJson (Join-Path $script:ReportDirectory 'launcher.json') $record
    }
    $consoleRecord = [ordered]@{}
    foreach ($key in $record.Keys) { $consoleRecord[$key] = $record[$key] }
    $consoleRecord['report_directory'] = $script:ReportDirectory
    $consoleRecord | ConvertTo-Json -Depth 8
    exit $Code
}

function Get-FullPath([string]$Value) {
    $full = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Value)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.Length -eq $root.Length) { return $root }
    return $full.TrimEnd('\', '/')
}

function Test-Within([string]$Candidate, [string]$Root) {
    return $Candidate.Equals($Root, [System.StringComparison]::OrdinalIgnoreCase) -or
        $Candidate.StartsWith($Root.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-ReparseAncestor([string]$Path) {
    $cursor = $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return $true }
        }
        $parent = [System.IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $false
}

function Get-IntervalSeconds($Value) {
    try {
        if ($Value -is [TimeSpan]) { return $Value.TotalSeconds }
        if ([string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
        return [System.Xml.XmlConvert]::ToTimeSpan([string]$Value).TotalSeconds
    } catch { return $null }
}

function Test-CurrentPrincipal([string]$UserId, $Identity) {
    if ([string]::IsNullOrWhiteSpace($UserId)) { return $false }
    if ($UserId -ieq $Identity.Name -or $UserId -ieq $Identity.User.Value) { return $true }
    try {
        $account = [System.Security.Principal.NTAccount]::new($UserId)
        return $account.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $Identity.User.Value
    } catch { return $null }
}

function Get-UtcTimestamp($Value) {
    if ($null -eq $Value -or $Value.Year -le 1900) { return $null }
    return $Value.ToUniversalTime().ToString('o')
}

try {
    # Check the supplied paths before provider resolution can hide an alias.
    $inputRoots = @(
        (Get-FullPath $Project),
        (Get-FullPath $DataRoot),
        (Get-FullPath (Join-Path $PSScriptRoot '..\..'))
    )
    foreach ($inputRoot in $inputRoots) {
        if (Test-ReparseAncestor $inputRoot) {
            Finish-Audit 'INPUT_DIRECTORY_UNSAFE' 2 'Use the actual input directories without junction or symbolic-link ancestors.'
        }
    }
    if (-not (Test-Path -LiteralPath $Project -PathType Container)) {
        Finish-Audit 'PROJECT_DIRECTORY_MISSING' 2 'Supply the actual existing project directory with -Project.'
    }
    $projectPath = Get-FullPath ((Resolve-Path -LiteralPath $Project).Path)
    $dataPath = Get-FullPath $DataRoot
    $reviewRoot = Get-FullPath ((Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path)
    if (-not $OutputDirectory) {
        $OutputDirectory = Join-Path $env:USERPROFILE (
            'KoralEquities_Audit_' + [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0, 8)
        )
    }
    $outputPath = Get-FullPath $OutputDirectory
    if (Test-Path -LiteralPath $outputPath) {
        Finish-Audit 'OUTPUT_DIRECTORY_ALREADY_EXISTS' 2 'Choose a new output directory; existing reports are never overwritten.'
    }
    foreach ($protectedRoot in @($projectPath, $dataPath, $reviewRoot)) {
        if (Test-Within $outputPath $protectedRoot) {
            Finish-Audit 'OUTPUT_DIRECTORY_UNSAFE' 2 'Choose an output directory outside the project, private data and review directories.'
        }
    }
    if (Test-ReparseAncestor $outputPath) {
        Finish-Audit 'OUTPUT_DIRECTORY_UNSAFE' 2 'Choose an output directory without junction or symbolic-link ancestors.'
    }
    $outputParent = Split-Path -Parent $outputPath
    if (-not (Test-Path -LiteralPath $outputParent -PathType Container)) {
        Finish-Audit 'OUTPUT_PARENT_MISSING' 2 'Choose a new output directory under an existing parent directory.'
    }
    New-Item -ItemType Directory -Path $outputPath -ErrorAction Stop | Out-Null
    $script:ReportDirectory = $outputPath

    $expectedPython = Join-Path $projectPath '.venv\Scripts\python.exe'
    $auditPython = if ($Python) { Get-FullPath $Python } else { $expectedPython }
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $definitions = @(
        @{Name='KoralEquities_ResearchObserver_5min'; Script='KE_Observer_Scheduler_2026-10-26.py'; Arg='--run-once'},
        @{Name='KoralEquities_PaperController_5min'; Script='KE_DAILY_PAPER.py'; Arg='--tick'}
    )
    $rows = @()
    foreach ($definition in $definitions) {
        try {
            $matches = @(Get-ScheduledTask -TaskPath '\' -TaskName $definition.Name -ErrorAction Stop)
        } catch {
            $missing = $_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::ObjectNotFound
            $rows += [ordered]@{
                name=$definition.Name; present=$(if ($missing) { $false } else { $null })
                findings=@($(if ($missing) { 'TASK_ABSENT' } else { 'TASK_INSPECTION_UNAVAILABLE' }))
            }
            continue
        }
        if ($matches.Count -ne 1) {
            $rows += [ordered]@{
                name=$definition.Name; present=$false
                findings=@($(if ($matches.Count -eq 0) { 'TASK_ABSENT' } else { 'TASK_IDENTITY_AMBIGUOUS' }))
            }
            continue
        }
        $task = $matches[0]
        $actions = @($task.Actions | Where-Object { $null -ne $_ })
        $triggers = @($task.Triggers | Where-Object { $null -ne $_ })
        $expectedArgs = '"' + (Join-Path $projectPath $definition.Script) + '" ' + $definition.Arg
        $actionMatches = $actions.Count -eq 1 -and $actions[0].Execute -ieq $expectedPython -and
            $actions[0].Arguments -ceq $expectedArgs -and $actions[0].WorkingDirectory -ieq $projectPath
        $intervals = @($triggers | ForEach-Object { Get-IntervalSeconds $_.Repetition.Interval })
        $fiveMinutes = $triggers.Count -eq 1 -and $intervals.Count -eq 1 -and $intervals[0] -eq 300
        $sameUser = Test-CurrentPrincipal ([string]$task.Principal.UserId) $identity
        $principalMatches = $sameUser -eq $true -and
            [string]$task.Principal.LogonType -in @('Interactive', '3') -and
            [string]$task.Principal.RunLevel -in @('Limited', '0')
        $state = switch ([string]$task.State) {
            { $_ -in @('Ready', '3') } { 'Ready'; break }
            { $_ -in @('Running', '4') } { 'Running'; break }
            { $_ -in @('Disabled', '1') } { 'Disabled'; break }
            { $_ -in @('Queued', '2') } { 'Queued'; break }
            default { 'Unknown' }
        }
        $instances = switch ([string]$task.Settings.MultipleInstances) {
            { $_ -in @('Parallel', '0') } { 'Parallel'; break }
            { $_ -in @('Queue', '1') } { 'Queue'; break }
            { $_ -in @('IgnoreNew', '2') } { 'IgnoreNew'; break }
            { $_ -in @('StopExisting', '3') } { 'StopExisting'; break }
            default { 'Unknown' }
        }
        $findings = @()
        if (-not $actionMatches) { $findings += 'TASK_ACTION_MISMATCH' }
        if (-not (Test-Path -LiteralPath $expectedPython -PathType Leaf)) { $findings += 'PROJECT_PYTHON_MISSING' }
        if (-not (Test-Path -LiteralPath (Join-Path $projectPath $definition.Script) -PathType Leaf)) { $findings += 'TASK_SCRIPT_MISSING' }
        if (-not $fiveMinutes) { $findings += 'TRIGGER_INTERVAL_MISMATCH' }
        if (@($triggers | Where-Object { $_.Enabled -ne $true }).Count) { $findings += 'TASK_TRIGGER_DISABLED' }
        if ($null -eq $sameUser) { $findings += 'PRINCIPAL_IDENTITY_UNVERIFIED' }
        if (-not $principalMatches) { $findings += 'TASK_PRINCIPAL_MISMATCH' }
        if ($instances -ne 'IgnoreNew') { $findings += 'MULTIPLE_INSTANCE_POLICY_MISMATCH' }
        if (-not $task.Settings.WakeToRun) { $findings += 'WAKE_TO_RUN_DISABLED' }
        if ($state -notin @('Ready', 'Running', '3', '4')) { $findings += 'TASK_NOT_ENABLED' }
        $runtime = $null
        try {
            $info = Get-ScheduledTaskInfo -InputObject $task -ErrorAction Stop
            $runtime = [ordered]@{
                last_run_time=(Get-UtcTimestamp $info.LastRunTime)
                next_run_time=(Get-UtcTimestamp $info.NextRunTime)
                last_task_result=[long]$info.LastTaskResult
                missed_runs=[long]$info.NumberOfMissedRuns
            }
            if ($state -notin @('Running', '4') -and $info.LastTaskResult -ne 0) { $findings += 'LAST_TASK_RESULT_NONZERO' }
        } catch { $findings += 'TASK_RUNTIME_INFO_UNAVAILABLE' }
        $rows += [ordered]@{
            name=$definition.Name; present=$true; state=$state
            action_matches_project=[bool]$actionMatches; principal_matches_user=[bool]$principalMatches
            trigger_interval_seconds=$(if ($intervals.Count -eq 1) { $intervals[0] } else { $null })
            wake_to_run=[bool]$task.Settings.WakeToRun; multiple_instances=$instances
            runtime=$runtime; findings=$findings
        }
    }
    $hasFindings = @($rows | Where-Object { $_.findings.Count -gt 0 }).Count -gt 0
    $taskReport = [ordered]@{
        schema='koral-windows-task-status-v1'; read_only=$true
        checked_utc=[DateTime]::UtcNow.ToString('o')
        status=$(if ($hasFindings) { 'ATTENTION_REQUIRED' } else { 'TASK_CONFIGURATION_MATCHES' })
        tasks=$rows; account_writes=0; model_calls=0
        forward_session_verification='NOT_PERFORMED_BY_TASK_INSPECTION'
    }
    $taskPath = Join-Path $outputPath 'tasks.json'
    Write-NewJson $taskPath $taskReport
    if (-not (Test-Path -LiteralPath $auditPython -PathType Leaf)) {
        Finish-Audit 'PYTHON_INTERPRETER_MISSING' 2 'Task report saved. Supply an existing Python interpreter with -Python; no installation was attempted.'
    }
    $auditScript = Join-Path $PSScriptRoot 'audit_workstation.py'
    if (-not (Test-Path -LiteralPath $auditScript -PathType Leaf)) {
        Finish-Audit 'BUNDLED_AUDITOR_MISSING' 2 'Task report saved. Use the complete reviewed audit package; no target application was run.'
    }
    $auditArgs = @('-I', '-S', '-B', $auditScript, '--project', $projectPath,
        '--data-root', $dataPath, '--task-status', $taskPath, '--output-dir', $outputPath)
    if ($AsOf) { $auditArgs += @('--as-of', $AsOf) }
    try {
        $null = & $auditPython @auditArgs 2>&1
        $auditCode = $LASTEXITCODE
    } catch {
        Finish-Audit 'PYTHON_AUDIT_LAUNCH_FAILED' 2 'Task report saved. Check the selected interpreter and the complete audit package; no task changes were made.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $outputPath 'audit.json') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $outputPath 'SUMMARY.md') -PathType Leaf)) {
        Finish-Audit 'PYTHON_AUDIT_REPORT_INCOMPLETE' 2 'Task report saved. Python did not produce both audit outputs; inspect the complete package and interpreter.' $auditCode
    }
    if ($auditCode -eq 3) {
        Finish-Audit 'AUDIT_CONCURRENT_CHANGE_RETRY' 3 'Input evidence changed during inspection. Retry using a new report directory; no task changes were made.' $auditCode
    }
    if ($auditCode -ne 0) {
        Finish-Audit 'AUDIT_COMPLETED_WITH_FINDINGS' 2 'Read SUMMARY.md and audit.json. No repair, research or task mutation was performed.' $auditCode
    }
    Finish-Audit 'AUDIT_COMPLETED' 0 'Read SUMMARY.md and audit.json. Review the evidence before deciding whether any repair is needed.' $auditCode
} catch {
    # Exception messages and subprocess output may contain private paths or arguments.
    Finish-Audit 'AUDIT_LAUNCHER_BLOCKED' 2 'Check the supplied directories and report destination. No repair or task mutation was attempted.'
}
