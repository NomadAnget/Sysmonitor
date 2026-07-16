# SysMonitor — Project Memory

Windows desktop system monitor (PyQt6). CPU, memory, network, multi-GPU (NVIDIA + AMD), per-process GPU memory and network traffic.

## Quick start

```powershell
uv sync
uv run python -m sysmonitor     # or python main.py
.\build.ps1                      # Nuitka one-file exe → SysMonitor.exe
```

- Python 3.13+ only. Use `uv`, not pip.
- No lint, typecheck, or test tooling — do not run them.
- Windows-only; all non-portable APIs (pywin32, pythonnet, ctypes Win32, PDH, WMI).
- Run without admin for most features; PawnIO driver requires admin for per-core CPU temp/freq via MSR.

## Package layout

```
sysmonitor/
├── __main__.py          # python -m sysmonitor entry
├── core/                # UI entry & window
│   ├── main.py          # QApp → single-instance → splash → init → MonitorWindow → app.exec
│   ├── window.py        # MonitorWindow (700px wide, dynamic height, Mica, connects MonitorData signals)
│   ├── splash.py        # SplashWindow (frameless loading progress during init)
│   └── single_instance.py  # Named mutex + QLocalServer IPC
├── data/                # Data & monitor backends
│   ├── monitor_data.py  # MonitorData (QObject): owns QTimers, background threads, emits Qt signals
│   └── monitors/
│       ├── gpu.py       # GpuBackend, GpuProcMem (NVML → LHM AMD → WMI → simulation)
│       ├── network.py   # NetworkETW (PDH \Process(*)\IO Read Bytes/sec)
│       └── cpu_sensors.py  # CpuSensors daemon thread (PDH power/freq + LHM temp)
├── ui/
│   └── widgets.py       # MeterRow, Sparkline, CoreGrid
└── utils/
    ├── config.py        # ThemeConfig global class + resolve_colors
    ├── elevation.py     # is_admin, try_elevate (unused in current main flow)
    ├── pawnio.py        # PawnIO kernel driver detection + silent install
    └── utils.py         # res_path, fmt_bytes, cpu_name, level_color, bar_style
```

Root `main.py` is `from sysmonitor.core.main import main; main()`.

## Entry flow (`core/main.py`)

1. `QApplication` init → `acquire_single_instance()` (named mutex `Local\SysMonitor_SingleInstance_Mutex`)
2. If second instance: `notify_existing_instance()` (QLocalSocket) then exit
3. `setQuitOnLastWindowClosed(False)` for tray
4. `SplashWindow` shown (frameless loading window, centered, progress bar + status label)
5. `ensure_pawnio()` → `CpuSensors()` → `GpuBackend()` → `NetworkETW()`
6. `MonitorData(gpu, net_etw, cpu_sensors)` created
7. `MonitorWindow(data)` → `start_single_instance_server()` → splash closed → `show()`
8. `app.aboutToQuit.connect(uninstall_pawnio)`

**No UAC auto-elevation** in current entry flow. `elevation.py` exists but is not called from main. The `--elevated` flag handling mentioned in older docs was removed.

## Data flow & threading

`MonitorData` is the central coordinator (QObject with 4 pyqtSignals):

| Signal | Emitted by | Trigger | Contents |
|--------|-----------|---------|----------|
| `cpu_updated` | `_cpu_worker` thread | `_timer_main` (100-1000ms) | `CpuData` (total%, per-core%, freqs, power, temp, procs) |
| `mem_updated` | `_mem_worker` thread | `_timer_mem` (1000ms) | `MemData` (mem/swap pct, gpu_mem list, freq, procs) |
| `net_updated` | `_net_worker` thread | `_timer_net` (1000ms) | `NetData` (up/down bytes/sec, procs_text) |
| `gpu_updated` | `_cpu_worker` thread | same as cpu | `list[GpuItem]` (util, temp, power, clock, enc/dec, PCIe, procs) |

- `MonitorWindow` connects signals in constructor: `data.cpu_updated.connect(self._on_cpu)`, etc.
- Workers use `threading.Event` + QTimer timeout for wake-up — no Qt signals from threads.
- `_proc_worker` runs continuously (1s sleep), polls `psutil.process_iter` for top memory & CPU processes.
- `CpuSensors._loop` runs in its own daemon thread (1s interval).
- `GpuBackend._bg_poll_loop` runs for NVML backend only (daemon thread, configurable interval).
- Cross-thread data flow: instance attributes, no explicit locking for simple values (power, temp, freq).

## GPU backend detection

`NVML` → WMI probe *(has AMD?)* → `LHM` (LibreHardwareMonitor .NET) → `WMI` fallback → `Simulation`

| Backend | Requires | Data |
|---------|----------|------|
| NVML | NVIDIA GPU + `nvidia-ml-py` | Full: util, mem, temp, power, clock, enc/dec, PCIe, per-process |
| LHM AMD | `LibreHardwareMonitorLib.dll` (in `libs/LHM/`) + pythonnet | Util, temp, power, clock, VRAM |
| WMI | pywin32 | Name + VRAM total only |
| Simulation | `SYS_AMD_SIMULATE=1` env | 2 fake AMD GPUs |

Per-process GPU memory (`GpuProcMem`): PDH `\GPU Process Memory(*)\Dedicated Usage`, works for any WDDM GPU.

Sensors `fmt_bytes_short` is defined locally in `gpu.py` (avoids circular import with `utils.py`).

## Theme system

Three modes cycled via `_cycle_theme()`: `system` → `dark` → `light` → ...

- `ThemeConfig` is a **global mutable class**, mutated by `resolve_colors(mode)`. No QPalette mutation.
- "system" mode reads `QApplication.palette()` live and tracks `colorSchemeChanged` / `PaletteChange` event for live Windows dark/light changes.
- `_applying_theme` flag prevents re-entrant `setStyleSheet` loops.

**Mica** (Windows 11): applied in `showEvent` via `DwmExtendFrameIntoClientArea` + `DWMWA_SYSTEMBACKDROP_TYPE=2`. Must re-apply after any `setWindowFlag()` (recreates HWND). Requires transparent widget backgrounds.

## Asset resolution

Always use `utils.res_path(*parts)`; works in source, Nuitka onefile (`sys._MEIPASS`), and PyInstaller modes. Walks up from `utils/` dir to find `libs/`.

Key assets: `libs/logo.ico`, `libs/LHM/*.dll`, `libs/PawnIO_setup.exe`.

`CpuSensors._init_lhm()` and `GpuBackend._find_libs()` do their own path resolution for LHM DLLs (appending to `sys.path` and `PATH`).

## PawnIO driver

`pawnio.py` detects and silently installs the PawnIO kernel driver via `PawnIO_setup.exe -silent`. Required for LHM to read CPU temperature/frequency via MSR. Auto-installed in `core/main.py` via `ensure_pawnio()` if admin; graceful fallback if not. Uninstalled on `app.aboutToQuit`.

## Build / CI quirks

**Binary dependencies** (`scripts/get-deps.ps1`): LHM DLLs and PawnIO_setup.exe are not in git. Run before any build — downloads from GitHub releases. `build.ps1` calls it automatically.

**Nuitka** (primary): `.\build.ps1` runs `get-deps.ps1`, `uv sync --group dev`, then:
```
uv run python -m nuitka --standalone --onefile --windows-console-mode=disable --windows-uac-admin --plugin-enable=pyqt6 --include-raw-dir=libs=libs --windows-icon-from-ico=libs/logo.ico --clean-cache=all -o SysMonitor.exe main.py
```

**PyInstaller** (alternative): `pyinstaller SysMonitor.spec` (note: `SysMonitor.spec` explicitly collects `pythonnet` + `clr_loader` hidden imports for CpuSensors LHM support).

**GitHub Actions** (`.github/workflows/build.yml`):
- Uses `setup-uv@v8.2.0` (pinned)
- Nuitka step uses `shell: cmd` — pwsh swallows long flags
- `github.server_url == 'https://github.com'` guard prevents Forgejo from picking it up

**Forgejo CI** (`.forgejo/workflows/build.yml`):
- Self-hosted Windows runner (label `windows`, no pwsh — uses `powershell` 5.1)
- Release asset upload via internal API directly (`http://debian.lan:3257/api/v1`)
- Uses `> ` multiline syntax for Nuitka command
- No `--clean-cache=all` in CI (reuses compile cache)

## Conventions

- All external calls (PDH, WMI, NVML, LHM) wrapped in try/except with graceful degradation.
- History storage: `deque(maxlen=600)` in Sparkline.
- `MeterRow.set_value()` only calls `setStyleSheet` when color tier changes (optimization).
- `psutil.cpu_percent(percpu=True)` called once at `MonitorData` init to calibrate.
- Memory frequency queried once via PowerShell subprocess (`Get-CimInstance Win32_PhysicalMemory`).
- `cpu_name()` reads `ProcessorNameString` from registry (matches Task Manager).
- All Python code in `sysmonitor/` package (root `main.py` is just `from sysmonitor.core.main import main; main()`).
