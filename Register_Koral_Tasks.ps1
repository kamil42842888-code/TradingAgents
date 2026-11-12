[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'Register', 'Probe', 'Activate', 'Disarm')]
    [string]$Mode = 'Inspect',
    [string]$Project = $PSScriptRoot,
    [switch]$AcceptModelCharges
)
$ErrorActionPreference = 'Stop'
$Project = (Resolve-Path -LiteralPath $Project).Path
$pythonPath = Join-Path $Project '.venv\Scripts\python.exe'
$controllerPath = Join-Path $Project 'KE_DAILY_PAPER.py'
$validation = Join-Path $Project '_Koral_Equities_Source_Backups\validation'
$definitions = @(
    @{Name='KoralEquities_ResearchObserver_5min'; Script='KE_Observer_Scheduler_2026-10-26.py'; Arg='--run-once'; Minutes=4},
    @{Name='KoralEquities_PaperController_5min'; Script='KE_DAILY_PAPER.py'; Arg='--tick'; Minutes=90}
)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
function Get-OwnedTask($definition) {
    $task = Get-ScheduledTask -TaskPath '\' -TaskName $definition.Name -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        $expectedArgs = '"' + (Join-Path $Project $definition.Script) + '" ' + $definition.Arg
        if (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ine $pythonPath -or
            $task.Actions[0].Arguments -cne $expectedArgs -or
            $task.Actions[0].WorkingDirectory -ine $Project) {
            throw ('Unrecognised task preserved: ' + $definition.Name)
        }
    }
    return $task
}
function Assert-Disarmed {
    $configPath = Join-Path $env:USERPROFILE '.tradingagents\paper_operations\daily_v1\config.json'
    if (Test-Path -LiteralPath $configPath) {
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        if ($config.armed -ne $false) { throw 'Controller armed or unknown; disarm before setup' }
    }
}
function Assert-Stopped {
    foreach ($definition in $definitions) {
        $task = Get-OwnedTask $definition
        if ($null -ne $task -and $task.State -ne 'Disabled') { throw ('Task must be disabled: ' + $definition.Name) }
    }
    $processes = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and $_.CommandLine.Contains($Project)
    })
    if ($processes.Count) { throw 'A Python process referencing this project is running; stop it normally first' }
    $ledgerLock = Join-Path $env:USERPROFILE '.tradingagents\paper\v3_autopilot\portfolio.json.lock'
    if (Test-Path -LiteralPath $ledgerLock) { throw 'Existing paper lock requires reconciliation; preserved' }
}
function Invoke-Runtime([string[]]$RuntimeArgs) {
    & $pythonPath -B $controllerPath @RuntimeArgs
    if ($LASTEXITCODE -ne 0) { throw ('Runtime command failed: ' + ($RuntimeArgs -join ' ')) }
}
if ($Mode -eq 'Inspect') {
    Assert-Disarmed
    Assert-Stopped
    foreach ($definition in $definitions) {
        $task = Get-OwnedTask $definition
        if ($null -eq $task) { Write-Output ($definition.Name + ': ABSENT') }
        else { Write-Output ($definition.Name + ': ' + $task.State) }
    }
    Write-Output 'KORAL_PREFLIGHT_STOPPED_AND_DISARMED; READ_ONLY'
    exit 0
}
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Existing project Python missing' }
if ($Mode -eq 'Disarm') {
    foreach ($definition in $definitions) {
        $task = Get-OwnedTask $definition
        if ($null -ne $task) { Disable-ScheduledTask -InputObject $task | Out-Null }
    }
    Invoke-Runtime -RuntimeArgs @('--disarm')
    Write-Output 'KORAL_TASKS_DISABLED; existing running process must finish normally'
    exit 0
}
if ($Mode -eq 'Register') {
    Assert-Disarmed
    Assert-Stopped
    Invoke-Runtime -RuntimeArgs @('--status')
    $backup = Join-Path $Project ('_Koral_Equities_Source_Backups\tasks-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $backup | Out-Null
    $changed = @()
    try {
        foreach ($definition in $definitions) {
            $old = Get-OwnedTask $definition
            if ($null -ne $old) {
                Export-ScheduledTask -InputObject $old | Set-Content -LiteralPath (Join-Path $backup ($definition.Name + '.xml')) -Encoding Unicode
            }
            $action = New-ScheduledTaskAction -Execute $pythonPath -WorkingDirectory $Project -Argument ('"' + (Join-Path $Project $definition.Script) + '" ' + $definition.Arg)
            $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
            $settings = New-ScheduledTaskSettingsSet -Disable -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes $definition.Minutes) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
            $new = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
            $changed += $definition
            Register-ScheduledTask -TaskPath '\' -TaskName $definition.Name -InputObject $new -Force | Out-Null
            if ((Get-OwnedTask $definition).State -ne 'Disabled') { throw 'Registration did not remain disabled' }
        }
    } catch {
        foreach ($definition in $changed) {
            $task = Get-OwnedTask $definition
            if ($null -ne $task) { Disable-ScheduledTask -InputObject $task | Out-Null }
            $xml = Join-Path $backup ($definition.Name + '.xml')
            if (Test-Path -LiteralPath $xml) {
                Register-ScheduledTask -TaskPath '\' -TaskName $definition.Name -Xml (Get-Content -LiteralPath $xml -Raw) -Force | Out-Null
            } elseif ($null -ne $task) { Unregister-ScheduledTask -InputObject $task -Confirm:$false }
        }
        throw
    }
    Write-Output ('KORAL_TASKS_REGISTERED_DISABLED; BACKUP=' + $backup)
    exit 0
}
if ($Mode -eq 'Probe') {
    Assert-Disarmed
    Assert-Stopped
    foreach ($definition in $definitions) { if ($null -eq (Get-OwnedTask $definition)) { throw 'Register both disabled tasks first' } }
    $probeName = 'KoralEquities_RecoveryProbe'
    if (Get-ScheduledTask -TaskPath '\' -TaskName $probeName -ErrorAction SilentlyContinue) { throw 'Existing probe task preserved; investigate it' }
    $token = [guid]::NewGuid().ToString('N')
    $probePath = Join-Path $validation ('probe-' + $token + '.json')
    $action = New-ScheduledTaskAction -Execute $pythonPath -WorkingDirectory $Project -Argument ('"' + $controllerPath + '" --probe-once --probe-id ' + $token)
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    $task = New-ScheduledTask -Action $action -Principal $principal -Settings $settings
    Register-ScheduledTask -TaskPath '\' -TaskName $probeName -InputObject $task | Out-Null
    try {
        Start-ScheduledTask -TaskPath '\' -TaskName $probeName
        $deadline = (Get-Date).AddSeconds(45)
        do {
            Start-Sleep -Milliseconds 500
            $state = Get-ScheduledTask -TaskPath '\' -TaskName $probeName
        } until (((Test-Path -LiteralPath $probePath) -and $state.State -ne 'Running') -or (Get-Date) -ge $deadline)
        $info = Get-ScheduledTaskInfo -TaskPath '\' -TaskName $probeName
        if (-not (Test-Path -LiteralPath $probePath) -or $info.LastTaskResult -ne 0 -or $state.State -eq 'Running') { throw 'Windows scheduled launch not proven' }
        $proof = Get-Content -LiteralPath $probePath -Raw | ConvertFrom-Json
        if ($proof.probe_id -cne $token -or $proof.status -ne 'WINDOWS_RUNTIME_PROBE_PASSED' -or $proof.project -ine $Project) { throw 'Probe receipt mismatch' }
        $proof.status = 'WINDOWS_SCHEDULER_PROBE_PASSED'
        $proof | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $validation 'scheduler.json') -Encoding UTF8
        Write-Output 'WINDOWS_SCHEDULER_PROBE_PASSED; production tasks remain DISABLED'
    } finally {
        $state = Get-ScheduledTask -TaskPath '\' -TaskName $probeName
        if ($state.State -eq 'Running') { Disable-ScheduledTask -InputObject $state | Out-Null }
        else { Unregister-ScheduledTask -InputObject $state -Confirm:$false }
    }
    exit 0
}
if ($Mode -eq 'Activate') {
    if (-not $AcceptModelCharges) { throw 'Explicit -AcceptModelCharges required for final activation' }
    Assert-Disarmed
    Assert-Stopped
    foreach ($definition in $definitions) {
        $task = Get-OwnedTask $definition
        if ($null -eq $task -or $task.Principal.LogonType -ne 'Interactive' -or $task.Settings.MultipleInstances -ne 'IgnoreNew' -or -not $task.Settings.WakeToRun) { throw 'Task configuration failed activation gate' }
    }
    try {
        Invoke-Runtime -RuntimeArgs @('--activate', '--accept-model-charges')
        foreach ($definition in $definitions) { Enable-ScheduledTask -InputObject (Get-OwnedTask $definition) | Out-Null }
    } catch {
        foreach ($definition in $definitions) { Disable-ScheduledTask -InputObject (Get-OwnedTask $definition) | Out-Null }
        Invoke-Runtime -RuntimeArgs @('--disarm')
        throw
    }
    Write-Output 'KORAL_PAPER_SCHEDULE_ACTIVE; logged-in powered online Windows session required'
}
