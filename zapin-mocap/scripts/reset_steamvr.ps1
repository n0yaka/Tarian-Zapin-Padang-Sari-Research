<#
reset_steamvr.ps1
Cleans up a stuck SteamVR session so it can be started again without rebooting.

Use it when SteamVR says it is already running, won't start, or errors right after starting.
It replaces "end every vr*.exe in Task Manager".

HOW TO RUN (PowerShell, from the scripts folder):
    powershell -ExecutionPolicy Bypass -File .\reset_steamvr.ps1            # SteamVR + adb
    powershell -ExecutionPolicy Bypass -File .\reset_steamvr.ps1 -AlsoAlvr  # also close the ALVR dashboard

What it does:
  1. Stops the adb server cleanly (ALVR uses adb for the USB connection).
  2. Force-closes every SteamVR process (vrserver, vrmonitor, vrcompositor, ...), plus leftover adb.
  3. Waits, then lists anything still running. If something cannot be closed, only a reboot will clear it.
  4. Checks that Steam itself is still running (Steam has been crashing when SteamVR closes).

It does not touch VIVE Hub or its services.
#>
param([switch]$AlsoAlvr)

$steamVrNames = 'vrserver','vrmonitor','vrcompositor','vrdashboard','vrwebhelper','vrstartup',
                'vrserverhelper','vrprismhost','vrpathreg','vrcmd'
$names = $steamVrNames + 'adb'
if ($AlsoAlvr) { $names += 'ALVR Dashboard' }

# 1. Stop adb politely first, so it releases the USB connection to the headset.
$adbPaths = @("$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe", "$env:USERPROFILE\ALVR\platform-tools\adb.exe")
foreach ($adb in $adbPaths) {
    if (Test-Path $adb) { & $adb kill-server 2>$null | Out-Null; break }
}

# 2. Force-close everything on the list, including child processes.
$found = Get-Process -ErrorAction SilentlyContinue | Where-Object { $names -contains $_.ProcessName }
if (-not $found) {
    Write-Host "No SteamVR processes were running."
} else {
    Write-Host "Closing:" ($found | ForEach-Object { "$($_.ProcessName) ($($_.Id))" }) -Separator "`n  "
    foreach ($p in $found) { taskkill /PID $p.Id /T /F 2>$null | Out-Null }
}

# 3. Give Windows a few seconds, then see what survived.
Start-Sleep -Seconds 4
$left = Get-Process -ErrorAction SilentlyContinue | Where-Object { $names -contains $_.ProcessName }
if ($left) {
    Write-Host "`nSTILL RUNNING after force-close:" -ForegroundColor Red
    $left | ForEach-Object { Write-Host "  $($_.ProcessName) ($($_.Id))" -ForegroundColor Red }
    Write-Host "These are stuck. Restart the laptop before starting SteamVR again." -ForegroundColor Red
} else {
    Write-Host "`nAll SteamVR processes are closed." -ForegroundColor Green
}

# 4. Steam must be running for SteamVR to start.
if (-not (Get-Process -Name steam -ErrorAction SilentlyContinue)) {
    Write-Host "Steam is not running (it may have crashed). Start Steam first, then SteamVR." -ForegroundColor Yellow
} else {
    Write-Host "Steam is running. You can start SteamVR (or ALVR > Launch SteamVR) now."
}
