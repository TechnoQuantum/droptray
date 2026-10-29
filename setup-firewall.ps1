# Lets phones on your home network reach DropTray, on whichever TCP port it uses.
# Private networks only: on public Wi-Fi (cafés, hotels) the port stays closed.
# Run once, as administrator:  right-click → Run with PowerShell, or from an admin prompt.

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    return
}

# Allow DropTray.exe itself, on whatever port it picks (it moves off a port another app takes).
# Run from source there is no exe, so allow the port in config.json instead.
Get-NetFirewallRule -DisplayName 'DropTray (TCP*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
$exe = Join-Path $PSScriptRoot 'DropTray.exe'
if (Test-Path $exe) {
    New-NetFirewallRule -DisplayName 'DropTray (TCP, any port)' -Direction Inbound -Protocol TCP -Program $exe `
        -Profile Private -Action Allow -Description 'Receive files from phones with DropTray' | Out-Null
    $what = 'DropTray.exe on any TCP port'
} else {
    $port = 47820
    try { $port = [int](Get-Content "$env:APPDATA\DropTray\config.json" -Raw | ConvertFrom-Json).port } catch {}
    if ($port -le 0) { $port = 47820 }
    New-NetFirewallRule -DisplayName "DropTray (TCP $port)" -Direction Inbound -Protocol TCP -LocalPort $port `
        -Profile Private -Action Allow -Description 'Receive files from phones with DropTray' | Out-Null
    $what = "TCP $port"
}
Get-NetFirewallRule -DisplayName 'DropTray Bonjour (UDP 5353)' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'DropTray Bonjour (UDP 5353)' -Direction Inbound -Protocol UDP -LocalPort 5353 `
    -Profile Private -Action Allow -Description 'Lets phones find DropTray as droptray.local' | Out-Null
Write-Host "Firewall rules added: $what for transfers, UDP 5353 for droptray.local (private networks only)."
Start-Sleep -Seconds 3
