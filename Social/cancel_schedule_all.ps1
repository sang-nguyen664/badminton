#powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\personal\Badminton\Social\cancel_schedule_all.ps1"

$ErrorActionPreference = 'Stop'

$TaskPrefix = 'Badminton Social Scheduler'
$Tasks = Get-ScheduledTask -TaskName "$TaskPrefix*" -ErrorAction SilentlyContinue

if (-not $Tasks) {
    Write-Host "[INFO] No scheduled tasks found for prefix: $TaskPrefix"
    exit 0
}

foreach ($Task in $Tasks) {
    Unregister-ScheduledTask -TaskName $Task.TaskName -Confirm:$false
    Write-Host "[OK] Cancelled task: $($Task.TaskName)"
}

Write-Host "[OK] Cancelled all scheduled tasks for prefix: $TaskPrefix"