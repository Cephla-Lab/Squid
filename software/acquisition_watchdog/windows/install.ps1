# Run in PowerShell from software\ :  .\acquisition_watchdog\windows\install.ps1
$ErrorActionPreference = "Stop"
$taskName = "SquidAcquisitionWatchdog"
$xmlPath  = Join-Path $PSScriptRoot "squid-acquisition-watchdog.xml"
Write-Host "Registering scheduled task '$taskName' from $xmlPath"
Register-ScheduledTask -TaskName $taskName -Xml (Get-Content $xmlPath -Raw) -Force
Write-Host "Done. Edit the task's --config/WorkingDirectory if your install path differs, then log off/on or 'Start' the task."
