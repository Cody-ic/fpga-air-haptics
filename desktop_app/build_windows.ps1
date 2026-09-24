param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'Build this Windows executable on Windows with 64-bit Python.'
}
$appDirectory = $PSScriptRoot
$buildDirectory = Join-Path $appDirectory 'build'
$distDirectory = Join-Path $appDirectory 'dist'

@'
import sys, struct, PyInstaller
assert sys.version_info >= (3, 10) and struct.calcsize('P') == 8, 'Use 64-bit Python 3.10 or newer'
'@ | & $Python -
if ($LASTEXITCODE -ne 0) { throw 'Install requirements-build.txt into a 64-bit Python environment first.' }
& $Python -m PyInstaller --noconfirm --clean --workpath $buildDirectory --distpath $distDirectory (Join-Path $appDirectory 'touchsee.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
& $Python (Join-Path $appDirectory 'package_release.py')
if ($LASTEXITCODE -ne 0) { throw 'Release archive creation failed.' }
Write-Output "Windows executable and portable ZIP are in: $distDirectory"
