$names = @(
    'Badminton Social Scheduler - sang - 1400',
    'Badminton Social Scheduler - sang - 1800',
    'Badminton Social Comment Bump - slot 1200',
    'Badminton Social Comment Bump - slot 1400'
)
foreach ($name in $names) {
    $info = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Get-ScheduledTaskInfo
    if ($info) {
        Write-Host ("{0}`n  LastRun={1}  LastResult={2}  NextRun={3}" -f $name, $info.LastRunTime, $info.LastTaskResult, $info.NextRunTime)
    }
}
