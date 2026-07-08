# SysMonitor — Project Memory

Windows desktop system monitor (PyQt6). CPU, memory, network, multi-GPU (NVIDIA + AMD), per-process GPU memory and network traffic.

## Quick start

```powershell
uv sync
uv run python -m sysmonitor     # or python main.py
.\build.ps1                      # Nuitka one-file exe → SysMonitor.exe
```

- Python 3.13+ only (`.python-version`, `uv.lock`). Use `uv`, not pip.
- No lint, typecheck, or test tooling configured — do not run them.
- Windows-only; all non-portable APIs (pywin32, pythonnet, ctypes Win32, PDH, WMI).

## Architecture

```
sysmonitor/
├── main.py              # entry: QApp → single-instance → MonitorWindow → app.exec
├── __main__.py          # python -m sysmonitor entry
├── window.py            # MonitorWindow (700px wide, dynamic height, Mica, 3 timers)
├── config.py            # ThemeConfig global class (mutated by resolve_colors)
├── utils.py             # res_path, fmt_bytes, cpu_name, level_color, bar_style
├── widgets.py           # MeterRow, Sparkline, CoreGrid
├── elevation.py         # is_admin, try_elevate (ShellExecuteW runas)
├── single_instance.py   # Named mutex + QLocalServer IPC
├── pawnio.py            # PawnIO kernel driver detection + silent install
└── monitors/
    ├── gpu.py           # GpuBackend, GpuProcMem (NVML → LHM → WMI → sim)
    ├── network.py       # NetworkETW (PDH \Process(*)\IO Read Bytes/sec)
    └── cpu_sensors.py   # CpuSensors (temp/power/freq daemon thread, LHM + PawnIO)
```

**Entry flow** (`main.py`):
1. QApplication init → `setQuitOnLastWindowClosed(False)` for tray
2. `acquire_single_instance()` (named mutex `Local\SysMonitor_SingleInstance_Mutex`)
3. If second instance: `notify_existing_instance()` (QLocalSocket) then exit
4. Auto UAC elevation: `elevation.py` — if not admin, relaunch via `ShellExecuteW runas` with `-m sysmonitor --elevated`; parent releases mutex and exits
5. `MonitorWindow` → `start_single_instance_server()` (QLocalServer `SysMonitor_SingleInstance_IPC`) → `show()`

**Three refresh timers** (window.py):
| Timer | Interval | Data |
|-------|----------|------|
| `self.timer` | 100-1000ms (QComboBox) | CPU %, GPU poll (util/temp/power/freq/codec/procs) |
| `self.mem_timer` | 100ms fixed | RAM, swap, VRAM bars + top-6 processes (background thread) |
| `self.net_timer` | 1000ms fixed | Up/down rates + per-process via NetworkETW snapshot |

**Background threads**: `CpuSensors._loop()` (power/freq/temp @ 1s, uses LHM + PawnIO for per-core temps), `_mem_proc_worker()` (top-6 procs @ 1s). Cross-thread data via instance attributes — no Qt signals.

**PawnIO driver**: `pawnio.py` detects and silently installs the PawnIO kernel driver (signed, HVCI-compatible) via `PawnIO_setup.exe -silent`. Required for LHM to read CPU temperature/frequency via MSR. Auto-installed in `CpuSensors.__init__()` if running as admin; graceful fallback if not.

## GPU backend detection

`NVML` → WMI probe *(has AMD?)* → `LHM` (LibreHardwareMonitor .NET) → `WMI` fallback → `Simulation`

| Backend | Requires | Data |
|---------|----------|------|
| NVML | NVIDIA GPU + `nvidia-ml-py` | Full: util, mem, temp, power, clock, enc/dec, PCIe, per-process |
| LHM AMD | `LibreHardwareMonitorLib.dll` (in `libs/LHM/`) + pythonnet | Util, temp, power, clock, VRAM (varies by admin rights) |
| WMI | pywin32 | Name + VRAM total only (no real-time) |
| Simulation | `SYS_AMD_SIMULATE=1` env | 2 fake AMD GPUs, "(仿真模式)" in title |

Per-process GPU memory (`GpuProcMem`): PDH `\GPU Process Memory(*)\Dedicated Usage`, works for any WDDM GPU.

## Theme system

Three modes cycled via `_cycle_theme()`: `system` → `dark` → `light` → ...

- `config.ThemeConfig` is a **global mutable class**, mutated by `resolve_colors(mode)`. No QPalette mutation.
- "system" mode reads `QApplication.palette()` live and tracks `colorSchemeChanged` / `PaletteChange` event for live Windows dark/light changes.
- `_applying_theme` flag prevents re-entrant `setStyleSheet` loops.

**Mica** (Windows 11): applied in `showEvent` via `DwmExtendFrameIntoClientArea` + `DWMWA_SYSTEMBACKDROP_TYPE=2`. Must re-apply after any `setWindowFlag()` call (recreates HWND). Requires transparent widget backgrounds.

## Asset resolution

Always use `utils.res_path(*parts)` to resolve paths. Works in source, Nuitka onefile, and PyInstaller modes. Never use `sys.executable` or `os.getcwd()`.

Key assets: `libs/logo.ico`, `libs/LHM/*.dll` (LibreHardwareMonitorLib).

## Build / CI quirks

**Binary dependencies** (`scripts/get-deps.ps1`): LHM DLLs and PawnIO_setup.exe are not in git. Run the script before any build — it downloads from official GitHub releases. `build.ps1` calls it automatically.

**Nuitka** (primary): `.\build.ps1` runs `scripts/get-deps.ps1`, `uv sync --group dev`, then `uv run python -m nuitka --standalone --onefile --windows-console-mode=disable --windows-uac-admin --plugin-enable=pyqt6 --include-raw-dir=libs=libs --windows-icon-from-ico=libs/logo.ico -o SysMonitor.exe main.py`

**PyInstaller** (alternative): `pyinstaller SysMonitor.spec`

**GitHub Actions** (`.github/workflows/build.yml`):
- Uses `setup-uv@v8.2.0` (pinned to exact version)
- Must use `shell: cmd` for Nuitka invocation — pwsh can swallow or mangle long flags (`--windows-disable-console`, `--include-raw-dir`)
- `github.server_url == 'https://github.com'` guard prevents Forgejo from picking it up

**Forgejo CI** (`.forgejo/workflows/build.yml`):
- Self-hosted Windows runner
- Release asset upload uses internal API directly (`http://debian.lan:3257/api/v1`) — cannot use `upload-artifact` action (Forgejo 403 with reverse proxy)

## Conventions

- Error resilience: all external calls (PDH, WMI, NVML, LHM) wrapped in try/except with graceful degradation.
- History storage: `deque(maxlen=600)`.
- UAC: elevated child launched with `--elevated` flag; parent releases mutex then exits (so child can acquire it).
- MeterRow: `set_value()` only calls `setStyleSheet` when color tier changes (optimization).
- All Python code in `sysmonitor/` package (root `main.py` is just `from sysmonitor.main import main; main()`).
