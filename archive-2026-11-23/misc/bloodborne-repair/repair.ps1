# Fixes "Bloodborne does not open any more" without reinstalling. On the friend's PC the game
# started with an empty GPU pipeline cache and stopped starting once a session had filled it (the
# cache in C:\bbport\user\cache is compiled again at every start); with an upscaler it crashed
# while loading, with the upscaler Off it played. So this moves the cache and bbport.ini aside,
# turns the pipeline cache off (BB_PIPELINE_CACHE=0) and starts the settings with the upscaler
# Off. Saves, game and DLC stay. A report on the Desktop helps find the cause.
# -Root and -Report exist for testing.
param([string]$Root = 'C:\bbport', [string]$Report = '')

$ErrorActionPreference = 'Stop'
function Fail($text) {
    Write-Host "`nERROR: $text" -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

Write-Host 'Bloodborne PC: repairing the game start.' -ForegroundColor Yellow
$probe = Join-Path $Root 'out\bb-probe.exe'
if (-not (Test-Path -LiteralPath $probe)) { Fail "Bloodborne is not installed in $Root." }
if (Get-Process -Name bb-probe -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $probe }) {
    Fail 'Close Bloodborne first, then run this again.'
}

# --- 1. Report (graphics card, driver, the settings that stopped working) --------------------------
if (-not $Report) { $Report = Join-Path ([Environment]::GetFolderPath('Desktop')) 'bloodborne-report.txt' }
$os = Get-CimInstance Win32_OperatingSystem
$lines = @("Bloodborne repair report, $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
           "Windows: $($os.Caption) $($os.Version)")
foreach ($g in Get-CimInstance Win32_VideoController) { $lines += "GPU: $($g.Name), driver $($g.DriverVersion)" }
# Video memory from the display driver keys (WMI's AdapterRAM stops at 4 GB).
$class = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
foreach ($k in Get-ChildItem $class -ErrorAction SilentlyContinue) {
    $p = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
    $mem = $p.'HardwareInformation.qwMemorySize'
    if ($mem) { $lines += ('VRAM: {0} = {1:N1} GB' -f $p.DriverDesc, ($mem / 1GB)) }
}
foreach ($n in 'BB_ADDCONT', 'BB_PREBUILT', 'BB_MSYS2', 'BB_PIPELINE_CACHE') {
    $lines += "$n=" + [Environment]::GetEnvironmentVariable($n, 'User')
}
$cache = Join-Path $Root 'user\cache'
$size = if (Test-Path -LiteralPath $cache) {
    '{0:N1} MB' -f ((Get-ChildItem -LiteralPath $cache -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
} else { 'none' }
$lines += "GPU cache: $size"
$ini = Join-Path $Root 'bbport.ini'
$lines += '--- bbport.ini before the repair'
$lines += if (Test-Path -LiteralPath $ini) { Get-Content -LiteralPath $ini } else { '(none)' }
$cmd = Join-Path $Root 'Bloodborne.cmd'
$lines += '--- Bloodborne.cmd'
$lines += if (Test-Path -LiteralPath $cmd) { Get-Content -LiteralPath $cmd } else { '(none)' }
Set-Content -LiteralPath $Report -Value $lines -Encoding UTF8
Write-Host "  Report: $Report"

# --- 2. Move the GPU cache and the settings aside (nothing is deleted) ---------------------------
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (Test-Path -LiteralPath $cache) {
    Rename-Item -LiteralPath $cache -NewName "cache-before-repair-$stamp"
    Write-Host "  GPU cache moved to user\cache-before-repair-$stamp (the game builds a new one)"
}
if (Test-Path -LiteralPath $ini) {
    Rename-Item -LiteralPath $ini -NewName "bbport.ini.before-repair-$stamp"
    Write-Host "  Settings moved to bbport.ini.before-repair-$stamp"
}

# --- 3. No pipeline cache, upscaler Off (what let the game play) ----------------------------------
# For this window too, so the setup's Play button gets it.
$env:BB_PIPELINE_CACHE = '0'
[Environment]::SetEnvironmentVariable('BB_PIPELINE_CACHE', '0', 'User')
Set-Content -LiteralPath $ini -Value 'upscaler=off' -Encoding ASCII
Write-Host '  GPU pipeline cache turned off (BB_PIPELINE_CACHE=0); upscaler set to Off'

# --- 4. The rest of the settings from the setup window ----------------------------------------------
Start-Process -FilePath (Join-Path $Root 'setup.bat') -WorkingDirectory $Root
Write-Host "`nDone. In the setup window leave Upscaler on ""Off"", press ""Save settings"" (NOT ""Install / Update""), then ""Play""." -ForegroundColor Green
Write-Host 'Without the cache the game may stutter for a moment the first time it shows a new area.'
Write-Host 'Keep the upscaler Off for now, also in the game menu.'
Write-Host 'If it crashes, a black window stays open with text: take a photo of it.'
Write-Host 'Send bloodborne-report.txt from your Desktop to the person who gave you the kit.'
Read-Host 'Press Enter to close this window'
