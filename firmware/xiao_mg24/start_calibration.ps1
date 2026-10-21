$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))
& python (Join-Path $PSScriptRoot 'fpga_calibration_gui.py')
if ($LASTEXITCODE -ne 0) { throw '校准窗口启动失败，请检查 Python、Tk 和 requirements.txt。' }
