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
# Moi slot tuong ung 1 dot dang bai: bump dung 1 lan luc :20 sau gio dang (68 bai / 2 tab ~ 26p, xong truoc :46).
# linh dang 11/14/17h (trang comment); sang dang 12/15/18h (linh comment); trang dang 13/16h (sang comment).
$Slots = @(
    @{ Slot = '1100'; Start = '11:20' },
    @{ Slot = '1200'; Start = '12:20' },
    @{ Slot = '1300'; Start = '13:20' },
    @{ Slot = '1400'; Start = '14:20' },
    @{ Slot = '1500'; Start = '15:20' },
    @{ Slot = '1600'; Start = '16:20' },
    @{ Slot = '1700'; Start = '17:20' },
    @{ Slot = '1800'; Start = '18:20' }
)

function Install-CommentBumpTask {
    if (-not (Test-Path $BatPath)) {
        throw "Batch file not found: $BatPath"
    }

    foreach ($Item in $Slots) {
        $start = [datetime]::ParseExact($Item.Start, 'HH:mm', $null)
        $taskName = "$TaskPrefix - slot $($Item.Slot)"

        $Action = New-ScheduledTaskAction `
            -Execute $BatPath `
            -Argument "--slot $($Item.Slot)" `
            -WorkingDirectory $SocialDir
        $Trigger = New-ScheduledTaskTrigger -Daily -At $start
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
            -Description "Comment 'Ben minh van con slot nha' vao bai viet dot $($Item.Slot). Chay hang ngay luc $($Item.Start), dung 1 lan." `
            -Force | Out-Null

        Write-Host "[OK] Installed task: $taskName ($($Item.Start), dung 1 lan)"
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
