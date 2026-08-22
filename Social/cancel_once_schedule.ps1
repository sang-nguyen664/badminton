#powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\personal\Badminton\Social\cancel_once_schedule.ps1" -Account sang -Time 19:00

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('linh', 'sang', 'chau')]
    [string]$Account,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(\d{2}:\d{2}|\d{4})$')]
    [string]$Time
)

$ErrorActionPreference = 'Stop'

$TaskPrefix = 'Badminton Social Scheduler'
$NormalizedTime = $Time.Replace(':', '')
$TaskName = "$TaskPrefix - $Account - $NormalizedTime"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if (-not $Task) {
    Write-Host "[INFO] Scheduled task not found: $TaskName"
    Write-Host '[INFO] Current matching tasks:'
    Get-ScheduledTask -TaskName "$TaskPrefix*" -ErrorAction SilentlyContinue |
        Select-Object TaskName, State |
        Format-Table -AutoSize
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "[OK] Cancelled task: $TaskName"