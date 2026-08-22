#powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\personal\Badminton\Social\schedule_social_scheduler.ps1" -Mode install

param(
    [ValidateSet('install', 'run', 'list')]
    [string]$Mode = 'install',

    [ValidateSet('linh', 'sang', 'chau')]
    [string]$Account = 'linh'
)

$ErrorActionPreference = 'Stop'

$ScriptPath = if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path }
$ScriptDir = Split-Path -Parent $ScriptPath
if ((Split-Path -Leaf $ScriptDir) -ieq 'Social') {
    $RootDir = Split-Path -Parent $ScriptDir
    $SocialDir = $ScriptDir
}
else {
    $RootDir = $ScriptDir
    $SocialDir = Join-Path $RootDir 'Social'
}
$PythonExe = Join-Path $SocialDir '.venv\Scripts\python.exe'
$TaskPrefix = 'Badminton Social Scheduler'
$Weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')

$Schedule = @(
    @{ Account = 'linh'; Time = '12:00' },
    @{ Account = 'linh'; Time = '16:00' },
    @{ Account = 'sang'; Time = '14:00' },
    @{ Account = 'sang'; Time = '18:00' }
)

function Resolve-PythonExe {
    if (Test-Path $PythonExe) {
        return $PythonExe
    }

    $FallbackPython = 'C:\Users\sang.nguyen\.local\bin\python3.14.exe'
    if (Test-Path $FallbackPython) {
        return $FallbackPython
    }

    return 'py.exe'
}

function Invoke-SocialScheduler {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('linh', 'sang', 'chau')]
        [string]$SelectedAccount
    )

    $ResolvedPython = Resolve-PythonExe
    $AccountConfig = "config/account_$SelectedAccount.json"
    $SummaryFile = Join-Path $env:TEMP "social_scheduler_${SelectedAccount}_summary_$(Get-Random)_$(Get-Random).txt"

    if (-not (Test-Path (Join-Path $SocialDir $AccountConfig))) {
        throw "Account config not found: $AccountConfig"
    }

    Push-Location $SocialDir
    try {
        $env:SOCIAL_SCHEDULER_SUMMARY_FILE = $SummaryFile

        Write-Host "[INFO] Running Social scheduler for account=$SelectedAccount"
        Write-Host "[INFO] Working directory: $SocialDir"
        Write-Host "[INFO] Python: $ResolvedPython"

        & $ResolvedPython social_scheduler.py `
            --account-config $AccountConfig `
            --targets config/targets.json `
            --targets config/targets_admin.json `
            --posts config/posts.json `
            --performance-mode `
            --concurrency 2

        $ExitCode = $LASTEXITCODE

        Write-Host ''
        Write-Host '===== Scheduler Summary ====='
        if (Test-Path $SummaryFile) {
            Get-Content $SummaryFile
            Write-Host ''
        }
        Write-Host "Process exit code: $ExitCode"
        if ($ExitCode -eq 0) {
            Write-Host 'Overall: SUCCESS'
        }
        else {
            Write-Host 'Overall: FAILURE'
        }
        Write-Host '============================='

        exit $ExitCode
    }
    finally {
        if (Test-Path $SummaryFile) {
            Remove-Item $SummaryFile -Force -ErrorAction SilentlyContinue
        }
        Remove-Item Env:SOCIAL_SCHEDULER_SUMMARY_FILE -ErrorAction SilentlyContinue
        Pop-Location
    }
}

function Install-SocialSchedulerTasks {
    $PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $DesiredTaskNames = $Schedule | ForEach-Object { "$TaskPrefix - $($_.Account) - $($_.Time.Replace(':', ''))" }

    Get-ScheduledTask -TaskName "$TaskPrefix*" -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -notin $DesiredTaskNames } |
        ForEach-Object {
            Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
            Write-Host "[OK] Removed old task: $($_.TaskName)"
        }

    foreach ($Item in $Schedule) {
        $TaskName = "$TaskPrefix - $($Item.Account) - $($Item.Time.Replace(':', ''))"
        $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -Mode run -Account $($Item.Account)"
        $Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $Arguments -WorkingDirectory $RootDir
        $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At ([datetime]::ParseExact($Item.Time, 'HH:mm', $null))
        $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $Action `
            -Trigger $Trigger `
            -Settings $Settings `
            -Description "Run Badminton Social scheduler for account $($Item.Account) at $($Item.Time), Monday through Friday." `
            -Force | Out-Null

        Write-Host "[OK] Installed task: $TaskName"
    }
}

function Show-SocialSchedulerTasks {
    Get-ScheduledTask -TaskName "$TaskPrefix*" -ErrorAction SilentlyContinue |
        Select-Object TaskName, State |
        Format-Table -AutoSize
}

switch ($Mode) {
    'install' { Install-SocialSchedulerTasks }
    'run' { Invoke-SocialScheduler -SelectedAccount $Account }
    'list' { Show-SocialSchedulerTasks }
}