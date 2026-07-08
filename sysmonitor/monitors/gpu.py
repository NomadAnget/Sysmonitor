import ctypes
import os
import random
import re
import sys
import threading
import time
import math

import psutil


_PDH_FMT_LARGE = 0x00000400


class _PdhCounterValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong), ("largeValue", ctypes.c_longlong)]


class _PdhCounterItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PdhCounterValue)]


class GpuProcMem:
    PATH = r"\GPU Process Memory(*)\Dedicated Usage"
    RE = re.compile(r"pid_(\d+)_luid_(0x[0-9A-Fa-f]+)_(0x[0-9A-Fa-f]+)_phys")

    def __init__(self):
        self.ok = False
        self._pdh = None
        self._q = None
        self._h = None
        self._dll = None
        try:
            import win32pdh
        except ImportError:
            return
        try:
            self._pdh = win32pdh
            self._q = win32pdh.OpenQuery()
            self._h = win32pdh.AddEnglishCounter(self._q, self.PATH)
            win32pdh.CollectQueryData(self._q)
            self._dll = ctypes.windll.pdh
            self._dll.PdhGetFormattedCounterArrayW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.c_void_p,
            ]
            self.ok = True
        except Exception:
            self.ok = False

    def sample(self):
        all_luids = set()
        mem = {}
        if not self.ok:
            return all_luids, mem
        try:
            self._pdh.CollectQueryData(self._q)
            size = ctypes.c_ulong(0)
            count = ctypes.c_ulong(0)
            self._dll.PdhGetFormattedCounterArrayW(
                self._h, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None
            )
            if size.value == 0:
                return all_luids, mem
            buf = (ctypes.c_byte * size.value)()
            rc = self._dll.PdhGetFormattedCounterArrayW(
                self._h, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buf
            )
            if rc != 0:
                return all_luids, mem
            items = ctypes.cast(
                buf, ctypes.POINTER(_PdhCounterItem * count.value)
            ).contents
        except Exception:
            return all_luids, mem

        for it in items:
            name = it.szName
            if not name:
                continue
            m = self.RE.match(name)
            if not m:
                continue
            luid = (int(m.group(2), 16) << 32) | int(m.group(3), 16)
            all_luids.add(luid)
            val = it.FmtValue.largeValue
            if val and val > 0:
                pid = int(m.group(1))
                d = mem.setdefault(luid, {})
                d[pid] = d.get(pid, 0) + int(val)
        return all_luids, mem

    def close(self):
        try:
            if self._q is not None and self._pdh is not None:
                self._pdh.CloseQuery(self._q)
        except Exception:
            pass


_DEFAULT_VRAM = {
    "rx 7900 xtx": 24,
    "rx 7900 xt": 20,
    "rx 7800 xt": 16,
    "rx 7700 xt": 12,
    "rx 7600 xt": 16,
    "rx 7600": 8,
    "rx 6900 xt": 16,
    "rx 6800 xt": 16,
    "rx 6800": 16,
    "rx 6700 xt": 12,
    "rx 6600 xt": 8,
    "rx 6600": 8,
    "rx 6500 xt": 4,
    "rx 6400": 4,
    "pro w7900": 48,
    "pro w7800": 32,
    "pro w7600": 8,
    "vega": 8,
    "radeon vii": 16,
}


def _guess_vram(name):
    lower = name.lower()
    for key, gb in _DEFAULT_VRAM.items():
        if key in lower:
            return gb * 1024**3
    return 8 * 1024**3


class GpuBackend:
    def __init__(self):
        self.kind = "none"
        self._nvml = None
        self._handles = []
        self._busids = []
        self._static = []
        self._procmem = None
        self._proc_by_card = None
        self._proc_ts = 0.0
        self._name_cache = {}
        self._luid_to_index = {}

        self._lhm_comp = None
        self._lhm_hw_types = None
        self._lhm_sensor_types = None
        self._lhm_hardware = []

        self._debug = os.environ.get("SYS_GPU_DEBUG") == "1"
        self._simulate = os.environ.get("SYS_AMD_SIMULATE") == "1"
        self._sim_seeds = []

        self._init()

    # ── logging ──────────────────────────────────────────────

    def _log(self, msg):
        if self._debug:
            print(f"[GPUBackend] {msg}", file=sys.stderr)

    # ── backend detection chain ──────────────────────────────

    def _init(self):
        if self._simulate:
            self._init_simulate()
            self.kind = "sim"
            self._log(f"Simulation backend: {self.count} GPU(s)")
            return

        self._init_nvml()
        if self._handles:
            self.kind = "nvml"
            self._log(f"NVML backend: {self.count} GPU(s)")
            if sys.platform.startswith("win"):
                pm = GpuProcMem()
                self._procmem = pm if pm.ok else None
                self._luid_to_index = self._build_luid_map()
            self._bg_lock = threading.Lock()
            self._bg_data = self._poll_nvml()
            self._bg_mem = self._poll_mem_nvml()
            threading.Thread(target=self._bg_poll_loop, daemon=True).start()
            return

        if sys.platform.startswith("win") and self._has_amd_gpu():
            self._init_lhm_amd()
            if self._lhm_hardware:
                self.kind = "amd"
                self._log(f"AMD (LHM) backend: {self.count} GPU(s)")
                if sys.platform.startswith("win"):
                    pm = GpuProcMem()
                    self._procmem = pm if pm.ok else None
                return

        self._init_wmi_fallback()
        if self._static:
            self.kind = "wmi"
            self._log(f"WMI fallback backend: {self.count} GPU(s)")

    # ── NVML (NVIDIA) ───────────────────────────────────────

    def _init_nvml(self):
        try:
            import pynvml
        except ImportError:
            try:
                import nvidia_ml_py as pynvml
            except ImportError:
                return
        try:
            pynvml.nvmlInit()
        except Exception:
            return

        self._nvml = pynvml
        try:
            count = pynvml.nvmlDeviceGetCount()
        except Exception:
            return

        for i in range(count):
            try:
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(h)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "replace")
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                try:
                    busid = pynvml.nvmlDeviceGetPciInfo(h).busId
                    if isinstance(busid, bytes):
                        busid = busid.decode("ascii", "replace")
                except Exception:
                    busid = str(i)
                self._handles.append(h)
                self._busids.append(busid)
                self._static.append({"name": name, "mem_total": mem.total})
            except Exception:
                continue

        if self._handles:
            self.kind = "nvml"

    @property
    def count(self):
        if self.kind == "nvml":
            return len(self._handles)
        elif self.kind == "amd":
            return len(self._lhm_hardware)
        elif self.kind == "sim":
            return len(self._sim_seeds)
        elif self.kind == "wmi":
            return len(self._static)
        return 0

    def static_info(self):
        return self._static

    # ── fast AMD probe via WMI (avoid pythonnet if no AMD card) ─

    @staticmethod
    def _has_amd_gpu():
        try:
            import win32com.client

            wmi = win32com.client.GetObject(
                "winmgmts:{impersonationLevel=impersonate}!//./root/cimv2"
            )
            for gpu in wmi.ExecQuery(
                "SELECT * FROM Win32_VideoController "
                "WHERE AdapterCompatibility LIKE '%AMD%' "
                "OR AdapterCompatibility LIKE '%ATI%' "
                "OR Name LIKE '%Radeon%' "
                "OR Name LIKE '%AMD%'"
            ):
                return True
        except Exception:
            pass
        return False

    # ── AMD via LHM ─────────────────────────────────────────

    def _find_libs(self):
        from ..utils import res_path

        libs = res_path("libs")
        if not os.path.exists(os.path.join(libs, "LibreHardwareMonitorLib.dll")):
            for cand in (
                os.path.join(os.path.dirname(sys.executable), "libs"),
                os.path.join(os.getcwd(), "libs"),
            ):
                if os.path.exists(os.path.join(cand, "LibreHardwareMonitorLib.dll")):
                    libs = cand
                    break
        return libs

    def _init_lhm_amd(self):
        try:
            from pythonnet import load

            load("netfx")
            import clr
        except Exception as e:
            self._log(f"pythonnet import failed: {e}")
            return

        try:
            libs = self._find_libs()
            if libs not in sys.path:
                sys.path.append(libs)
            os.environ["PATH"] = libs + os.pathsep + os.environ.get("PATH", "")
            clr.AddReference("LibreHardwareMonitorLib")
            from LibreHardwareMonitor.Hardware import (
                Computer,
                HardwareType,
                SensorType,
            )
        except Exception as e:
            self._log(f"LHM assembly load failed: {e}")
            return

        try:
            comp = Computer()
            comp.IsGpuEnabled = True
            comp.Open()
        except Exception as e:
            self._log(f"LHM Computer.Open() failed: {e}")
            return

        amd_gpus = []
        for hw in comp.Hardware:
            if hw.HardwareType == HardwareType.GpuAmd:
                hw.Update()
                name = hw.Name.strip()
                mem_total = None
                for s in hw.Sensors:
                    if (
                        s.SensorType == SensorType.SmallData
                        and s.Value is not None
                        and "memory total" in s.Name.lower()
                    ):
                        try:
                            v = float(s.Value)
                            mem_total = int(v * 1024 * 1024) if v < 1e6 else int(v)
                        except Exception:
                            pass
                if mem_total is None:
                    mem_total = _guess_vram(name)
                self._static.append({"name": name, "mem_total": mem_total})
                amd_gpus.append(hw)
                self._log(f"AMD GPU: {name}, VRAM: {fmt_bytes_short(mem_total)}")

        if amd_gpus:
            self._lhm_comp = comp
            self._lhm_hw_types = HardwareType
            self._lhm_sensor_types = SensorType
            self._lhm_hardware = amd_gpus
        else:
            try:
                comp.Close()
            except Exception:
                pass

    # ── WMI fallback (any GPU, basic info only) ─────────────

    def _init_wmi_fallback(self):
        try:
            import win32com.client

            wmi = win32com.client.GetObject(
                "winmgmts:{impersonationLevel=impersonate}!//./root/cimv2"
            )
            for gpu in wmi.ExecQuery("SELECT * FROM Win32_VideoController"):
                gpu_name = str(gpu.Name or "")
                if not gpu_name:
                    continue
                vram = gpu.AdapterRAM
                vram = int(vram) if vram else 0
                self._static.append(
                    {
                        "name": gpu_name,
                        "mem_total": vram if vram > 0 else None,
                    }
                )
            if self._static:
                self._log(f"WMI fallback: {len(self._static)} GPU(s)")
        except Exception:
            pass

    # ── Simulation mode ─────────────────────────────────────

    def _init_simulate(self):
        names = [
            "AMD Radeon RX 7900 XTX (Simulated)",
            "AMD Radeon RX 7800 XT (Simulated)",
        ]
        totals = [24 * 1024**3, 16 * 1024**3]
        for i in range(2):
            self._static.append({"name": names[i], "mem_total": totals[i]})
            self._sim_seeds.append(i)

    # ── Background poll thread ─────────────────────────────
    def _bg_poll_loop(self):
        while True:
            with self._bg_lock:
                self._bg_data = self._poll_nvml()
                self._bg_mem = self._poll_mem_nvml()
            time.sleep(1.0)

    # ── poll / poll_mem dispatchers ─────────────────────────

    def poll(self):
        if self.kind == "nvml":
            with self._bg_lock:
                return list(getattr(self, "_bg_data", []))
        elif self.kind == "amd":
            return self._poll_lhm()
        elif self.kind == "sim":
            return self._poll_sim()
        elif self.kind == "wmi":
            return self._poll_wmi()
        return []

    def poll_mem(self):
        if self.kind == "nvml":
            with self._bg_lock:
                return list(getattr(self, "_bg_mem", []))
        elif self.kind == "amd":
            return self._poll_mem_lhm()
        elif self.kind == "sim":
            return self._poll_mem_sim()
        elif self.kind == "wmi":
            return self._poll_mem_wmi()
        return []

    # ── NVML poll ──────────────────────────────────────────

    def _poll_nvml(self):
        if self.kind != "nvml":
            return []
        self._refresh_procs()
        result = []
        nv = self._nvml
        for idx, h in enumerate(self._handles):
            info = {"name": self._static[idx]["name"]}
            try:
                util = nv.nvmlDeviceGetUtilizationRates(h)
                info["gpu_util"] = util.gpu
            except Exception:
                info["gpu_util"] = None
            try:
                mem = nv.nvmlDeviceGetMemoryInfo(h)
                info["mem_used"] = mem.used
                info["mem_total"] = mem.total
            except Exception:
                info["mem_used"] = info["mem_total"] = None
            try:
                info["temp"] = nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)
            except Exception:
                info["temp"] = None
            try:
                info["power"] = nv.nvmlDeviceGetPowerUsage(h) / 1000.0
            except Exception:
                info["power"] = None
            try:
                info["clock"] = nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_GRAPHICS)
            except Exception:
                info["clock"] = None
            try:
                info["enc_util"] = nv.nvmlDeviceGetEncoderUtilization(h)[0]
            except Exception:
                info["enc_util"] = None
            try:
                info["dec_util"] = nv.nvmlDeviceGetDecoderUtilization(h)[0]
            except Exception:
                info["dec_util"] = None
            try:
                info["enc_sessions"] = nv.nvmlDeviceGetEncoderStats(h)[0]
            except Exception:
                info["enc_sessions"] = None
            try:
                info["pcie_width"] = nv.nvmlDeviceGetCurrPcieLinkWidth(h)
            except Exception:
                info["pcie_width"] = None
            try:
                info["pcie_gen"] = nv.nvmlDeviceGetCurrPcieLinkGeneration(h)
            except Exception:
                info["pcie_gen"] = None
            try:
                info["max_pcie_width"] = nv.nvmlDeviceGetMaxPcieLinkWidth(h)
            except Exception:
                info["max_pcie_width"] = None
            try:
                info["max_pcie_gen"] = nv.nvmlDeviceGetMaxPcieLinkGeneration(h)
            except Exception:
                info["max_pcie_gen"] = None
            if self._procmem is not None:
                info["procs"] = (self._proc_by_card or {}).get(idx, [])
            else:
                info["procs"] = self._gpu_processes(h)
            result.append(info)
        return result

    def _poll_mem_nvml(self):
        if self.kind != "nvml":
            return []
        out = []
        nv = self._nvml
        for h in self._handles:
            try:
                m = nv.nvmlDeviceGetMemoryInfo(h)
                out.append((m.used, m.total))
            except Exception:
                out.append((None, None))
        return out

    # ── LHM (AMD) poll ──────────────────────────────────────

    def _poll_lhm(self):
        if self.kind != "amd":
            return []
        result = []
        st = self._lhm_sensor_types
        for idx, hw in enumerate(self._lhm_hardware):
            try:
                hw.Update()
            except Exception:
                pass

            info = {
                "name": (
                    self._static[idx]["name"] if idx < len(self._static) else "AMD GPU"
                ),
                "gpu_util": None,
                "mem_used": None,
                "mem_total": (
                    self._static[idx].get("mem_total")
                    if idx < len(self._static)
                    else None
                ),
                "temp": None,
                "power": None,
                "clock": None,
                "enc_util": None,
                "dec_util": None,
                "enc_sessions": None,
                "pcie_width": None,
                "pcie_gen": None,
                "max_pcie_width": None,
                "max_pcie_gen": None,
                "procs": [],
            }

            for s in hw.Sensors:
                if s.Value is None:
                    continue
                try:
                    val = float(s.Value)
                except Exception:
                    continue
                nl = s.Name.lower()

                if s.SensorType == st.Load:
                    if "gpu" in nl and "memory" not in nl:
                        if "core" in nl or info["gpu_util"] is None:
                            info["gpu_util"] = val

                elif s.SensorType == st.Temperature:
                    if "gpu" in nl and info["temp"] is None:
                        info["temp"] = val
                    elif info["temp"] is None and "hotspot" in nl:
                        info["temp"] = val

                elif s.SensorType == st.Clock:
                    if "gpu" in nl and "memory" not in nl:
                        if info["clock"] is None:
                            info["clock"] = int(val)

                elif s.SensorType == st.Power:
                    if "gpu" in nl or "package" in nl:
                        if info["power"] is None:
                            info["power"] = val

                elif s.SensorType == st.SmallData:
                    if "memory used" in nl:
                        info["mem_used"] = (
                            int(val * 1024 * 1024) if val < 1e6 else int(val)
                        )
                    elif "memory total" in nl and info["mem_total"] is None:
                        info["mem_total"] = (
                            int(val * 1024 * 1024) if val < 1e6 else int(val)
                        )

            result.append(info)
        return result

    def _poll_mem_lhm(self):
        if self.kind != "amd":
            return []
        out = []
        st = self._lhm_sensor_types
        for idx, hw in enumerate(self._lhm_hardware):
            try:
                hw.Update()
            except Exception:
                pass
            used = None
            total = (
                self._static[idx].get("mem_total") if idx < len(self._static) else None
            )
            for s in hw.Sensors:
                if s.SensorType == st.SmallData and s.Value is not None:
                    nl = s.Name.lower()
                    try:
                        v = float(s.Value)
                    except Exception:
                        continue
                    if "memory used" in nl:
                        used = int(v * 1024 * 1024) if v < 1e6 else int(v)
                    elif "memory total" in nl and total is None:
                        total = int(v * 1024 * 1024) if v < 1e6 else int(v)
            out.append((used, total))
        return out

    # ── Simulation poll ─────────────────────────────────────

    def _poll_sim(self):
        t = time.monotonic()
        result = []
        for i, seed in enumerate(self._sim_seeds):
            phase = t * 0.1 + seed * 2.0
            util = 35 + 45 * (0.5 + 0.5 * math.sin(phase * 0.7))
            mt = (
                self._static[i].get("mem_total") or (24 * 1024**3)
                if i < len(self._static)
                else 24 * 1024**3
            )
            mem_ratio = 0.25 + 0.55 * abs(math.cos(phase * 0.3))

            result.append(
                {
                    "name": (
                        self._static[i]["name"] if i < len(self._static) else "AMD GPU"
                    ),
                    "gpu_util": min(99.0, util),
                    "mem_used": int(mt * mem_ratio),
                    "mem_total": mt,
                    "temp": 45 + 30 * abs(math.sin(phase * 0.5)),
                    "power": 30 + 300 * (util / 100.0),
                    "clock": int(800 + 1700 * (util / 100.0)),
                    "enc_util": None,
                    "dec_util": None,
                    "enc_sessions": None,
                    "pcie_width": 16,
                    "pcie_gen": 4,
                    "max_pcie_width": 16,
                    "max_pcie_gen": 4,
                    "procs": [
                        {
                            "pid": 1234,
                            "name": "sim_proc.exe",
                            "mem": int(512 * 1024 * 1024),
                        }
                    ],
                }
            )
        return result

    def _poll_mem_sim(self):
        t = time.monotonic()
        out = []
        for i, seed in enumerate(self._sim_seeds):
            mt = (
                self._static[i].get("mem_total") or (24 * 1024**3)
                if i < len(self._static)
                else 24 * 1024**3
            )
            phase = t * 0.1 + seed * 2.0
            used = int(mt * (0.25 + 0.55 * abs(math.cos(phase * 0.3))))
            out.append((used, mt))
        return out

    # ── WMI fallback poll (basic, no real-time sensors) ────

    def _poll_wmi(self):
        return [
            {
                "name": s["name"],
                "gpu_util": None,
                "mem_used": None,
                "mem_total": s.get("mem_total"),
                "temp": None,
                "power": None,
                "clock": None,
                "enc_util": None,
                "dec_util": None,
                "enc_sessions": None,
                "pcie_width": None,
                "pcie_gen": None,
                "max_pcie_width": None,
                "max_pcie_gen": None,
                "procs": [],
            }
            for s in self._static
        ]

    def _poll_mem_wmi(self):
        return [(None, s.get("mem_total")) for s in self._static]

    # ── per-process GPU memory (shared by nvml/amd) ────────

    def _proc_name(self, pid):
        name = self._name_cache.get(pid)
        if name is None:
            try:
                name = psutil.Process(pid).name()
            except Exception:
                name = f"PID {pid}"
            self._name_cache[pid] = name
        return name

    @staticmethod
    def _norm_busid(s):
        parts = s.strip().lower().split(":")
        return ":".join(parts[-2:]) if len(parts) >= 2 else s.strip().lower()

    def _build_luid_map(self):
        mapping = {}
        try:
            cuda = ctypes.WinDLL("nvcuda.dll")
            if cuda.cuInit(0) != 0:
                return mapping
            count = ctypes.c_int()
            cuda.cuDeviceGetCount(ctypes.byref(count))
            nvml_by_bus = {self._norm_busid(b): i for i, b in enumerate(self._busids)}
            for i in range(count.value):
                dev = ctypes.c_int()
                if cuda.cuDeviceGet(ctypes.byref(dev), i) != 0:
                    continue
                luid = (ctypes.c_char * 8)()
                mask = ctypes.c_uint()
                if cuda.cuDeviceGetLuid(luid, ctypes.byref(mask), dev) != 0:
                    continue
                raw = bytes(luid)
                luid_int = (int.from_bytes(raw[4:8], "little") << 32) | int.from_bytes(
                    raw[0:4], "little"
                )
                buf = ctypes.create_string_buffer(32)
                cuda.cuDeviceGetPCIBusId(buf, 32, dev)
                idx = nvml_by_bus.get(self._norm_busid(buf.value.decode()))
                if idx is not None:
                    mapping[luid_int] = idx
        except Exception:
            pass
        return mapping

    def _refresh_procs(self):
        if self._procmem is None:
            return
        now = time.monotonic()
        if self._proc_by_card is not None and now - self._proc_ts < 1.0:
            return
        self._proc_ts = now

        all_luids, mem_map = self._procmem.sample()
        mapping = self._luid_to_index
        if not mapping:
            if not self._busids or self.count == 0:
                return
            luids = sorted(all_luids)
            order = sorted(range(self.count), key=lambda i: self._busids[i])
            mapping = {lu: order[k] for k, lu in enumerate(luids) if k < self.count}

        cards = {i: [] for i in range(self.count)}
        for luid, pidmap in mem_map.items():
            idx = mapping.get(luid)
            if idx is None:
                continue
            for pid, m in pidmap.items():
                if not m or m < 5 * 1024 * 1024:
                    continue
                cards[idx].append({"pid": pid, "name": self._proc_name(pid), "mem": m})
        for i in cards:
            cards[i].sort(key=lambda x: x["mem"], reverse=True)
        self._proc_by_card = cards

    def _gpu_processes(self, handle):
        nv = self._nvml
        merged = {}
        for getter in (
            "nvmlDeviceGetComputeRunningProcesses",
            "nvmlDeviceGetGraphicsRunningProcesses",
        ):
            try:
                for p in getattr(nv, getter)(handle):
                    mem = getattr(p, "usedGpuMemory", None)
                    if merged.get(p.pid) is None or (mem or 0) > (merged[p.pid] or 0):
                        merged[p.pid] = mem
            except Exception:
                continue

        procs = []
        for pid, mem in merged.items():
            try:
                name = psutil.Process(pid).name()
            except Exception:
                name = f"PID {pid}"
            procs.append({"pid": pid, "name": name, "mem": mem})
        procs.sort(key=lambda x: x["mem"] or 0, reverse=True)
        return procs

    # ── cleanup ─────────────────────────────────────────────

    def shutdown(self):
        if self._procmem is not None:
            self._procmem.close()
        if self.kind == "nvml" and self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
        if self._lhm_comp is not None:
            try:
                self._lhm_comp.Close()
            except Exception:
                pass


# helper — avoid circular import with utils.py
def fmt_bytes_short(n):
    if n is None:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
