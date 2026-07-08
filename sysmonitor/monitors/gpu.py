import ctypes
import os
import sys
import re
import time

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
        self._init_nvml()
        if self.kind == "nvml" and sys.platform.startswith("win"):
            pm = GpuProcMem()
            self._procmem = pm if pm.ok else None
            self._luid_to_index = self._build_luid_map()

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
        return len(self._handles)

    def static_info(self):
        return self._static

    def poll(self):
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

    def poll_mem(self):
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

    def shutdown(self):
        if self._procmem is not None:
            self._procmem.close()
        if self.kind == "nvml" and self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
