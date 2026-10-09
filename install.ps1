# DropTray without the Setup wizard: download, verify, install for this user, start.
#
#   Install or update (PowerShell):
#     irm https://raw.githubusercontent.com/TechnoQuantum/droptray/main/install.ps1 | iex
#   Uninstall (settings and history stay in %APPDATA%\DropTray):
#     & ([scriptblock]::Create((irm https://raw.githubusercontent.com/TechnoQuantum/droptray/main/install.ps1))) -Uninstall
#   From a clone after building (uses dist\DropTray instead of downloading):
#     powershell -ExecutionPolicy Bypass -File install.ps1
#
# No administrator rights needed. Installs to %LOCALAPPDATA%\Programs\DropTray with a Start Menu shortcut.
param([switch]$Uninstall, [switch]$Firewall)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # Invoke-WebRequest is many times slower with the progress bar

$repo = 'TechnoQuantum/droptray'
$dest = Join-Path $env:LOCALAPPDATA 'Programs\DropTray'
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\DropTray.lnk'
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

function Stop-DropTray {
    Get-Process DropTray -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-CimInstance Win32_Process -Filter "Name like 'python%.exe'" |
        Where-Object CommandLine -like '*droptray.py*' |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 800
}

Stop-DropTray

if ($Uninstall) {
    $inno = Join-Path $dest 'unins000.exe'
    if (Test-Path $inno) {                      # installed by Setup.exe: let its uninstaller clean up
        Start-Process $inno -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
    }
    Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $lnk -Force -ErrorAction SilentlyContinue
    Remove-ItemProperty $run -Name DropTray -ErrorAction SilentlyContinue
    Write-Host 'DropTray removed. Settings and history remain in' (Join-Path $env:APPDATA 'DropTray')
    return
}

# Where the files come from: a local build next to this script, or the latest GitHub release.
$local = if ($PSScriptRoot) { Join-Path $PSScriptRoot 'dist\DropTray' } else { $null }
$work = $null
if ($local -and (Test-Path (Join-Path $local 'DropTray.exe'))) {
    $src = $local
    Write-Host "Installing the local build from $src"
} else {
    $base = if ($env:DROPTRAY_DOWNLOAD) { $env:DROPTRAY_DOWNLOAD } else { "https://github.com/$repo/releases/latest/download" }
    $work = Join-Path $env:TEMP ('droptray-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory $work | Out-Null
    $zip = Join-Path $work 'DropTray-win-x64.zip'
    Write-Host 'Downloading DropTray...'
    Invoke-WebRequest "$base/DropTray-win-x64.zip" -OutFile $zip -UseBasicParsing
    Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile (Join-Path $work 'SHA256SUMS.txt') -UseBasicParsing

    $line = Get-Content (Join-Path $work 'SHA256SUMS.txt') | Where-Object { $_ -match 'DropTray-win-x64\.zip' }
    $expected = ($line -split '\s+')[0].ToLower()
    $actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if (-not $expected -or $expected -ne $actual) {
        Remove-Item $work -Recurse -Force
        throw "Checksum mismatch for the download (expected $expected, got $actual). Nothing was installed."
    }
    Write-Host 'Checksum verified.'
    $src = Join-Path $work 'DropTray'
    Expand-Archive $zip -DestinationPath $src -Force
}

New-Item -ItemType Directory -Force $dest | Out-Null
robocopy $src $dest /MIR /XF unins000.exe unins000.dat /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying failed (robocopy exit $LASTEXITCODE)" }
if ($work) { Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue }

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = Join-Path $dest 'DropTray.exe'
$s.WorkingDirectory = $dest
$s.IconLocation = (Join-Path $dest 'DropTray.exe') + ',0'
$s.Description = 'Receive files from your iPhone'
$s.Save()

# If "Open at sign-in" is on, point it at this copy (the app also does this when it starts).
if (Get-ItemProperty $run -Name DropTray -ErrorAction SilentlyContinue) {
    Set-ItemProperty $run -Name DropTray -Value ('"' + (Join-Path $dest 'DropTray.exe') + '"')
}

if ($Firewall) {
    # Lets phones on private networks reach DropTray (TCP 8765, UDP 5353). Asks for administrator.
    $rules = @'
New-NetFirewallRule -DisplayName 'DropTray (TCP 8765)' -Direction Inbound -Protocol TCP -LocalPort 8765 -Profile Private -Action Allow | Out-Null
New-NetFirewallRule -DisplayName 'DropTray Bonjour (UDP 5353)' -Direction Inbound -Protocol UDP -LocalPort 5353 -Profile Private -Action Allow | Out-Null
'@
    Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile', '-Command', $rules
}

Start-Process (Join-Path $dest 'DropTray.exe')
Write-Host "DropTray installed to $dest and started. It's in the Start Menu as DropTray."
if (-not $Firewall) {
    Write-Host 'If your phone cannot connect, allow DropTray through the firewall once (asks for administrator):'
    Write-Host '  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/TechnoQuantum/droptray/main/install.ps1))) -Firewall'
}
