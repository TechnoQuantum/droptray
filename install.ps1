# Installs the built DropTray (dist\DropTray) for the current user, no administrator needed:
#   %LOCALAPPDATA%\Programs\DropTray\DropTray.exe, plus a Start Menu shortcut.
# Settings and history stay in %APPDATA%\DropTray, so reinstalling or updating keeps them.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1             install or update, then start it
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall  remove it (keeps settings and history)
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$dest = Join-Path $env:LOCALAPPDATA 'Programs\DropTray'
$src = Join-Path $PSScriptRoot 'dist\DropTray'
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\DropTray.lnk'
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

# Stop any running copy: the packaged one, or a development one started from the script.
Get-Process DropTray -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name like 'python%.exe'" |
    Where-Object CommandLine -like '*droptray.py*' |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

if ($Uninstall) {
    Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $lnk -Force -ErrorAction SilentlyContinue
    Remove-ItemProperty $run -Name DropTray -ErrorAction SilentlyContinue
    Write-Host 'DropTray removed. Settings and history remain in' (Join-Path $env:APPDATA 'DropTray')
    return
}

if (-not (Test-Path (Join-Path $src 'DropTray.exe'))) {
    throw "No build found at $src. Build first: .venv\Scripts\python.exe -m PyInstaller DropTray.spec --noconfirm"
}
New-Item -ItemType Directory -Force $dest | Out-Null
robocopy $src $dest /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying failed (robocopy exit $LASTEXITCODE)" }

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = Join-Path $dest 'DropTray.exe'
$s.WorkingDirectory = $dest
$s.IconLocation = (Join-Path $dest 'DropTray.exe') + ',0'
$s.Description = 'Receive files from your iPhone'
$s.Save()

# If "Open at sign-in" was on, point it at the installed copy (the app also does this when it starts).
if (Get-ItemProperty $run -Name DropTray -ErrorAction SilentlyContinue) {
    Set-ItemProperty $run -Name DropTray -Value ('"' + (Join-Path $dest 'DropTray.exe') + '"')
}

Start-Process (Join-Path $dest 'DropTray.exe')
Write-Host "DropTray installed to $dest and started. Find it in the Start Menu as DropTray."
