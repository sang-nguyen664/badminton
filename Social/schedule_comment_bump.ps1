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
$IntervalMinutes = 15

# Moi slot tuong ung 1 dot dang bai: bump dung 3 lan luc :15, :30, :45 sau gio dang roi ngung.
# linh dang 11/14/17h (sang comment); sang dang 12/15/18h (linh comment); chau dang 13/16h (linh comment).
$Slots = @(
    @{ Slot = '1100'; Start = '11:15'; End = '11:45' },
    @{ Slot = '1200'; Start = '12:15'; End = '12:45' },
    @{ Slot = '1300'; Start = '13:15'; End = '13:45' },
    @{ Slot = '1400'; Start = '14:15'; End = '14:45' },
    @{ Slot = '1500'; Start = '15:15'; End = '15:45' },
    @{ Slot = '1600'; Start = '16:15'; End = '16:45' },
    @{ Slot = '1700'; Start = '17:15'; End = '17:45' },
    @{ Slot = '1800'; Start = '18:15'; End = '18:45' }
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
