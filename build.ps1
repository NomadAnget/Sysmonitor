# 本地一键构建 SysMonitor.exe (Nuitka)
# 用法:  .\build.ps1        产物: .\SysMonitor.exe
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

uv sync --group dev
uv run python -m nuitka --standalone --onefile `
    --windows-disable-console --plugin-enable=pyqt6 `
    --include-data-dir=libs=libs --clean-cache=all `
    --windows-icon-from-ico=libs/logo.ico `
    -o SysMonitor.exe monitor.py

if (Test-Path "SysMonitor.exe") {
    $size = (Get-Item "SysMonitor.exe").Length / 1MB
    Write-Host ("build ok: SysMonitor.exe  ({0:N1} MB)" -f $size)
} else {
    Write-Error "build failed: SysMonitor.exe not found"
}
