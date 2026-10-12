#powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\personal\Badminton\Social\schedule_zalo_auto_reply.ps1" -Mode install

param(
    [ValidateSet('install', 'list', 'uninstall')]
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
$BatPath = Join-Path $SocialDir 'Zalo\run_zalo_auto_reply.bat'
$TaskName = 'Badminton Social Zalo Auto Reply'

function Install-ZaloAutoReplyTask {
    if (-not (Test-Path $BatPath)) {
        throw "Batch file not found: $BatPath"
    }

    $Action = New-ScheduledTaskAction `
        -Execute $BatPath `
        -WorkingDirectory $SocialDir
    # Chay nen lien tuc: no luc dang nhap, tu khoi dong lai neu process chet.
    # Dang ky duoi user hien tai (AtLogOn khong chi dinh user se doi quyen admin).
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
    $Principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 5 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description "Tu dong tra loi tin nhan nguoi la tren Zalo web (chat.zalo.me). Chay nen lien tuc, quet moi 60s." `
        -Force | Out-Null

    Write-Host "[OK] Installed task: $TaskName (chay khi log on, tu restart neu crash)"
    Write-Host "[INFO] Lan dau chay can quet QR: Zalo\run_zalo_auto_reply.bat --setup"
}

function Show-ZaloAutoReplyTask {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
        Select-Object TaskName, State |
        Format-Table -AutoSize
}

function Uninstall-ZaloAutoReplyTask {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
        ForEach-Object {
            Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
            Write-Host "[OK] Removed task: $($_.TaskName)"
        }
}

switch ($Mode) {
    'install' { Install-ZaloAutoReplyTask }
    'list' { Show-ZaloAutoReplyTask }
    'uninstall' { Uninstall-ZaloAutoReplyTask }
}
