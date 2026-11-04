param(
    [string]$IdeRoot = 'D:\tools\ArduinoIDE',
    [string]$DataRoot = 'D:\ArduinoData'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ideVersion = '2.3.10'
$coreVersion = '5.0.0'
$indexUrl = 'https://siliconlabs.github.io/arduino/package_arduinosilabs_index.json'
foreach ($subdir in @('downloads', 'packages-data', 'sketchbook', 'build-cache', 'build', 'logs')) {
    New-Item -ItemType Directory -Path (Join-Path $DataRoot $subdir) -Force | Out-Null
}
if (-not (Test-Path -LiteralPath (Join-Path $IdeRoot 'Arduino IDE.exe'))) {
    $archive = Join-Path $DataRoot "downloads\arduino-ide_$($ideVersion)_Windows_64bit.zip"
    Write-Host "Downloading Arduino IDE $ideVersion to $archive"
    & python (Join-Path $PSScriptRoot 'download.py') "https://downloads.arduino.cc/arduino-ide/arduino-ide_$($ideVersion)_Windows_64bit.zip" $archive
    if ($LASTEXITCODE -ne 0) { throw 'IDE download failed.' }
    New-Item -ItemType Directory -Path $IdeRoot -Force | Out-Null
    Write-Host "Extracting IDE into $IdeRoot"
    Expand-Archive -LiteralPath $archive -DestinationPath $IdeRoot -Force
}
$cli = Get-ChildItem -LiteralPath $IdeRoot -Recurse -Filter arduino-cli.exe | Select-Object -First 1 -ExpandProperty FullName
if (-not $cli) { throw 'Bundled arduino-cli.exe was not found.' }
$config = Join-Path $DataRoot 'arduino-cli.yaml'
$yaml = @"
board_manager:
  additional_urls:
    - $indexUrl
directories:
  data: $DataRoot\packages-data
  downloads: $DataRoot\downloads
  user: $DataRoot\sketchbook
build_cache:
  path: $DataRoot\build-cache
"@
Set-Content -LiteralPath $config -Value $yaml -Encoding utf8
$ideConfigDir = Join-Path $env:USERPROFILE '.arduinoIDE'
New-Item -ItemType Directory -Path $ideConfigDir -Force | Out-Null
$ideConfig = Join-Path $ideConfigDir 'arduino-cli.yaml'
if (Test-Path -LiteralPath $ideConfig) {
    $backup = "$ideConfig.before-mg24"
    if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $ideConfig -Destination $backup }
}
Copy-Item -LiteralPath $config -Destination $ideConfig -Force
Write-Host 'Installing the pinned Silicon Labs board package (no device is flashed).'
# Fetch the vendor index directly: some proxies terminate the CLI's HEAD request.
$index = Invoke-RestMethod $indexUrl
[System.IO.File]::WriteAllText((Join-Path $DataRoot 'packages-data\package_arduinosilabs_index.json'), ($index | ConvertTo-Json -Depth 40), (New-Object System.Text.UTF8Encoding $false))
if (-not (Test-Path -LiteralPath (Join-Path $DataRoot 'packages-data\package_index.json'))) {
    & $cli --config-file $config core update-index
    if ($LASTEXITCODE -ne 0) { throw 'Arduino base package index update failed.' }
}
# Populate the official package cache with resumable, checksum-verified downloads.
$package = $index.packages | Where-Object name -eq 'SiliconLabs'
$platform = $package.platforms | Where-Object version -eq $coreVersion
$archives = @($platform)
foreach ($dependency in $platform.toolsDependencies) {
    $tool = $package.tools | Where-Object { $_.name -eq $dependency.name -and $_.version -eq $dependency.version }
    $archives += $tool.systems | Where-Object host -eq 'i686-mingw32' | Select-Object -First 1
}
foreach ($item in $archives) {
    $cached = Join-Path $DataRoot ("downloads\packages\" + $item.archiveFileName)
    & python (Join-Path $PSScriptRoot 'download.py') $item.url $cached --sha256 ($item.checksum -replace '^SHA-256:', '')
    if ($LASTEXITCODE -ne 0) { throw "Package download failed: $($item.archiveFileName)" }
}
& $cli --config-file $config core install "SiliconLabs:silabs@$coreVersion"
if ($LASTEXITCODE -ne 0) { throw 'Silicon Labs board package installation failed.' }
& $cli --config-file $config lib install 'ArduinoJson@7.4.2'
if ($LASTEXITCODE -ne 0) { throw 'ArduinoJson installation failed.' }
& $cli --config-file $config version
& $cli --config-file $config core list
Set-Content -LiteralPath (Join-Path $DataRoot 'arduino-cli-path.txt') -Value $cli -Encoding utf8
Write-Host "Ready. IDE: $IdeRoot; configuration: $config"
