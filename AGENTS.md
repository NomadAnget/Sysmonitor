# SysMonitor — Project Memory

## Overview

SysMonitor is a Windows desktop system monitoring tool built as a **PyQt6 package** (`sysmonitor/`). It provides real-time monitoring of CPU, memory, network, and multi-GPU NVIDIA cards with per-process GPU memory and network traffic. **AMD GPU** support via LibreHardwareMonitorLib (pythonnet).

- **Language**: Python 3.13+
- **GUI**: PyQt6, single-threaded with background threads for slow operations
- **Build**: Nuitka (single-file exe via `build.ps1`), also PyInstaller-compatible (`SysMonitor.spec`)
- **Dependencies**: `psutil`, `nvidia-ml-py`, `PyQt6`, `pythonnet`, `pywin32`, `pywintrace`
- **GPU Backends**: NVML (NVIDIA) → LHM (AMD) → WMI fallback (any), plus `SYS_AMD_SIMULATE=1` simulation mode
- **CI/CD**: Forgejo Actions + GitHub Actions (tag-triggered release)

## Architecture

```
sysmonitor/             (package, replaces old monitor.py)
├── config.py           # ThemeConfig + resolve_colors()
├── utils.py            # res_path, fmt_bytes, cpu_name, level_color, bar_style
├── widgets.py          # MeterRow, Sparkline custom widgets
├── elevation.py        # is_admin, try_elevate (UAC)
├── single_instance.py  # Named mutex + QLocalServer IPC
├── window.py           # MonitorWindow (main window, ~738 lines)
├── main.py             # main() entry point (init → UAC → window)
├── monitors/
│   ├── gpu.py          # GpuBackend, GpuProcMem (NVML + PDH)
│   ├── network.py      # NetworkETW (PDH per-process IO)
│   └── cpu_sensors.py  # CpuSensors (temp/power/freq thread)
├── __init__.py          # Package marker
└── __main__.py          # python -m sysmonitor entry
```

### Entry Point

`main()` in `sysmonitor/main.py` does:
1. Qt application init
2. Single-instance check (named mutex `Local\SysMonitor_SingleInstance_Mutex`)
3. Auto UAC elevation (if not admin, relaunch via `ShellExecuteW runas` with `-m sysmonitor`)
4. Create `MonitorWindow`, start IPC server (`QLocalServer`), show window
5. `app.exec()` — note `setQuitOnLastWindowClosed(False)` for tray persistence

Run with: `python -m sysmonitor` or `python main.py`.

## Core Modules

### GpuBackend (`sysmonitor/monitors/gpu.py`)

Multi-GPU monitoring with three backends in priority order:

| Priority | Backend | Source | Supports |
|----------|---------|--------|----------|
| 1 | NVML | `nvidia-ml-py` / `pynvml` | NVIDIA GPUs (full data) |
| 2 | LHM AMD | `LibreHardwareMonitorLib` via pythonnet | AMD GPUs (util, temp, power, clock, mem) |
| 3 | WMI fallback | `Win32_VideoController` (pywin32) | Any GPU (name + VRAM only, no real-time) |
| — | Simulation | `SYS_AMD_SIMULATE=1` env var | 2 simulated AMD GPUs for UI testing |

Detection chain: **NVML → WMI probe *(has AMD?)* → LHM → WMI fallback → Simulation**

**NVML** (existing): Full NVML data — gpu_util%, mem_used/total, temp°C, power W, clock MHz, enc/dec util%, PCIe width/gen, per-process GPU memory.

**LHM AMD**: Reads AMD GPU sensors from LibreHardwareMonitor via .NET:
- `SensorType.Load` → GPU core utilization (%)
- `SensorType.Temperature` → GPU temperature (°C)
- `SensorType.Clock` → GPU core clock (MHz)
- `SensorType.Power` → GPU power draw (W)
- `SensorType.SmallData` → VRAM used/total (MB → bytes)
- Non-admin users may not get temperature/power (varies by system)

**WMI fallback**: Last resort — returns only `name` and `mem_total` from `Win32_VideoController`. No real-time data.

**Simulation** (`SYS_AMD_SIMULATE=1`): Generates 2 fake AMD GPUs (RX 7900 XTX, RX 7800 XT) with sine-wave data for UI testing. Title bar shows "(仿真模式)".

**Per-process GPU memory** (`GpuProcMem`): Uses PDH counter `\GPU Process Memory(*)\Dedicated Usage` via raw `PdhGetFormattedCounterArrayW`. Works for any WDDM GPU (NVIDIA + AMD). LUID → GPU index mapping:
- NVIDIA: CUDA `cuDeviceGetLuid` + `cuDeviceGetPCIBusId` → NVML bus ID
- AMD/fallback: sorted LUID order (no per-GPU mapping, limited to single-GPU systems)

**Vendor detection**: `_has_amd_gpu()` WMI query checks `AdapterCompatibility` and `Name` for AMD/ATI/Radeon patterns before loading LHM (avoids pythonnet overhead on non-AMD systems).

**GPU VRAM fallback**: If LHM doesn't report `Memory Total`, `_guess_vram()` matches GPU name against known AMD models (7900 XTX → 24 GB, 7800 XT → 16 GB, etc.).

### NetworkETW (`sysmonitor/monitors/network.py`)

Per-process network monitoring using PDH counter `\Process(*)\IO Read Bytes/sec`. Compatible with HVCI (no kernel driver needed). Samples all processes' I/O and matches instance names to PIDs.

### CpuSensors (`sysmonitor/monitors/cpu_sensors.py`)

Background daemon thread that polls three metrics every 1s:

| Metric | Source | Requirements |
|--------|--------|-------------|
| Temperature | `Win32_PerfFormattedData_Counters_ThermalZoneInformation` (WMI) | None (built-in, pywin32) |
| Power (W) | `\Energy Meter(*_PKG)\Power` PDH counter | None (built-in EMI) |
| Real-time freq (MHz) | `% Processor Performance` × base MHz | None |

Temperature uses **LHM** (LibreHardwareMonitor) first regardless of admin status — reads CPU DTS directly. Falls back to WMI `Win32_PerfFormattedData_Counters_ThermalZoneInformation` (ACPI motherboard zone, in Kelvin) if LHM unavailable.

### MonitorWindow (`sysmonitor/window.py`)

Main window, 700px fixed width, dynamic height based on content (CPU cores × GPU count).

**Three refresh timers**:
| Timer | Interval | Purpose |
|-------|----------|---------|
| `self.timer` | 100-1000ms (configurable) | CPU %, GPU poll (util/temp/power/freq/codec/procs) |
| `self.mem_timer` | 100ms fixed | Memory bars (RAM, swap, VRAM) |
| `self.net_timer` | 1000ms fixed | Network up/down rates + per-process |

**Theme system** — three modes:
- `system`: Reads QPalette live, follows Windows dark/light mode + accent color changes
- `dark` / `light`: Hardcoded color sets (ThemeConfig in config.py)
- Cycling via `_cycle_theme()`: system → dark → light → ...
- `colorSchemeChanged` signal + `PaletteChange` event for live system mode tracking
- Protected by `_applying_theme` flag to prevent re-entrant `setStyleSheet` loops

**Mica effect** (Windows 11): Applied in `showEvent` → `_enable_mica()`:
- `DwmExtendFrameIntoClientArea` with (-1,-1,-1,-1) margins
- `DWMWA_SYSTEMBACKDROP_TYPE` = 2 (DWMSBT_MAINWINDOW)
- `DWMWA_USE_IMMERSIVE_DARK_MODE` = 20 for title bar
- Transparency: QWidget background = transparent, QGroupBox background = transparent → Mica shows through
- Must re-apply after `setWindowFlag` (which recreates HWND)

**Single instance IPC**: `QLocalServer` listening on `SysMonitor_SingleInstance_IPC`. Second instance connects briefly → triggers `_restore()` on first instance.

### Custom Widgets

**MeterRow** (`sysmonitor/widgets.py`): Label + QProgressBar. Optimized `set_value()` — only calls `setStyleSheet` when color tier changes (green/yellow/red). Uses `level_color()` with two palettes (light/dark).

**Sparkline** (`sysmonitor/widgets.py`): QPainter-rendered history chart (600-point deque). Draws gradient fill + polyline + current value text + 25/50/75% grid lines. Dynamic line color follows value.

### Helper Functions (`sysmonitor/utils.py`)

- `res_path(*parts)`: Resolves resource paths for source/Nuitka/PyInstaller modes. Auto-detects package dir offset.
- `fmt_bytes(n)`: Human-readable byte formatting (B/KB/MB/GB/TB)
- `cpu_name()`: Reads `HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0\ProcessorNameString` for proper CPU model name
- `level_color(value)`, `bar_style(value)`: Theming utilities (reads ThemeConfig)

## Data Flow

```
                     ┌─────────────────────┐
                     │   refresh_main()     │ ← timer (100-1000ms)
                     │  ┌─────────────────┐ │
                     │  │ CPU: psutil      │ │
                     │  │ GPU: GpuBackend  │ │
                     │  │ Sensors: CpuSens │ │
                     │  └─────────────────┘ │
                     └─────────┬───────────┘
                               │
                     ┌─────────▼───────────┐
                     │   refresh_mem()      │ ← mem_timer (100ms)
                     │  ┌─────────────────┐ │
                     │  │ psutil VM/Swap   │ │
                     │  │ GpuBackend.poll  │ │
                     │  │ _mem (light)     │ │
                     │  └─────────────────┘ │
                     └─────────┬───────────┘
                               │
                     ┌─────────▼───────────┐
                     │   refresh_net()      │ ← net_timer (1000ms)
                     │  ┌─────────────────┐ │
                     │  │ psutil net_io    │ │
                     │  │ NetworkETW snap  │ │
                     │  └─────────────────┘ │
                     └─────────────────────┘
```

Background threads:
- `CpuSensors._loop()`: Continuous polling of power/freq/temp at 1s
- `_mem_proc_worker()`: Enumerates top 6 memory-consuming processes every 1s

## Build System

### Nuitka (primary)
```powershell
.\build.ps1
```
Produces `SysMonitor.exe` as a single-file executable with embedded data files (libs/*.dll, logo.ico).

### PyInstaller (alternative)
```bash
pyinstaller SysMonitor.spec
```

### Resources
- `libs/`: .NET DLLs (LibreHardwareMonitorLib, etc.) — loaded at runtime via pythonnet
- `libs/logo.ico`: Application icon (window, taskbar, tray)

### Packaging paths
Resources resolved by `res_path()`:
- Source: relative to package parent (auto-detects `sysmonitor/` offset)
- Nuitka onefile: relative to temp extraction directory (`__file__`)
- PyInstaller: relative to `sys._MEIPASS`

## Known Constraints

| Data | Source | Constraint |
|------|--------|------------|
| CPU temp | WMI ThermalZoneInfo | Most Windows 10/11 systems; Kelvin→Celsius (LHM gives DTS directly) |
| CPU power | EMI Energy Meter | Always available on modern Windows |
| Per-process GPU mem | GPU Process Memory counter | Windows only; WDDM mode |
| Per-process net | PDH IO counters | Simplified; not true ETW |
| GPU metrics (NVIDIA) | NVML | NVIDIA only; falls back to AMD/WMI |
| GPU metrics (AMD) | LHM / LibreHardwareMonitor | pythonnet + .NET; admin rights may be needed |
| VRAM fallback | `_guess_vram()` | Name-based lookup for known AMD models |
| Mica | DWM API | Windows 11 only; transparent background required |
| WSL2 net | — | Traffic counted to host `vmwp.exe` |

## Conventions

- Thread safety: background threads communicate via instance attributes (no Qt signals for sensor data)
- Error resilience: all external calls wrapped in try/except with graceful degradation
- Theme: QSS-driven via ThemeConfig (no QPalette mutation except reading), dynamic property selectors for sub-text
- Memory: `deque(maxlen=600)` for history data
- Onefile builds: use `res_path()` for all resource lookups; never `sys.executable` or `cwd` for data files
- UAC: child instance launched with `-m sysmonitor --elevated`; parent releases mutex then exits
