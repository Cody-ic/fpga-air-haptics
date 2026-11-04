param(
    [string]$Python = 'python',
    [switch]$NoPause
)

# Keep this launcher ASCII so Windows PowerShell 5.1 can parse it on any locale.
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$logDir = Join-Path $repoRoot '.runtime\calibration\launcher'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$startupLog = Join-Path $logDir "$stamp-startup.log"
$stdoutLog = Join-Path $logDir "$stamp-stdout.log"
$stderrLog = Join-Path $logDir "$stamp-stderr.log"

try {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $pythonCommand = Get-Command $Python -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $guiPath = Join-Path $PSScriptRoot 'fpga_calibration_gui.py'
    "Python: $($pythonCommand.Source)" | Set-Content -LiteralPath $startupLog -Encoding UTF8
    Write-Host "Opening TouchSee calibration window with $($pythonCommand.Source)"
    Write-Host 'Choose the FPGA serial port in the Chinese window. Opening it does not start calibration.'
    $process = Start-Process -FilePath $pythonCommand.Source -ArgumentList @('-u', ('"{0}"' -f $guiPath)) `
        -WorkingDirectory $repoRoot -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    if ($process.ExitCode -ne 0) {
        throw "Python exited with code $($process.ExitCode). Check the error log below."
    }
} catch {
    Write-Host 'Calibration window could not start.' -ForegroundColor Red
    Write-Host $_.Exception.Message
    if (Test-Path -LiteralPath $logDir) {
        $_ | Out-String | Add-Content -LiteralPath $startupLog -Encoding UTF8
    }
    if (Test-Path -LiteralPath $stderrLog) {
        Get-Content -LiteralPath $stderrLog -Encoding UTF8 | Write-Host
    }
    Write-Host "Startup log: $startupLog"
    Write-Host "Python error log: $stderrLog"
    Write-Host 'Use Python 3.11+ with Tk and install firmware/xiao_mg24/requirements.txt.'
    if (-not $NoPause) { [void](Read-Host 'Press Enter to close') }
    exit 1
}
