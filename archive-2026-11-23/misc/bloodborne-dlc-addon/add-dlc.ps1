# Adds The Old Hunters to a Bloodborne install made with bloodborne-kit. The DLC areas already ship
# with the v1.09 game data and the DLC .pkg is only a license, so this copies the port build that
# reports add-on licenses (bb-probe.exe, built from src\runtime_content.c on branch dlc-old-hunters)
# into C:\bbport and turns on the license read from the DLC .pkg placed next to this script.
# -Root exists for testing; the kit always installs to C:\bbport.
param([string]$Root = 'C:\bbport')

$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot

function Fail($text) {
    Write-Host "`nERROR: $text" -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

function Read-BE32($reader) {
    $b = $reader.ReadBytes(4)
    [Array]::Reverse($b)
    [BitConverter]::ToUInt32($b, 0)
}

# param.sfo is a plain entry (id 0x1000) in the PKG's entry table.
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

Write-Host 'Bloodborne PC: adding The Old Hunters.' -ForegroundColor Yellow

# --- 1. The install ---------------------------------------------------------------------------------
$probe = Join-Path $Root 'out\bb-probe.exe'
if (-not (Test-Path -LiteralPath $probe)) {
    Fail "Bloodborne is not installed in $Root yet. Run 1-START-HERE.bat from bloodborne-kit first, then this."
}
foreach ($file in 'bb-probe.exe', 'src\runtime_content.c') {
    if (-not (Test-Path -LiteralPath (Join-Path $Here $file))) { Fail "This add-on is incomplete ($file is missing): unzip it again." }
}
if (Get-Process -Name bb-probe -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $probe }) {
    Fail 'Close Bloodborne first, then run this again.'
}

# --- 2. The license, from the DLC .pkg (the game reads the last two digits of its label) ------------
$labels = @()
foreach ($pkg in Get-ChildItem -LiteralPath $Here -Filter *.pkg) {
    try { $v = Get-SfoValues (Get-PkgSfo $pkg.FullName) } catch { Fail $_.Exception.Message }
    if ($v['CATEGORY'] -ne 'ac') { Write-Host "  $($pkg.Name): not a DLC, skipped"; continue }
    $label = ($v['CONTENT_ID'] -split '-')[-1]
    if ($label -notmatch '^[A-Z0-9]{14}[0-9]{2}$') { Fail "$($pkg.Name): unexpected DLC label '$label'." }
    Write-Host "  $($pkg.Name): $($v['TITLE']), license $label"
    $labels += $label
}
if (-not $labels) { Fail "No DLC .pkg in this folder. Put the DLC .pkg (The Old Hunters) next to 2-ADD-DLC.bat." }

# --- 3. The port build that reports add-on licenses -----------------------------------------------
$backup = "$probe.before-dlc"
if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $probe -Destination $backup }
Copy-Item -LiteralPath (Join-Path $Here 'bb-probe.exe') -Destination $probe -Force
# The matching source, so a rebuild keeps the feature.
Copy-Item -LiteralPath (Join-Path $Here 'src\runtime_content.c') -Destination (Join-Path $Root 'src\runtime_content.c') -Force

# --- 4. Turn the license on; BB_PREBUILT keeps run.bat from rebuilding before every start ---------
$value = $labels -join ','
[Environment]::SetEnvironmentVariable('BB_ADDCONT', $value, 'User')
[Environment]::SetEnvironmentVariable('BB_PREBUILT', '1', 'User')
Write-Host "`nDone: The Old Hunters license ($value) is on." -ForegroundColor Green
Write-Host 'Start Bloodborne from the Desktop shortcut. The title screen looks the same as before.'
Write-Host 'The DLC opens after Vicar Amelia: the Messengers in the Hunter''s Dream give you the Eye of a Blood-drunk Hunter.'
Read-Host 'Press Enter to close'
