#powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\personal\Badminton\Social\schedule_social_scheduler.ps1" -Mode install

param(
    [ValidateSet('install', 'run', 'list')]
    [string]$Mode = 'install',

    [ValidateSet('linh', 'sang', 'chau')]
    [string]$Account = 'linh',

    [string]$ScheduledTime = ''
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
$DailyLogDir = Join-Path $SocialDir 'logs\daily_log'
$LockDir = Join-Path $SocialDir 'logs\locks'
$LockStaleMinutes = 45
$BumpFinishWaitMinutes = 15   # bump khoe manh chay ~7-9p; qua 15p coi nhu treo
$PostedLinksDir = Join-Path $SocialDir 'logs\posted_links'
$PostedLinksCsv = Join-Path $PostedLinksDir 'posted_links.csv'
$Weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')

$Schedule = @(
    @{ Account = 'linh'; Time = '11:00' },
    @{ Account = 'linh'; Time = '14:00' },
    @{ Account = 'linh'; Time = '17:00' },
    @{ Account = 'sang'; Time = '12:00' },
    @{ Account = 'sang'; Time = '15:00' },
    @{ Account = 'sang'; Time = '18:00' },
    @{ Account = 'chau'; Time = '13:00' },
    @{ Account = 'chau'; Time = '16:00' }
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

function Resolve-ScheduledTimeForLog {
    param(
        [string]$Value
    )

    if ($Value -match '^\d{2}:\d{2}$') {
        return $Value
    }
    if ($Value -match '^\d{4}$') {
        return "$($Value.Substring(0, 2)):$($Value.Substring(2, 2))"
    }

    return (Get-Date).ToString('HH:mm')
}

function Get-SummaryCounts {
    param(
        [string[]]$SummaryLines
    )

    $SuccessCount = 0
    $FailedCount = 0
    foreach ($Line in $SummaryLines) {
        if ($Line -match 'thanh cong=(\d+), that bai=(\d+)') {
            $SuccessCount = [int]$Matches[1]
            $FailedCount = [int]$Matches[2]
            break
        }
    }

    [PSCustomObject]@{
        Success = $SuccessCount
        Failed = $FailedCount
    }
}

function Get-FailedGroupNotes {
    param(
        [string[]]$SummaryLines
    )

    $Notes = @()
    for ($Index = 0; $Index -lt $SummaryLines.Count; $Index++) {
        if ($SummaryLines[$Index] -match 'Link bai viet da dang thanh cong') {
            break
        }
        if ($SummaryLines[$Index] -match '^\s*-\s*name=(.*)$') {
            $Name = $Matches[1].Trim()
            if (-not $Name -or $Name -eq 'none') {
                continue
            }

            $Url = ''
            if (($Index + 1) -lt $SummaryLines.Count -and $SummaryLines[$Index + 1] -match '^\s*url=(.*)$') {
                $Url = $Matches[1].Trim()
            }

            $Reason = ''
            if (($Index + 2) -lt $SummaryLines.Count -and $SummaryLines[$Index + 2] -match '^\s*reason=(.*)$') {
                $Reason = $Matches[1].Trim()
            }

            $Notes += [PSCustomObject]@{
                Name = $Name
                Url = $Url
                Reason = $Reason
            }
        }
    }

    return $Notes
}

function Get-SucceededPostLinks {
    param(
        [string[]]$SummaryLines
    )

    $Links = @()
    $SectionIndex = -1
    for ($Index = 0; $Index -lt $SummaryLines.Count; $Index++) {
        if ($SummaryLines[$Index] -match 'Link bai viet da dang thanh cong') {
            $SectionIndex = $Index
            break
        }
    }
    if ($SectionIndex -lt 0) {
        return $Links
    }

    for ($Index = $SectionIndex + 1; $Index -lt $SummaryLines.Count; $Index++) {
        if ($SummaryLines[$Index] -match '^\s*-\s*name=(.*)$') {
            $Name = $Matches[1].Trim()
            if (-not $Name -or $Name -eq 'none') {
                continue
            }

            $GroupUrl = ''
            $PostUrl = ''
            if (($Index + 1) -lt $SummaryLines.Count -and $SummaryLines[$Index + 1] -match '^\s*group_url=(.*)$') {
                $GroupUrl = $Matches[1].Trim()
            }
            if (($Index + 2) -lt $SummaryLines.Count -and $SummaryLines[$Index + 2] -match '^\s*post_url=(.*)$') {
                $PostUrl = $Matches[1].Trim()
            }

            $Links += [PSCustomObject]@{
                Name     = $Name
                GroupUrl = $GroupUrl
                PostUrl  = $PostUrl
            }
        }
    }

    return $Links
}

function Save-PostedLinksCsv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SelectedAccount,

        [Parameter(Mandatory = $true)]
        [string]$SelectedScheduledTime,

        [AllowEmptyCollection()]
        [array]$SucceededLinks
    )

    if ($SucceededLinks.Count -eq 0) {
        return
    }

    New-Item -ItemType Directory -Path $PostedLinksDir -Force | Out-Null

    $RunDate = Get-Date
    $Rows = foreach ($Item in $SucceededLinks) {
        [PSCustomObject]@{
            Date          = $RunDate.ToString('yyyy-MM-dd')
            Time          = $RunDate.ToString('HH:mm:ss')
            Account       = $SelectedAccount
            ScheduledTime = $SelectedScheduledTime
            GroupName     = $Item.Name
            GroupUrl      = $Item.GroupUrl
            PostUrl       = $Item.PostUrl
        }
    }

    $Rows | Export-Csv -Path $PostedLinksCsv -NoTypeInformation -Encoding UTF8 -Append
    Write-Host "[OK] Posted links saved: $PostedLinksCsv ($($Rows.Count) link)"
}

function Write-DailyRunLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SelectedAccount,

        [Parameter(Mandatory = $true)]
        [string]$SelectedScheduledTime,

        [Parameter(Mandatory = $true)]
        [string]$SummaryFile,

        [Parameter(Mandatory = $true)]
        [int]$ExitCode
    )

    New-Item -ItemType Directory -Path $DailyLogDir -Force | Out-Null

    $SummaryLines = @()
    if (Test-Path $SummaryFile) {
        $SummaryLines = Get-Content $SummaryFile -Encoding UTF8
    }

    $Counts = Get-SummaryCounts -SummaryLines $SummaryLines
    $FailedNotes = Get-FailedGroupNotes -SummaryLines $SummaryLines
    $SucceededLinks = Get-SucceededPostLinks -SummaryLines $SummaryLines
    $Timestamp = Get-Date
    $SafeTime = $SelectedScheduledTime.Replace(':', '')
    $LogFileName = "social_scheduler_${SelectedAccount}_${SafeTime}_$($Timestamp.ToString('yyyyMMdd_HHmmss')).txt"
    $LogPath = Join-Path $DailyLogDir $LogFileName

    $Lines = @(
        "Ten account + gio theo lich: account_$SelectedAccount - $SelectedScheduledTime",
        "Group dang thanh cong = $($Counts.Success)",
        "Group dang that bai = $($Counts.Failed)",
        "Process exit code = $ExitCode",
        'Note:'
    )

    if ($FailedNotes.Count -eq 0) {
        $Lines += 'Khong co group dang that bai'
    }
    else {
        foreach ($Item in $FailedNotes) {
            $Lines += "Ten group dang that bai: $($Item.Name)"
            $Lines += "Link group dang that bai: $($Item.Url)"
            if ($Item.Reason) {
                $Lines += "Ly do that bai: $($Item.Reason)"
            }
            $Lines += ''
        }
    }

    $Lines += 'Link bai viet da dang thanh cong:'
    if ($SucceededLinks.Count -eq 0) {
        $Lines += 'Khong co bai viet dang thanh cong'
    }
    else {
        foreach ($Item in $SucceededLinks) {
            $Lines += "Ten group: $($Item.Name)"
            if ($Item.PostUrl) {
                $Lines += "Link bai viet: $($Item.PostUrl)"
            }
            else {
                $Lines += 'Link bai viet: (khong lay duoc - co the dang cho duyet)'
            }
            $Lines += ''
        }
    }

    Set-Content -Path $LogPath -Value $Lines -Encoding UTF8
    Write-Host "[OK] Daily log saved: $LogPath"
}

function Invoke-SocialScheduler {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('linh', 'sang', 'chau')]
        [string]$SelectedAccount,

        [string]$SelectedScheduledTime = ''
    )

    $ResolvedPython = Resolve-PythonExe
    $AccountConfig = "config/account_$SelectedAccount.json"
    $LogScheduledTime = Resolve-ScheduledTimeForLog -Value $SelectedScheduledTime
    $SummaryFile = Join-Path $env:TEMP "social_scheduler_${SelectedAccount}_summary_$(Get-Random)_$(Get-Random).txt"

    if (-not (Test-Path (Join-Path $SocialDir $AccountConfig))) {
        throw "Account config not found: $AccountConfig"
    }

    # Mutex voi tool comment bump: ca 2 dung chung Chrome profile cua account.
    # Thu tu uu tien: dang bai di truoc, bump chay sau (bump tu cho khi thay poster lock).
    # Neu bump lo giu profile truoc (hiem), poster CHO bump ket thuc tu nhien (khong mat luot bump);
    # chi dung bump khi qua $BumpFinishWaitMinutes phut (= bump treo).
    New-Item -ItemType Directory -Path $LockDir -Force | Out-Null
    $PosterLock = Join-Path $LockDir "poster_$SelectedAccount.lock"
    $BumpLock = Join-Path $LockDir "bump_$SelectedAccount.lock"

    if (Test-Path $BumpLock) {
        $WaitUntil = (Get-Date).AddMinutes($BumpFinishWaitMinutes)
        while (Test-Path $BumpLock) {
            $BumpLockAge = (Get-Date) - (Get-Item $BumpLock).LastWriteTime
            if ($BumpLockAge.TotalMinutes -gt $LockStaleMinutes) {
                break
            }
            if ((Get-Date) -ge $WaitUntil) {
                break
            }
            Write-Host "[INFO] Comment bump account $SelectedAccount dang chay, poster cho bump ket thuc (toi da $BumpFinishWaitMinutes phut)..."
            Start-Sleep -Seconds 20
        }

        if (Test-Path $BumpLock) {
            Write-Host "[WARN] Comment bump account $SelectedAccount chay qua $BumpFinishWaitMinutes phut (treo), dung de giai phong profile cho lich dang bai."
            Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -match 'comment_bump_today' } |
                ForEach-Object {
                    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                    Write-Host "[OK] Da dung bump python PID=$($_.ProcessId)"
                }
            Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -match "profiles[\\/]account_$SelectedAccount" } |
                ForEach-Object {
                    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                    Write-Host "[OK] Da dung chrome profile account_$SelectedAccount PID=$($_.ProcessId)"
                }
            Remove-Item $BumpLock -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
        }
    }

    Set-Content -Path $PosterLock -Value (Get-Date).ToString('s') -Encoding UTF8

    Push-Location $SocialDir
    try {
        $env:SOCIAL_SCHEDULER_SUMMARY_FILE = $SummaryFile

        Write-Host "[INFO] Running Social scheduler for account=$SelectedAccount"
        Write-Host "[INFO] Scheduled time: $LogScheduledTime"
        Write-Host "[INFO] Working directory: $SocialDir"
        Write-Host "[INFO] Python: $ResolvedPython"

        $SchedulerOutput = & $ResolvedPython social_scheduler.py `
            --account-config $AccountConfig `
            --targets config/targets.json `
            --targets config/targets_admin.json `
            --posts config/posts.json `
            --performance-mode `
            --concurrency 2 2>&1

        $ExitCode = $LASTEXITCODE
        $SchedulerOutput | ForEach-Object { Write-Host $_ }

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

        Write-DailyRunLog `
            -SelectedAccount $SelectedAccount `
            -SelectedScheduledTime $LogScheduledTime `
            -SummaryFile $SummaryFile `
            -ExitCode $ExitCode

        $SucceededLinks = @()
        if (Test-Path $SummaryFile) {
            $SucceededLinks = Get-SucceededPostLinks -SummaryLines (Get-Content $SummaryFile -Encoding UTF8)
        }
        Save-PostedLinksCsv `
            -SelectedAccount $SelectedAccount `
            -SelectedScheduledTime $LogScheduledTime `
            -SucceededLinks $SucceededLinks

        exit $ExitCode
    }
    finally {
        if (Test-Path $SummaryFile) {
            Remove-Item $SummaryFile -Force -ErrorAction SilentlyContinue
        }
        Remove-Item Env:SOCIAL_SCHEDULER_SUMMARY_FILE -ErrorAction SilentlyContinue
        Remove-Item $PosterLock -Force -ErrorAction SilentlyContinue
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
        $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -Mode run -Account $($Item.Account) -ScheduledTime $($Item.Time)"
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
    'run' { Invoke-SocialScheduler -SelectedAccount $Account -SelectedScheduledTime $ScheduledTime }
    'list' { Show-SocialSchedulerTasks }
}