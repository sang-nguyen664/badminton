#powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\personal\Badminton\Social\schedule_comment_bump.ps1" -Mode install

param(
    [ValidateSet('install', 'run', 'list', 'uninstall')]
    [string]$Mode = 'install'
)

$ErrorActionPreference = 'Stop'

$ScriptPath = if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path }
$ScriptDir = Split-Path -Parent $ScriptPath
if ((Split-Path -Leaf $ScriptDir) -ieq 'Social') {
    $SocialDir = $ScriptDir
}
else {
    $SocialDir = Join-Path $ScriptDir 'Social'
}
$BatPath = Join-Path $SocialDir 'Bump\run_comment_bump_today.bat'
$TaskPrefix = 'Badminton Social Comment Bump'
$IntervalMinutes = 30

# Moi slot tuong ung 1 dot dang bai. Tool se tu --slot de chi lay link cua dot do.
# linh dang 12:00 + 16:00 (comment bang sang); sang dang 14:00 + 18:00 (comment bang linh).
$Slots = @(
    @{ Slot = '1200'; Start = '12:30'; End = '16:00' },
    @{ Slot = '1400'; Start = '14:30'; End = '18:00' },
    @{ Slot = '1600'; Start = '16:30'; End = '19:30' },
    @{ Slot = '1800'; Start = '18:30'; End = '19:30' }
)

function Install-CommentBumpTask {
    if (-not (Test-Path $BatPath)) {
        throw "Batch file not found: $BatPath"
    }

    foreach ($Item in $Slots) {
        $start = [datetime]::ParseExact($Item.Start, 'HH:mm', $null)
        $end = [datetime]::ParseExact($Item.End, 'HH:mm', $null)
        $duration = $end - $start
        $taskName = "$TaskPrefix - slot $($Item.Slot)"

        $Action = New-ScheduledTaskAction `
            -Execute $BatPath `
            -Argument "--slot $($Item.Slot)" `
            -WorkingDirectory $SocialDir
        # PS 5.1 khong cho -RepetitionInterval tren -Daily, nen lay Repetition tu trigger -Once.
        $Trigger = New-ScheduledTaskTrigger -Daily -At $start
        $Trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $start `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
            -RepetitionDuration $duration).Repetition
        $Settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew

        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $Action `
            -Trigger $Trigger `
            -Settings $Settings `
            -Description "Comment '.' vao bai viet dot $($Item.Slot). Chay hang ngay $($Item.Start)-$($Item.End), moi $IntervalMinutes phut." `
            -Force | Out-Null

        Write-Host "[OK] Installed task: $taskName ($($Item.Start)-$($Item.End), moi $IntervalMinutes phut)"
    }
}

function Show-CommentBumpTask {
    Get-ScheduledTask -TaskName "$TaskPrefix*" -ErrorAction SilentlyContinue |
        Select-Object TaskName, State |
        Format-Table -AutoSize
}

function Uninstall-CommentBumpTask {
    Get-ScheduledTask -TaskName "$TaskPrefix*" -ErrorAction SilentlyContinue |
        ForEach-Object {
            Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
            Write-Host "[OK] Removed task: $($_.TaskName)"
        }
}

switch ($Mode) {
    'install' { Install-CommentBumpTask }
    'run' { & $BatPath }
    'list' { Show-CommentBumpTask }
    'uninstall' { Uninstall-CommentBumpTask }
}
