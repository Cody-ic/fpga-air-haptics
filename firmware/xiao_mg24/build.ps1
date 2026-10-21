param(
    [ValidateSet('usb', 'ble', 'both')][string]$Mode = 'both',
    [string]$DataRoot = 'D:\ArduinoData',
    [string]$IdeRoot = 'D:\tools\ArduinoIDE'
)
$ErrorActionPreference = 'Stop'
$cli = Get-ChildItem -LiteralPath $IdeRoot -Recurse -Filter arduino-cli.exe | Select-Object -First 1 -ExpandProperty FullName
if (-not $cli) { throw 'Run setup_environment.ps1 first.' }
$config = Join-Path $DataRoot 'arduino-cli.yaml'
$board = 'SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs'
$modes = if ($Mode -eq 'both') { @('usb', 'ble') } else { @($Mode) }
$versions = & $cli --config-file $config core list
if (-not ($versions -match 'SiliconLabs:silabs\s+5\.0\.0')) { throw 'Silicon Labs core 5.0.0 is required.' }
foreach ($selected in $modes) {
    $macro = if ($selected -eq 'ble') { 1 } else { 0 }
    # Stage in D: with an explicit mode header, preserving the user's source macro.
    $stage = Join-Path $DataRoot "build\mg24-$selected\receiver"
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Copy-Item -Path (Join-Path $PSScriptRoot 'receiver\*') -Destination $stage -Force
    $original = Get-Content -LiteralPath (Join-Path $stage 'config.h') -Raw -Encoding utf8
    Set-Content -LiteralPath (Join-Path $stage 'config.h') -Value ("#define MG24_USE_BLUETOOTH $macro`n" + $original) -Encoding utf8
    $output = Join-Path $PSScriptRoot "build\$selected"
    $build = Join-Path $DataRoot "build\mg24-$selected\objects"
    $log = Join-Path $DataRoot "logs\mg24-$selected-build.log"
    & python (Join-Path $PSScriptRoot 'run_compile.py') $cli $config $board $build $output $stage $log
    $compileExit = $LASTEXITCODE
    if ($compileExit -ne 0) { throw "$selected build failed; see $log" }
    Write-Host "Built $selected firmware: $output (no upload performed)"
}
