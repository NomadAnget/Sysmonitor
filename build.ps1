# 本地一键构建 SysMonitor.exe
# 用法:  .\build.ps1        产物: dist\SysMonitor.exe
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt pyinstaller --quiet

python -m PyInstaller SysMonitor.spec --noconfirm

if (Test-Path "dist\SysMonitor.exe") {
    $size = (Get-Item "dist\SysMonitor.exe").Length / 1MB
    Write-Host ("构建成功: dist\SysMonitor.exe  ({0:N1} MB)" -f $size)
} else {
    Write-Error "构建失败: 未生成 dist\SysMonitor.exe"
}
