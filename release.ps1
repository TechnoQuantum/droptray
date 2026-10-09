# Builds a DropTray release, and publishes it with -Publish. The version is APP_VERSION in core.py.
#
#   powershell -ExecutionPolicy Bypass -File release.ps1                      build into release\
#   powershell -ExecutionPolicy Bypass -File release.ps1 -Publish            build, commit, tag, push, GitHub release
#   powershell -ExecutionPolicy Bypass -File release.ps1 -Publish -Notes "What changed"
#
# Needs: .venv with the build requirements, Inno Setup 6, and (to publish) the GitHub CLI logged in.
# Installed copies of DropTray find the new release by themselves and update within a few hours.
param([switch]$Publish, [string]$Notes = '')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$py = '.venv\Scripts\python.exe'
$iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
if (-not (Test-Path $iscc)) { $iscc = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' }
$gh = 'C:\Program Files\GitHub CLI\gh.exe'

$version = [regex]::Match((Get-Content core.py -Raw), 'APP_VERSION\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $version) { throw 'APP_VERSION not found in core.py' }
$tag = "v$version"
Write-Host "DropTray $version" -ForegroundColor Cyan

if ($Publish) {
    if (git status --porcelain -- . ':!packaging/winget') { throw 'Commit or stash your changes first: the release is built from what is committed.' }
    if (git tag --list $tag) { throw "Tag $tag exists already. Raise APP_VERSION in core.py." }
}

Write-Host 'Tests'
& $py tests\test_multipart.py; if ($LASTEXITCODE) { throw 'tests failed' }
& $py tests\test_store.py; if ($LASTEXITCODE) { throw 'tests failed' }

Write-Host 'App (PyInstaller)'
& $py -m PyInstaller DropTray.spec --noconfirm --log-level WARN; if ($LASTEXITCODE) { throw 'PyInstaller failed' }

Write-Host 'Installer (Inno Setup)'
& $iscc /Q "/DAppVersion=$version" installer\DropTray.iss; if ($LASTEXITCODE) { throw 'Inno Setup failed' }

Write-Host 'Release files'
Remove-Item release -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory release | Out-Null
Copy-Item installer\Output\DropTraySetup.exe release\
Compress-Archive -Path 'dist\DropTray\*' -DestinationPath release\DropTray-win-x64.zip
$sums = foreach ($f in 'DropTraySetup.exe', 'DropTray-win-x64.zip') {
    '{0}  {1}' -f (Get-FileHash "release\$f" -Algorithm SHA256).Hash.ToLower(), $f
}
$sums | Set-Content release\SHA256SUMS.txt -Encoding ascii
$sums | ForEach-Object { Write-Host "  $_" }

Write-Host 'winget manifest'
$wg = 'packaging\winget\manifests\t\TechnoQuantum\DropTray'
$prev = Get-ChildItem $wg -Directory | Sort-Object { [version]($_.Name -replace '[^0-9.]', '') } | Select-Object -Last 1
$new = Join-Path $wg $version
if ($prev.FullName -ne (Resolve-Path -LiteralPath $new -ErrorAction SilentlyContinue).Path) {
    Copy-Item $prev.FullName $new -Recurse -Force
}
$setupHash = (Get-FileHash release\DropTraySetup.exe -Algorithm SHA256).Hash
foreach ($file in Get-ChildItem $new -Filter *.yaml) {
    $text = Get-Content $file.FullName -Raw
    $text = $text -replace '(?m)^PackageVersion: .*$', "PackageVersion: $version"
    $text = $text -replace 'releases/download/v[^/]+/', "releases/download/$tag/"
    $text = $text -replace 'releases/tag/v[^\s]+', "releases/tag/$tag"
    $text = $text -replace '(?m)^(\s*InstallerSha256: ).*$', "`${1}$setupHash"
    Set-Content $file.FullName $text -NoNewline -Encoding utf8
}

if (-not $Publish) {
    Write-Host "Built in release\. Publish with: release.ps1 -Publish" -ForegroundColor Green
    return
}

Write-Host 'Publishing'
git add $new
git commit -m "Release $version" -m "winget manifest for $version." | Out-Null
git tag -a $tag -m "DropTray $version"
git push origin HEAD --follow-tags
if (-not $Notes) { $Notes = "DropTray $version. Installed copies update themselves; new users: download DropTraySetup.exe below." }
$Notes += "`n`n**Install:** run **DropTraySetup.exe**, or ``irm https://raw.githubusercontent.com/TechnoQuantum/droptray/main/install.ps1 | iex``.`n`n``SHA256SUMS.txt`` lists the checksums of both downloads."
& $gh release create $tag release\DropTraySetup.exe release\DropTray-win-x64.zip release\SHA256SUMS.txt --title "DropTray $version" --notes $Notes
Write-Host "Published $tag" -ForegroundColor Green
