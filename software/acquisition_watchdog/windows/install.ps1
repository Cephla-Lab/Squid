# Run in PowerShell (as the user who runs the Squid GUI), from software\ :
#   .\acquisition_watchdog\windows\install.ps1
# Registers a logon-triggered scheduled task that runs the acquisition watchdog.
# Edit $workingDir / $configPath below if your install path differs.
$ErrorActionPreference = "Stop"

$taskName   = "SquidAcquisitionWatchdog"
$workingDir = "C:\Squid\software"
$configPath = "C:\Squid\software\configuration.ini"

$action = New-ScheduledTaskAction `
    -Execute "pythonw.exe" `
    -Argument "-m acquisition_watchdog --config `"$configPath`"" `
    -WorkingDirectory $workingDir

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 999 `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Write-Host "Registering scheduled task '$taskName'..."
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Squid acquisition watchdog (alerts on prematurely-ended acquisitions)" `
    -Force

Write-Host "Done. It starts at next logon. Run now with: Start-ScheduledTask -TaskName $taskName"
