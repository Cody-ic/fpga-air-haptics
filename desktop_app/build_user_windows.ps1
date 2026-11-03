param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Use 64-bit Windows to build the user executable.' }
$destination = Join-Path $PSScriptRoot 'dist\user'
$cache = Join-Path $PSScriptRoot 'build\user'
& $Python -m PyInstaller --noconfirm --clean --workpath $cache --distpath $destination (Join-Path $PSScriptRoot 'touchsee_user.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
& $Python (Join-Path $PSScriptRoot 'package_user_release.py')
if ($LASTEXITCODE -ne 0) { throw 'User package creation failed.' }
Write-Output "User release: $destination"
