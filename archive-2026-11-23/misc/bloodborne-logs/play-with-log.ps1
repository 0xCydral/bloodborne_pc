# Plays Bloodborne the way the Desktop shortcut does and keeps everything a crash leaves behind,
# in one zip on the Desktop: the game's whole console output (its crash report and GPU errors),
# the Windows crash and graphics-driver events of the session, and the PC and its settings.
# -Root and -OutDir exist for testing.
param([string]$Root = 'C:\bbport', [string]$OutDir = '')

$ErrorActionPreference = 'Stop'
function Fail($text) {
    Write-Host "`nERROR: $text" -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

$probe = Join-Path $Root 'out\bb-probe.exe'
if (-not (Test-Path -LiteralPath $probe)) { Fail "Bloodborne is not installed in $Root." }
if (Get-Process -Name bb-probe -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $probe }) {
    Fail 'Close Bloodborne first, then run this again.'
}
if (-not $OutDir) { $OutDir = [Environment]::GetFolderPath('Desktop') }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dir = Join-Path $OutDir "bloodborne-logs-$stamp"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# --- 1. The PC and the settings --------------------------------------------------------------------
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$info = @("Bloodborne log, $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
          "Windows: $($os.Caption) $($os.Version)",
          "CPU: $($cpu.Name)",
          ('RAM: {0:N1} GB' -f ($os.TotalVisibleMemorySize / 1MB)))
foreach ($g in Get-CimInstance Win32_VideoController) { $info += "GPU: $($g.Name), driver $($g.DriverVersion), $($g.CurrentHorizontalResolution)x$($g.CurrentVerticalResolution)" }
# Video memory from the display driver keys (WMI's AdapterRAM stops at 4 GB).
$class = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
foreach ($k in Get-ChildItem $class -ErrorAction SilentlyContinue) {
    $p = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
    $mem = $p.'HardwareInformation.qwMemorySize'
    if ($mem) { $info += ('VRAM: {0} = {1:N1} GB' -f $p.DriverDesc, ($mem / 1GB)) }
}
foreach ($n in 'BB_ADDCONT', 'BB_PREBUILT', 'BB_MSYS2', 'BB_PIPELINE_CACHE') {
    $info += "$n (user) = " + [Environment]::GetEnvironmentVariable($n, 'User')
}
$cache = Join-Path $Root 'user\cache'
if (Test-Path -LiteralPath $cache) {
    $info += ('GPU cache: {0:N1} MB' -f ((Get-ChildItem -LiteralPath $cache -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
}
foreach ($file in 'bbport.ini', 'Bloodborne.cmd') {
    $info += "--- $file"
    $path = Join-Path $Root $file
    $info += if (Test-Path -LiteralPath $path) { Get-Content -LiteralPath $path } else { '(none)' }
}
$systemFile = Join-Path $dir 'system.txt'
Set-Content -LiteralPath $systemFile -Value $info -Encoding UTF8

# --- 2. The same environment as the shortcut (Bloodborne.cmd's set lines) -------------------------
$launcher = Join-Path $Root 'Bloodborne.cmd'
if (Test-Path -LiteralPath $launcher) {
    foreach ($line in Get-Content -LiteralPath $launcher) {
        if ($line -match '^\s*set "([^=]+)=(.*)"\s*$') { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] }
    }
}

# --- 3. Play, with every line the game prints going to game-log.txt -------------------------------
$log = Join-Path $dir 'game-log.txt'
Write-Host 'Bloodborne is starting. Play until it crashes, or close it.' -ForegroundColor Yellow
Write-Host 'Leave this window open: it collects the logs when the game ends.'
$start = Get-Date
$game = Start-Process cmd.exe -ArgumentList "/S /C """"$Root\run.bat"" > ""$log"" 2>&1""" `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru -Wait
$seconds = [int]((Get-Date) - $start).TotalSeconds
Add-Content -LiteralPath $systemFile -Value @('--- session', "Game exit code: $($game.ExitCode) after $seconds s") -Encoding UTF8

# --- 4. What Windows recorded during the session (crashes, graphics driver resets) ----------------
$events = @()
try {
    $events += Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = $start } -ErrorAction Stop |
        Where-Object { $_.ProviderName -match 'Application Error|Windows Error Reporting|Application Hang' -and $_.Message -match 'bb-probe|python' }
} catch { }
try {
    $events += Get-WinEvent -FilterHashtable @{ LogName = 'System'; StartTime = $start } -ErrorAction Stop |
        Where-Object { $_.ProviderName -match 'Display|nvlddmkm|amdkmdag|amdwddmg|igfx|dxgkrnl' -or $_.Id -eq 4101 }
} catch { }
$eventText = if ($events) {
    $events | Sort-Object TimeCreated | ForEach-Object { "=== $($_.TimeCreated) $($_.LogName)/$($_.ProviderName) id $($_.Id)"; $_.Message; '' }
} else { 'No crash or graphics driver events recorded by Windows during the session.' }
Set-Content -LiteralPath (Join-Path $dir 'windows-events.txt') -Value $eventText -Encoding UTF8

# --- 5. One zip to send ----------------------------------------------------------------------------
$zip = "$dir.zip"
Compress-Archive -Path "$dir\*" -DestinationPath $zip -Force
Write-Host "`nLast lines of the game log:" -ForegroundColor Cyan
Get-Content -LiteralPath $log -Tail 15 -ErrorAction SilentlyContinue
Write-Host "`nDone. Send this file: $zip" -ForegroundColor Green
Start-Process explorer.exe -ArgumentList "/select,""$zip"""
Read-Host 'Press Enter to close this window'
