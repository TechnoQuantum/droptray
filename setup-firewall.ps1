# Lets phones on your home network reach DropTray on TCP 8765.
# Private networks only: on public Wi-Fi (cafés, hotels) the port stays closed.
# Run once, as administrator:  right-click → Run with PowerShell, or from an admin prompt.

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    return
}

Get-NetFirewallRule -DisplayName 'DropTray (TCP 8765)' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'DropTray (TCP 8765)' -Direction Inbound -Protocol TCP -LocalPort 8765 `
    -Profile Private -Action Allow -Description 'Receive files from phones with DropTray' | Out-Null
Get-NetFirewallRule -DisplayName 'DropTray Bonjour (UDP 5353)' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'DropTray Bonjour (UDP 5353)' -Direction Inbound -Protocol UDP -LocalPort 5353 `
    -Profile Private -Action Allow -Description 'Lets phones find DropTray as droptray.local' | Out-Null
Write-Host 'Firewall rules added: TCP 8765 for transfers, UDP 5353 for droptray.local (private networks only).'
Start-Sleep -Seconds 3
