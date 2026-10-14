param(
    [string]$Flutter = 'flutter',
    [string]$BuildRoot = (Join-Path ([System.IO.Path]::GetTempPath()) 'touchsee-builds'),
    [switch]$Offline
)
$ErrorActionPreference = 'Stop'
$source = $PSScriptRoot
$flutterCommand = (Get-Command $Flutter -ErrorAction Stop).Source
$buildBase = [System.IO.Path]::GetFullPath($BuildRoot)
if ($buildBase -match '[^\x00-\x7F]') {
    throw 'Choose an ASCII BuildRoot, for example D:\codex-tmp\touchsee-builds.'
}
# Gradle resolves junctions; a real ASCII copy avoids Windows AOT path errors.
$stage = Join-Path $buildBase ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage -Force | Out-Null
function Copy-SourceTree([string]$From, [string]$To) {
    New-Item -ItemType Directory -Path $To -Force | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $From -Force) {
        if ($entry.Name -in @('.gradle', '.cxx', 'build', 'local.properties',
            'key.properties', 'GeneratedPluginRegistrant.java') -or
            $entry.Extension -in @('.iml', '.jks', '.keystore')) { continue }
        $target = Join-Path $To $entry.Name
        if ($entry.PSIsContainer) { Copy-SourceTree $entry.FullName $target }
        else { Copy-Item -LiteralPath $entry.FullName -Destination $target }
    }
}
foreach ($name in @('lib', 'android')) {
    Copy-SourceTree (Join-Path $source $name) (Join-Path $stage $name)
}
foreach ($name in @('pubspec.yaml', 'pubspec.lock', 'analysis_options.yaml')) {
    Copy-Item -LiteralPath (Join-Path $source $name) -Destination $stage
}
Write-Host "Build workspace: $stage"
Push-Location $stage
try {
    if ($Offline) { & $flutterCommand pub get --offline }
    else { & $flutterCommand pub get }
    if ($LASTEXITCODE -ne 0) { throw 'Flutter dependency resolution failed.' }
    & $flutterCommand build apk --release --no-pub
    if ($LASTEXITCODE -ne 0) { throw 'Android APK build failed.' }
    $output = Join-Path $source 'dist'
    New-Item -ItemType Directory -Path $output -Force | Out-Null
    $apk = Join-Path $output 'TouchSee-Android-0.1.0.apk'
    Copy-Item -LiteralPath (Join-Path $stage 'build\app\outputs\flutter-apk\app-release.apk') -Destination $apk
    Get-FileHash -LiteralPath $apk -Algorithm SHA256 | Format-List
    Write-Host "Internal-test APK: $apk"
} finally {
    Pop-Location
}
