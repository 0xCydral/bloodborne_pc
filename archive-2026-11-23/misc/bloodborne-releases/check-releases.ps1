# Daily check for new Bloodborne port releases (upstream and Supermedo's Windows builds). Shows a
# Windows notification when a repo's latest release tag differs from the one seen last time.
# State: %LOCALAPPDATA%\bbport-releases.json. Needs gh (logged in) on PATH.
$ErrorActionPreference = 'Stop'
$repos = 'deadinside28/bloodborne_pc', 'Supermedo/bloodborne_pc'
$stateFile = Join-Path $env:LOCALAPPDATA 'bbport-releases.json'
$state = @{}
if (Test-Path $stateFile) {
    (Get-Content $stateFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $state[$_.Name] = $_.Value }
}

$news = @()
foreach ($repo in $repos) {
    $latest = gh release view --repo $repo --json tagName,name,publishedAt,url | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $latest) { continue }
    if ($state[$repo] -ne $latest.tagName) {
        if ($state.ContainsKey($repo)) { $news += "$($latest.name) ($repo)" }
        $state[$repo] = $latest.tagName
    }
}
$state | ConvertTo-Json | Set-Content $stateFile -Encoding UTF8

if ($news) {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $icon = New-Object System.Windows.Forms.NotifyIcon
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.Visible = $true
    $icon.ShowBalloonTip(15000, 'New Bloodborne port release', ($news -join "`n"), 'Info')
    Start-Sleep -Seconds 16
    $icon.Dispose()
}
$news
