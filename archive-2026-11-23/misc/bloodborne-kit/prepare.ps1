# Installs Bloodborne for the Windows port (bbport) with no downloads: unpacks MSYS2 to C:\msys64
# and the already built port to C:\bbport, extracts the game and the v1.09 update from the two PS4
# .pkg files placed next to this script into C:\Games\<TITLE_ID>, then opens the port's setup window.
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'

$Kit = $PSScriptRoot
$Msys = 'C:\msys64'
$Repo = 'C:\bbport'
# Windows' own tar (bsdtar, Windows 10 1803 and newer); a Git or MSYS2 tar on PATH reads C: as a host.
$Tar = Join-Path $env:SystemRoot 'System32\tar.exe'
$PkgTool = Join-Path $Kit 'pkgtool\PkgTool.exe'
# Written into each folder this script fills: 'incomplete' while it works, 'complete' when done,
# so a run that was interrupted resumes and a folder made by something else is left alone.
$MarkerName = '.bloodborne-kit'

function Step($n, $text) { Write-Host "`n[$n/5] $text" -ForegroundColor Cyan }
function Fail($text) {
    Write-Host "`nERROR: $text" -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

# Native tools report failure through their exit code; text they write to stderr must not stop
# the script, which Windows PowerShell 5.1 does under 'Stop'.
function Invoke-Native($exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    & $exe @arguments | Out-Host
    $LASTEXITCODE
}

function Read-BE32($reader) {
    $b = $reader.ReadBytes(4)
    [Array]::Reverse($b)
    [BitConverter]::ToUInt32($b, 0)
}

# param.sfo is a plain entry (id 0x1000) in the PKG's entry table, outside the extracted files.
function Get-PkgSfo($path) {
    $fs = [IO.File]::OpenRead($path)
    $br = New-Object IO.BinaryReader($fs)
    try {
        $magic = $br.ReadBytes(4)
        if ($magic[0] -ne 0x7F -or [Text.Encoding]::ASCII.GetString($magic, 1, 3) -ne 'CNT') {
            throw "$path is not a PS4 .pkg file"
        }
        $fs.Position = 0x10; $count = Read-BE32 $br
        $fs.Position = 0x18; $table = Read-BE32 $br
        for ($i = 0; $i -lt $count; $i++) {
            $fs.Position = $table + 32 * $i
            $id = Read-BE32 $br
            $null = $br.ReadBytes(12)
            $offset = Read-BE32 $br
            $size = Read-BE32 $br
            if ($id -eq 0x1000) {
                $fs.Position = $offset
                return ,$br.ReadBytes($size)
            }
        }
        throw "no param.sfo inside $path"
    } finally { $br.Close() }
}

function Get-SfoValues($b) {
    if ([BitConverter]::ToUInt32($b, 0) -ne 0x46535000) { throw 'param.sfo is damaged' }
    $keyTable = [BitConverter]::ToUInt32($b, 8)
    $dataTable = [BitConverter]::ToUInt32($b, 12)
    $n = [BitConverter]::ToUInt32($b, 16)
    $values = @{}
    for ($i = 0; $i -lt $n; $i++) {
        $e = 0x14 + 16 * $i
        $k = $keyTable + [BitConverter]::ToUInt16($b, $e)
        $fmt = [BitConverter]::ToUInt16($b, $e + 2)
        $len = [BitConverter]::ToUInt32($b, $e + 4)
        $d = $dataTable + [BitConverter]::ToUInt32($b, $e + 12)
        $end = [Array]::IndexOf($b, [byte]0, [int]$k)
        $key = [Text.Encoding]::ASCII.GetString($b, $k, $end - $k)
        if ($fmt -eq 0x0404) { $values[$key] = [BitConverter]::ToUInt32($b, $d) }
        else { $values[$key] = [Text.Encoding]::UTF8.GetString($b, $d, $len).TrimEnd([char]0) }
    }
    $values
}

function Get-MarkerState($folder) {
    $marker = Join-Path $folder $MarkerName
    if (Test-Path -LiteralPath $marker) { return (Get-Content -LiteralPath $marker -Raw).Trim() }
    if ((Test-Path -LiteralPath $folder) -and (Get-ChildItem -LiteralPath $folder -Force | Select-Object -First 1)) {
        return 'foreign'
    }
    return 'missing'
}

function Set-MarkerState($folder, $state) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    Set-Content -LiteralPath (Join-Path $folder $MarkerName) -Value $state -Encoding ASCII
}

# payload\<name>.tar.gz holds one top folder named like $target's leaf; it is unpacked next to it.
function Expand-Payload($name, $target) {
    $state = Get-MarkerState $target
    if ($state -eq 'complete') { Write-Host "  Already unpacked: $target"; return }
    if ($state -eq 'foreign') {
        Fail "$target already exists and was not made by this kit. Rename or delete it, then run this again."
    }
    $archive = Join-Path $Kit "payload\$name.tar.gz"
    Set-MarkerState $target 'incomplete'
    if ((Invoke-Native $Tar @('-xzf', $archive, '-C', (Split-Path -Parent $target))) -ne 0) {
        Fail "Unpacking $archive failed. Free some space on C: and run this again."
    }
    Set-MarkerState $target 'complete'
}

Write-Host 'Bloodborne PC: installing everything offline. Do not close this window.' -ForegroundColor Yellow

# --- 1. Check the kit and the two .pkg files ------------------------------------------------------
Step 1 'Checking the kit and the .pkg files in this folder'
if (-not (Test-Path $Tar)) { Fail 'This Windows has no tar.exe: Windows 10 version 1803 or newer is needed.' }
foreach ($file in "payload\msys64.tar.gz", "payload\bbport.tar.gz", "pkgtool\PkgTool.exe", "pkgtool\LibOrbisPkg.dll") {
    if (-not (Test-Path (Join-Path $Kit $file))) { Fail "The kit is incomplete ($file is missing): unzip the whole zip again." }
}
$base = $null; $update = $null
foreach ($pkg in Get-ChildItem -LiteralPath $Kit -Filter *.pkg) {
    try { $sfo = Get-PkgSfo $pkg.FullName; $v = Get-SfoValues $sfo }
    catch { Fail $_.Exception.Message }
    Write-Host ("  {0}: {1} v{2} ({3})" -f $pkg.Name, $v['TITLE_ID'], $v['APP_VER'], $v['CATEGORY'])
    if ($v['CATEGORY'] -eq 'gd') { $base = @{ File = $pkg; Sfo = $v } }
    elseif ($v['CATEGORY'] -eq 'gp') { $update = @{ File = $pkg; Sfo = $v; Bytes = $sfo } }
}
if (-not $base) { Fail "The game .pkg (the big one, ~31 GB) is not in this folder: $Kit" }
if (-not $update) { Fail "The update .pkg (v1.09) is not in this folder: $Kit" }
if ($update.Sfo['APP_VER'] -ne '01.09') { Fail "The update is v$($update.Sfo['APP_VER']); the port needs v01.09." }
if ($update.Sfo['TITLE_ID'] -ne $base.Sfo['TITLE_ID']) { Fail 'The game and the update are from different regions.' }

$TitleId = $base.Sfo['TITLE_ID']
$Games = "C:\Games\$TitleId"
$GameDir = "$Games\uroot"

# GiB still needed on C: (3.8 for MSYS2; 0.8 for the port plus 0.4 its first start writes).
$needGB = 2
if ((Get-MarkerState $Games) -ne 'complete') { $needGB += [math]::Ceiling(($base.File.Length + $update.File.Length) / 1GB) }
if ((Get-MarkerState $Msys) -ne 'complete') { $needGB += 4 }
if ((Get-MarkerState $Repo) -ne 'complete') { $needGB += 2 }
$freeGB = [math]::Floor((Get-PSDrive C).Free / 1GB)
if ($freeGB -lt $needGB) { Fail "Drive C: has $freeGB GB free; about $needGB GB are needed." }
Write-Host "  OK: game $TitleId, update v01.09, $freeGB GB free on C: ($needGB GB needed)" -ForegroundColor Green
if ($CheckOnly) { Read-Host 'Check only: press Enter to close'; exit 0 }

# --- 2. MSYS2 ---------------------------------------------------------------------------------------
Step 2 "Unpacking the tools to $Msys (5-15 minutes)"
Expand-Payload 'msys64' $Msys
# Windows' tar drops the System attribute that marks MSYS2's one Cygwin-style symlink
# (/etc/mtab -> /proc/mounts); without it MSYS2 reads the link as a plain file.
(Get-Item -Force -LiteralPath "$Msys\etc\mtab").Attributes = 'System'
Write-Host '  OK' -ForegroundColor Green

# --- 3. The port --------------------------------------------------------------------------------------
Step 3 "Unpacking the port to $Repo (1-2 minutes)"
Expand-Payload 'bbport' $Repo
Write-Host '  OK' -ForegroundColor Green

# --- 4. The game and the update -------------------------------------------------------------------
Step 4 "Extracting the game to $Games (15-30 minutes, the window may look frozen)"
if ((Get-MarkerState $Games) -eq 'complete') {
    Write-Host '  The game is already extracted; skipping it.'
} else {
    Set-MarkerState $Games 'incomplete'
    $code = Invoke-Native $PkgTool @('pkg_extract', $base.File.FullName, $Games)
    if ($code -ne 0 -or -not (Test-Path "$GameDir\eboot.bin")) {
        Fail 'Extracting the game failed. If it mentions a passcode or keys, this .pkg cannot be used.'
    }
    Set-MarkerState $Games 'complete'
}
Write-Host '  Applying the v1.09 update...'
if ((Invoke-Native $PkgTool @('pkg_extract', $update.File.FullName, $Games)) -ne 0) { Fail 'Extracting the update failed.' }
[IO.File]::WriteAllBytes("$GameDir\sce_sys\param.sfo", $update.Bytes)
Write-Host '  OK' -ForegroundColor Green

# --- 5. Settings ------------------------------------------------------------------------------------
Step 5 'Opening the setup window'
# The port in C:\bbport is already built. Without BB_PREBUILT=1, run.bat runs the build check
# through MSYS2 before every start (scripts\run_windows.py). Set for this window, so the setup's
# Play button gets it, and for the Windows user, so the Desktop shortcut gets it too.
$env:BB_PREBUILT = '1'
[Environment]::SetEnvironmentVariable('BB_PREBUILT', '1', 'User')
Set-Content -LiteralPath "$Repo\out\game_dir.txt" -Value $GameDir -Encoding ASCII
Start-Process -FilePath "$Repo\setup.bat" -WorkingDirectory $Repo
Write-Host "`nDone. The game folder is already filled in the setup window:" -ForegroundColor Green
Write-Host "  $GameDir"
Write-Host 'Pick your settings and press "Save settings" (NOT "Install / Update"), then "Play".' -ForegroundColor Yellow
Read-Host 'Press Enter to close this window'
