# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# pythonnet 在 CpuSensors 中延迟导入 (函数内 import), 显式收集
# 其数据文件与依赖, 否则打包后 CPU 温度读取会失效
_datas, _binaries, _hidden = [], [], []
for pkg in ("pythonnet", "clr_loader"):
    d, b, h = collect_all(pkg)
    _datas += d
    _binaries += b
    _hidden += h

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_binaries,
    datas=[('libs\\LHM\\*.dll', 'libs\\LHM'), ('libs\\PawnIO_setup.exe', 'libs'), ('libs\\logo.ico', 'libs')] + _datas,
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SysMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
