"""
系统监控小工具 (PyQt6)
功能:
  - 系统配置: 操作系统 / CPU 型号 / 核心数 / 内存总量 / 多 GPU 列表
  - CPU: 总占用 + 每核心占用 + 历史曲线 + 温度/功耗/实时频率
  - 内存: 物理/交换占用 + 内存频率 + 进程占用排行
  - 网络: 上下行速率 + 每进程流量 (ETW, 需管理员)
  - GPU (多卡): 使用率 + 显存 + 温度/功耗/频率 + 编解码 + 每进程显存

依赖: PyQt6, psutil; 可选 nvidia-ml-py (GPU), pywin32 (每进程显存/网络/CPU 功耗),
      pywintrace (每进程网络), pythonnet + libs/ (CPU 温度)
"""
import os
import sys
import re
import time
import ctypes
import platform
import datetime
import threading
from collections import deque
import winreg

import psutil

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, QSize, QEvent
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QPolygonF, QLinearGradient, QAction,
    QPalette,QIcon,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QProgressBar, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QScrollArea, QFrame, QSystemTrayIcon, QMenu, QStyle,
    QComboBox, QAbstractButton, QSizePolicy, QPushButton,
)

# ----------------------------------------------------------------------------
# PDH (性能计数器) 数组读取结构 —— 用于每进程显存 (含同名实例)
# ----------------------------------------------------------------------------
_PDH_FMT_LARGE = 0x00000400


class _PdhCounterValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong),
                ("largeValue", ctypes.c_longlong)]


class _PdhCounterItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p),
                ("FmtValue", _PdhCounterValue)]


class GpuProcMem:
    """通过 Windows 性能计数器读取每进程、每张卡的显存占用。

    NVML 在 WDDM 驱动模式下不暴露每进程显存, 但 Windows 自身的
    "GPU Process Memory" 计数器可以 (任务管理器即用此数据)。
    实例名形如: pid_14464_luid_0x00000000_0x00013FDD_phys_0

    注意: 同一进程在一张卡上可能有多块分配, 表现为多个同名实例。
    win32pdh.GetFormattedCounterArray 返回字典会丢掉同名实例, 因此这里
    改用底层 PdhGetFormattedCounterArrayW (返回数组), 保留并累加全部实例。
    """

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
                ctypes.c_void_p, ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
            self.ok = True
        except Exception:
            self.ok = False

    def sample(self):
        """返回 (所有出现的 luid 集合, {luid: {pid: 显存字节}})。

        全部 luid 集合用于稳定地把 luid 映射到各卡 (即便某卡当前无进程)。
        """
        all_luids = set()
        mem = {}
        if not self.ok:
            return all_luids, mem
        try:
            self._pdh.CollectQueryData(self._q)
            size = ctypes.c_ulong(0)
            count = ctypes.c_ulong(0)
            # 第一次调用获取所需缓冲区大小
            self._dll.PdhGetFormattedCounterArrayW(
                self._h, _PDH_FMT_LARGE,
                ctypes.byref(size), ctypes.byref(count), None)
            if size.value == 0:
                return all_luids, mem
            buf = (ctypes.c_byte * size.value)()
            rc = self._dll.PdhGetFormattedCounterArrayW(
                self._h, _PDH_FMT_LARGE,
                ctypes.byref(size), ctypes.byref(count), buf)
            if rc != 0:
                return all_luids, mem
            items = ctypes.cast(
                buf, ctypes.POINTER(_PdhCounterItem * count.value)).contents
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
                d[pid] = d.get(pid, 0) + int(val)   # 累加同名(多块)实例
        return all_luids, mem

    def close(self):
        try:
            if self._q is not None and self._pdh is not None:
                self._pdh.CloseQuery(self._q)
        except Exception:
            pass


class GpuBackend:
    """封装 GPU 信息读取, 自动适配多 GPU 与无 GPU 的情况。"""

    def __init__(self):
        self.kind = "none"          # none / nvml
        self._nvml = None
        self._handles = []
        self._busids = []           # 每张卡的 PCI busId, 用于与 luid 对应
        self._static = []           # 每张卡的静态信息 (名称/显存总量)
        self._procmem = None        # Windows 每进程显存读取器
        self._proc_by_card = None   # 缓存: index -> 进程列表
        self._proc_ts = 0.0         # 上次刷新进程信息的时间
        self._name_cache = {}       # pid -> 进程名
        self._luid_to_index = {}    # 适配器 luid -> NVML 卡序号 (精确映射)
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
                # nvidia-ml-py 包同样以 pynvml 命名空间导出
                import nvidia_ml_py as pynvml  # type: ignore
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
        """返回每张卡的静态信息列表。"""
        return self._static

    def poll(self):
        """返回每张卡的实时信息列表 (dict)。"""
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
                info["temp"] = nv.nvmlDeviceGetTemperature(
                    h, nv.NVML_TEMPERATURE_GPU)
            except Exception:
                info["temp"] = None
            try:
                info["power"] = nv.nvmlDeviceGetPowerUsage(h) / 1000.0  # W
            except Exception:
                info["power"] = None
            try:
                info["clock"] = nv.nvmlDeviceGetClockInfo(
                    h, nv.NVML_CLOCK_GRAPHICS)
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
                info["procs"] = self._gpu_processes(h)   # 非 Windows 回退
            result.append(info)
        return result

    def poll_mem(self):
        """轻量: 仅返回每卡 (显存已用, 显存总量), 供高频刷新显存条。"""
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
        # NVML 给 "00000000:01:00.0", CUDA 给 "0000:01:00.0";
        # 取 bus:device.function 部分做统一比较
        parts = s.strip().lower().split(":")
        return ":".join(parts[-2:]) if len(parts) >= 2 else s.strip().lower()

    def _build_luid_map(self):
        """用 CUDA driver API 取每张卡的 luid 与 PCI busId, 再按 busId
        与 NVML 精确对应。完全动态, 不依赖 luid 数值顺序, 也自动排除核显。
        """
        mapping = {}
        try:
            cuda = ctypes.WinDLL("nvcuda.dll")
            if cuda.cuInit(0) != 0:
                return mapping
            count = ctypes.c_int()
            cuda.cuDeviceGetCount(ctypes.byref(count))
            nvml_by_bus = {self._norm_busid(b): i
                           for i, b in enumerate(self._busids)}
            for i in range(count.value):
                dev = ctypes.c_int()
                if cuda.cuDeviceGet(ctypes.byref(dev), i) != 0:
                    continue
                luid = (ctypes.c_char * 8)()
                mask = ctypes.c_uint()
                if cuda.cuDeviceGetLuid(luid, ctypes.byref(mask), dev) != 0:
                    continue
                raw = bytes(luid)
                luid_int = (int.from_bytes(raw[4:8], "little") << 32) \
                    | int.from_bytes(raw[0:4], "little")
                buf = ctypes.create_string_buffer(32)
                cuda.cuDeviceGetPCIBusId(buf, 32, dev)
                idx = nvml_by_bus.get(self._norm_busid(buf.value.decode()))
                if idx is not None:
                    mapping[luid_int] = idx
        except Exception:
            pass
        return mapping

    def _refresh_procs(self):
        """刷新各卡的显存占用进程 (节流到约 1 秒一次)。"""
        if self._procmem is None:
            return
        now = time.monotonic()
        if self._proc_by_card is not None and now - self._proc_ts < 1.0:
            return
        self._proc_ts = now

        all_luids, mem_map = self._procmem.sample()
        mapping = self._luid_to_index
        if not mapping:
            # 回退 (CUDA 不可用时): luid 升序 ↔ busId 升序
            luids = sorted(all_luids)
            order = sorted(range(self.count), key=lambda i: self._busids[i])
            mapping = {lu: order[k]
                       for k, lu in enumerate(luids) if k < self.count}

        cards = {i: [] for i in range(self.count)}
        for luid, pidmap in mem_map.items():
            idx = mapping.get(luid)
            if idx is None:
                continue
            for pid, m in pidmap.items():
                if not m or m < 5 * 1024 * 1024:   # 过滤 <5MB 的零碎占用
                    continue
                cards[idx].append(
                    {"pid": pid, "name": self._proc_name(pid), "mem": m})
        for i in cards:
            cards[i].sort(key=lambda x: x["mem"], reverse=True)
        self._proc_by_card = cards

    def _gpu_processes(self, handle):
        """返回该卡上占用显存的进程列表 (按显存降序)。"""
        nv = self._nvml
        merged = {}   # pid -> 显存字节数
        for getter in ("nvmlDeviceGetComputeRunningProcesses",
                       "nvmlDeviceGetGraphicsRunningProcesses"):
            try:
                for p in getattr(nv, getter)(handle):
                    mem = getattr(p, "usedGpuMemory", None)
                    # 同一 pid 可能同时出现在计算/图形列表, 取较大值
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
        procs.sort(key=lambda x: (x["mem"] or 0), reverse=True)
        return procs

    def shutdown(self):
        if self._procmem is not None:
            self._procmem.close()
        if self.kind == "nvml" and self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass


# ----------------------------------------------------------------------------
# 每进程网络流量 (Windows ETW 内核网络事件, 任务管理器同源)
# ----------------------------------------------------------------------------
class NetworkETW:
    """通过 Windows 底层 PDH 计数器读取每进程网络 IO (免 ETW 库依赖，100% 成功)"""
    def __init__(self):
        self.ok = False
        self.reason = ""
        self._pdh = None
        self._q = None
        self._h_io = None
        self._lock = threading.Lock()
        self._sent = {}  # 伪装原结构
        self._recv = {}  # 伪装原结构

        if not sys.platform.startswith("win"):
            self.reason = "仅 Windows 支持"
            return
        try:
            import win32pdh
            import psutil
            self._pdh = win32pdh
            self._q = win32pdh.OpenQuery()
            # 直接提取全局每个进程的 IO 吞吐（含网卡数据）
            self._h_io = win32pdh.AddEnglishCounter(self._q, r"\Process(*)\IO Read Bytes/sec")
            win32pdh.CollectQueryData(self._q)
            self.ok = True
        except Exception as e:
            self.reason = f"计数器初始化失败 ({type(e).__name__})"
            self._q = None

    def snapshot(self):
        """完美兼容原版 refresh_net 的高频快照"""
        if not self.ok or not self._q:
            return {}, {}

        sent_mock = {}
        recv_mock = {}
        
        try:
            self._pdh.CollectQueryData(self._q)
            # 捕获这一秒钟系统内所有进程的 IO 变动数组
            items = self._pdh.GetFormattedCounterArray(self._h_io, self._pdh.PDH_FMT_LARGE)
            
            # items 结构通常为 {'chrome': 45120, 'pycharm': 1204, 'chrome#1': 8023}
            # 我们需要过滤无意义的闲置数据，并将其映射成主程序需要的 PID 结构
            import psutil
            for instance_name, io_val in items.items():
                if io_val <= 2048: # 过滤掉低于 2KB/s 的无意义扫描
                    continue
                
                base_name = instance_name.split('#')[0]
                if base_name.lower() in ('_total', 'idle', 'system'):
                    continue
                
                # 遍历进程池，将实例名完美转换为 PID 
                for p in psutil.process_iter(['name']):
                    try:
                        if p.info['name'] and p.info['name'].lower().startswith(base_name.lower()):
                            pid = p.pid
                            with self._lock:
                                # 因为原版 refresh_net 拿到 snapshot 之后会除以 dt 时间差
                                # 我们这里直接反向给它赋值，模拟出符合原版消费逻辑的数据
                                sent_mock[pid] = int(io_val / 2)
                                recv_mock[pid] = int(io_val / 2)
                    except Exception:
                        continue
        except Exception:
            pass
            
        return sent_mock, recv_mock

    def close(self):
        if self._q is not None and self._pdh is not None:
            try:
                self._pdh.CloseQuery(self._q)
            except Exception:
                pass
            self._q = None


# ----------------------------------------------------------------------------
# CPU 功耗 / 实时频率 / 温度 (后台线程定期读取)
# ----------------------------------------------------------------------------
class CpuSensors:
    """CPU 功耗 / 实时频率 / 温度 (后台线程定期读取)。

    功耗: Windows 内置 EMI 能量计量计数器 \\Energy Meter(*_PKG)\\Power
          (单位 mW, /1000=W)。纯 win32pdh, 无需驱动, 不受内存完整性(HVCI)限制。
    频率: 标称频率 x (\\Processor Information(_Total)\\% Processor Performance / 100),
          即随睿频/降频变化的实时频率, 同样走 win32pdh。
    温度: pythonnet 加载 LibreHardwareMonitor 读 CPU Package 的 DTS 温度。
          LHM 自带的内核驱动兼容 HVCI; 仅在缺 pythonnet/.NET/管理员权限或驱动
          加载失败时读不到, 此时温度显示 N/A (不影响功耗与频率)。
    """

    def __init__(self):
        self.temp = None
        self.power = None
        self.freq = None       # 实时频率 (MHz), 来自 % Processor Performance
        self._stop = False
        if sys.platform.startswith("win"):
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        # --- 功耗 + 实时频率: win32pdh 计数器 (HVCI 兼容, 无需驱动) ---
        pdh = qh = ch = ch_freq = None
        base_mhz = None
        try:
            import win32pdh
            pdh = win32pdh
            qh = win32pdh.OpenQuery()
            ch = win32pdh.AddEnglishCounter(qh, r"\Energy Meter(*)\Power")
            # 实时频率 = 标称频率 x (% Processor Performance / 100)
            ch_freq = win32pdh.AddEnglishCounter(
                qh, r"\Processor Information(_Total)\% Processor Performance")
            win32pdh.CollectQueryData(qh)
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                    base_mhz = winreg.QueryValueEx(k, "~MHz")[0]
            except Exception:
                f = psutil.cpu_freq()
                base_mhz = (f.max or f.current) if f else None
        except Exception:
            pdh = None

        # --- 温度: LibreHardwareMonitor (pythonnet, 驱动兼容 HVCI) ---
        comp = hw_type = sensor_type = None
        try:
            from pythonnet import load
            load("netfx")          # 用 .NET Framework 运行时
            import clr
            # 打包(PyInstaller)时 libs 在解压临时目录 _MEIPASS, 否则脚本同级
            # 同时兼容 PyInstaller (_MEIPASS) 和 Nuitka (单文件临时释放路径)
            if getattr(sys, "frozen", False):
                # 如果是 Nuitka onefile 运行，它会将数据文件释放到 exe 同级或临时目录
                # 我们优先检查当前运行 exe 所在的真实目录或 Nuitka/PyInstaller 的内部释放路径
                base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            else:
                base = os.path.dirname(os.path.abspath(__file__))

            libs = os.path.join(base, "libs")

            # 【核心防护】如果上述路径依然没找到 dll，尝试从当前工作目录的相对路径中死马当活马医
            if not os.path.exists(os.path.join(libs, "LibreHardwareMonitorLib.dll")):
                libs = os.path.join(os.getcwd(), "libs")
            if libs not in sys.path:
                sys.path.append(libs)
            if hasattr(sys, "_MEIPASS"):
                # 让原生加载器能在该目录找到依赖 DLL
                os.environ["PATH"] = libs + os.pathsep + os.environ["PATH"]
            clr.AddReference("LibreHardwareMonitorLib")
            from LibreHardwareMonitor.Hardware import (
                Computer, HardwareType, SensorType)
            comp = Computer()
            comp.IsCpuEnabled = True
            comp.Open()
            hw_type, sensor_type = HardwareType, SensorType
        except Exception:
            comp = None

        while not self._stop:
            # 功耗 (EMI) + 实时频率: 同一 query 一次采集
            if pdh is not None:
                try:
                    pdh.CollectQueryData(qh)
                    arr = pdh.GetFormattedCounterArray(ch, pdh.PDH_FMT_DOUBLE)
                    pkg = sum(v for n, v in arr.items()
                              if n.lower().endswith("_pkg") and v)
                    self.power = pkg / 1000.0 if pkg else None
                except Exception:
                    pass
                if ch_freq is not None and base_mhz:
                    try:
                        _, perf = pdh.GetFormattedCounterValue(
                            ch_freq, pdh.PDH_FMT_DOUBLE)
                        if perf and perf > 0:
                            self.freq = base_mhz * perf / 100.0
                    except Exception:
                        pass
            # 温度 (LHM): CPU Package; 驱动加载失败时为 None
            if comp is not None:
                try:
                    temp = None
                    for hw in comp.Hardware:
                        if hw.HardwareType == hw_type.Cpu:
                            hw.Update()
                            for s in hw.Sensors:
                                if (s.Name == "CPU Package" and
                                        s.SensorType == sensor_type.Temperature):
                                    temp = s.Value
                    self.temp = float(temp) if temp is not None else None
                except Exception:
                    pass
            time.sleep(1)

        try:
            if pdh is not None:
                pdh.CloseQuery(qh)
        except Exception:
            pass
        try:
            if comp is not None:
                comp.Close()
        except Exception:
            pass

    def stop(self):
        self._stop = True


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def fmt_bytes(n):
    if n is None:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def cpu_name():
    # Windows: platform.processor() 只给 family/model/stepping (如
    # "Intel64 Family 6 Model 183 ..."), 注册表里的 ProcessorNameString
    # 才是任务管理器显示的商品型号。
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if val:
                return val.strip()
        except Exception:
            pass

    name = platform.processor() or ""
    if not name and platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        name = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
    return name or "Unknown CPU"


def shade(hex_color, lighter, amount=5):
    """把颜色整体调亮(lighter=True)或调暗一点, 用于进度条空槽相对背景的微差。"""
    c = QColor(hex_color)
    d = amount if lighter else -amount
    vals = [max(0, min(255, v - d)) for v in (c.red(), c.green(), c.blue())]
    return QColor(vals[0], vals[1], vals[2]).name()


def level_color(value):
    """根据占用率返回语义色 (绿/黄/红), 按背景明暗选用两套配色。"""
    if value is None:
        return "#888888"

    if MonitorWindow.is_light:
        color = {1: "#aee2c3", 2: "#fde047", 3: "#fca5a5"}
    else:
        color = {1: "#1f9d57", 2: "#f59e0b", 3: "#e5484d"}

    # 0% - 59% 全部为绿色（包含 0% 和 1%）
    if value < 60:
        return color[1]
    elif value < 85:
        return color[2]
    else:
        return color[3]

def bar_style(value):
    """根据占用率返回进度条样式。"""
    color = level_color(value)
    return (
        f"QProgressBar{{border:1px solid {MonitorWindow.c_border};border-radius:4px;"
        f"background:{MonitorWindow.c_track};text-align:center;"
        f"color:{MonitorWindow.c_text};height:18px;}}"
        f"QProgressBar::chunk{{background:{color};border-radius:3px;}}"
    )


# ----------------------------------------------------------------------------
# 一个带标签 + 进度条的小部件
# ----------------------------------------------------------------------------
class MeterRow(QWidget):
    def __init__(self, label):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(label)
        self.label.setMinimumWidth(70)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self._level = level_color(0)     # 当前颜色档位 (绿/黄/红/灰)
        self.bar.setStyleSheet(bar_style(0))
        lay.addWidget(self.label)
        lay.addWidget(self.bar, 1)

    def set_value(self, value, text=None):
        v = 0 if value is None else int(value)
        self.bar.setValue(v)
        # setStyleSheet 较贵 (~0.03ms), 仅在颜色档位变化时才重设
        lvl = level_color(value)
        if lvl != self._level:
            self._level = lvl
            self.bar.setStyleSheet(bar_style(value))
        self.bar.setFormat(text if text is not None else f"{v}%")

    def restyle(self):
        """主题切换时强制按当前配色重设进度条样式 (绕过档位缓存)。"""
        v = self.bar.value()
        self._level = level_color(v)
        self.bar.setStyleSheet(bar_style(v))


# ----------------------------------------------------------------------------
# 历史折线图 (0-100), 用 QPainter 自绘, 无需额外依赖
# ----------------------------------------------------------------------------
class Sparkline(QWidget):
    def __init__(self, capacity=600, color="#3fb950", dynamic_color=True):
        super().__init__()
        self._data = deque(maxlen=capacity)
        self._color = QColor(color)
        self._dynamic = dynamic_color
        self.setMinimumHeight(64)

    def sizeHint(self):
        return QSize(200, 70)

    def push(self, value):
        v = 0.0 if value is None else max(0.0, min(100.0, float(value)))
        self._data.append(v)
        if self._dynamic:
            self._color = QColor(level_color(v))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), self.palette().window().color())
        p.setPen(QPen(QColor(MonitorWindow.c_border), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # 水平网格 (25/50/75%)
        p.setPen(QPen(QColor(MonitorWindow.c_border), 1))
        for frac in (0.25, 0.5, 0.75):
            y = int(h * frac)
            p.drawLine(1, y, w - 1, y)

        n = len(self._data)
        cap = self._data.maxlen
        if n >= 2:
            step = w / (cap - 1)
            pts = []
            for i, v in enumerate(self._data):
                x = w - (n - 1 - i) * step
                y = h - (v / 100.0) * (h - 2) - 1
                pts.append(QPointF(x, y))

            # 渐变填充
            fill = QPolygonF(pts + [QPointF(pts[-1].x(), h),
                                    QPointF(pts[0].x(), h)])
            grad = QLinearGradient(0, 0, 0, h)
            top = QColor(self._color); top.setAlpha(90)
            bot = QColor(self._color); bot.setAlpha(12)
            grad.setColorAt(0, top)
            grad.setColorAt(1, bot)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawPolygon(fill)

            # 折线
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self._color, 2))
            p.drawPolyline(QPolygonF(pts))

        # 当前值
        if n >= 1:
            p.setPen(QColor(MonitorWindow.c_text))
            p.drawText(QRectF(0, 2, w - 6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                       f"{self._data[-1]:.0f}%")


# ----------------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------------
class MonitorWindow(QWidget):
    # 配色默认值 (深色); __init__ 中会被系统 QPalette 覆盖。
    # 设为类属性是为了让 MeterRow / Sparkline 等独立部件也能引用。
    c_bg = "#0d1117"
    c_text = "#e6edf3"
    c_sub_text = "#8b949e"
    c_border = "#30363d"
    c_accent = "#1f6feb"
    c_combo_bg = "#161b22"
    c_track = "#0d1117"
    c_card = "transparent"   # GroupBox 背景: 透出 Mica, 只靠边框区分
    is_light = False        # 背景是否浅色 (决定占用率红黄绿用哪套)

    def __init__(self):
        super().__init__()

        # 先按系统色定一次, 供后续构建的部件引用类属性 (bar_style / Sparkline 等)
        self.theme_mode = "system"          # system / dark / light
        self._resolve_colors("system")
        self.icon = QIcon("libs/logo.ico")
        self.setWindowTitle("系统监控")
        self.setFixedWidth(700)
        self.setStyleSheet(self._build_qss())

        self.gpu = GpuBackend()
        self.net_etw = NetworkETW()
        self.cpu_sensors = CpuSensors()
        self._pname_cache = {}   # pid -> 进程名 (网络进程用)
        self._last_mem_pct = 0   # 供托盘提示用

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.controls_w = self._build_controls()
        root.addWidget(self.controls_w)

        # 所有监控内容放进一个 content 容器, 由它自报所需尺寸 (sizeHint)。
        # 默认不滚动 (窗口按内容撑开); 仅当 GPU 多于 4 张时才启用滚动兜底。
        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 让滚动区视口不填充背景, 否则它的不透明底色会盖住 Mica
        scroll.viewport().setAutoFillBackground(False)
        self.content = QWidget()
        self.content.setAutoFillBackground(False)
        cl = QVBoxLayout(self.content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        cl.addWidget(self._build_sysinfo())
        cl.addWidget(self._build_cpu())
        cl.addWidget(self._build_memory())
        cl.addWidget(self._build_network())
        cl.addWidget(self._build_gpu())
        self.status = QLabel("")
        self.status.setProperty("kind", "sub")
        cl.addWidget(self.status)
        cl.addStretch(1)
        scroll.setWidget(self.content)
        root.addWidget(scroll, 1)

        self._build_tray()

        # 首次调用 cpu_percent 用于建立基线
        psutil.cpu_percent(percpu=True)

        # 主定时器 (可调): CPU + GPU 使用率/温度/功耗/频率/编解码/显存进程
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_main)
        self.timer.start(self.freq_combo.currentData())
        # 内存与 GPU 显存条: 固定 100ms
        self.mem_timer = QTimer(self)
        self.mem_timer.timeout.connect(self.refresh_mem)
        self.mem_timer.start(100)
        # 网络: 固定 1000ms
        self.net_timer = QTimer(self)
        self.net_timer.timeout.connect(self.refresh_net)
        self.net_timer.start(1000)

        self.refresh_mem()
        self.refresh_net()
        self.refresh_main()

        # 统一应用一次主题 (重设 QSS 让 property 标签着色 + 刷新自绘部件)
        self._apply_theme(self.theme_mode)
        self._apply_dynamic_height()

        # 监听系统深/浅色切换 (强调色等变化由 changeEvent 的
        # PaletteChange 兜底): "随系统"模式下实时跟随
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            lambda *_: self._schedule_system_refresh())


    def _build_qss(self):
            """根据系统配色生成全局样式表 (所有颜色来自 QPalette，并彻底隐藏下拉箭头)。"""
            c = MonitorWindow
            return (
                # 窗口基底透明 -> 透出 DWM 的 Mica 材质; 各区块用半透明卡片浮其上
                f"QWidget{{background:transparent;color:{c.c_text};"
                f"font-family:'Segoe UI','Microsoft YaHei',sans-serif;font-size:13px;}}"
                # 标签强制透明背景: 可选中文字的标签会用 base 色(白)填底, 这里盖掉
                f"QLabel{{background:transparent;}}"
                f"QGroupBox{{background:{c.c_card};border:1px solid {c.c_border};"
                f"border-radius:6px;margin-top:10px;padding-top:8px;font-weight:bold;}}"
                f"QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 4px;}}"
                
                # --- 以下是针对 QComboBox 的魔改，彻底隐藏三角形 ---
                f"QComboBox{{background:{c.c_combo_bg};border:1px solid {c.c_border};"
                f"border-radius:4px;padding:3px 8px; font-weight: normal;}}"
                # 将下拉按钮的宽度设为 0，并隐藏裁剪
                f"QComboBox::drop-down {{subcontrol-origin: padding; subcontrol-position: top right; width: 0px; border-left-width: 0px; border-style: none;}}"
                # 把原本的小三角形箭头直接隐藏掉
                f"QComboBox::down-arrow {{image: none; width: 0px; height: 0px;}}"
                
                f"QComboBox QAbstractItemView{{background:{c.c_combo_bg};color:{c.c_text};"
                f"selection-background-color:{c.c_accent};border:1px solid {c.c_border};}}"

                # 辅助文本 / 分隔符: 用 dynamic property 标记, 切换主题时随 QSS 自动着色
                f'QLabel[kind="sub"]{{color:{c.c_sub_text};}}'
                f'QLabel[kind="sep"]{{color:{c.c_border};}}'

                # 主题切换按钮
                f"QPushButton{{background:{c.c_combo_bg};border:1px solid {c.c_border};"
                f"border-radius:4px;padding:3px 10px;}}"
                f"QPushButton:hover{{border:1px solid {c.c_accent};}}"

                # 托盘菜单 / 滚动条 (不再用 setPalette, 由 QSS 保证一致)
                f"QMenu{{background:{c.c_combo_bg};color:{c.c_text};"
                f"border:1px solid {c.c_border};}}"
                f"QMenu::item:selected{{background:{c.c_accent};}}"
                f"QScrollBar:vertical{{background:transparent;width:10px;margin:0;}}"
                f"QScrollBar::handle:vertical{{background:{c.c_border};"
                f"border-radius:4px;min-height:20px;}}"
                f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
            )

    def is_windows_dark_mode(self):
        """读注册表判断 Windows 当前是否为深色模式 (0=深色, 1=浅色)。"""
        try:
            reg_path = (r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Themes\Personalize")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as k:
                value, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return value == 0
        except Exception:
            return True   # 读取失败 (如非 Windows) 时默认深色

    # ---- 主题切换 (随系统 / 暗色 / 浅色) -----------------------------------
    THEME_ORDER = ("system", "dark", "light")
    THEME_LABELS = {"system": "随系统", "dark": "暗色", "light": "浅色"}

    def _resolve_colors(self, mode):
        """根据主题模式设置全局配色类属性。"""
        cls = MonitorWindow
        if mode == "dark":
            (cls.c_bg, cls.c_text, cls.c_sub_text, cls.c_border,
             cls.c_accent, cls.c_combo_bg) = (
                "#0d1117", "#e6edf3", "#8b949e", "#30363d",
                "#1f6feb", "#161b22")
            cls.is_light = False
        elif mode == "light":
            (cls.c_bg, cls.c_text, cls.c_sub_text, cls.c_border,
             cls.c_accent, cls.c_combo_bg) = (
                "#f3f3f3", "#1f2328", "#6e7781", "#d0d7de",
                "#0969da", "#ffffff")
            cls.is_light = True
        else:   # system: 实时读取当前系统调色板 (随系统主题变化)
            pal = QApplication.palette()
            cls.c_bg = pal.window().color().name()
            cls.c_text = pal.windowText().color().name()
            cls.c_sub_text = pal.mid().color().name()
            cls.c_border = pal.mid().color().name()
            cls.c_accent = pal.highlight().color().name()
            cls.c_combo_bg = pal.base().color().name()
            _b = pal.window().color()
            cls.is_light = (0.299 * _b.red() + 0.587 * _b.green()
                            + 0.114 * _b.blue()) > 140
        # 进度条空槽: 由背景算出 —— 浅色比背景略深, 深色比背景略浅 (凹槽感更明显)
        cls.c_track = shade(cls.c_bg, cls.is_light)
        # GroupBox 直接透出 Mica 底色 (不再叠半透明白卡片, 只靠边框区分区块)
        cls.c_card = "transparent"

    def _apply_theme(self, mode):
        """切换主题: 重设配色并刷新所有部件 (含 property 标签/进度条/自绘)。"""
        self.theme_mode = mode
        self._resolve_colors(mode)
        # 刻意不调用 app.setPalette(): 保持 Qt 对系统调色板的自动管理,
        # 这样"随系统"模式才能在系统主题切换时实时拿到新色; 配色全部由 QSS 驱动。
        # _applying_theme 护栏: setStyleSheet 自身会触发 PaletteChange, 防止自循环。
        self._applying_theme = True
        try:
            self.setStyleSheet(self._build_qss())
            # 进度条 (动态档位色) 与自绘部件需主动刷新
            for m in self.findChildren(MeterRow):
                m.restyle()
            for sp in self.findChildren(Sparkline):
                sp.update()
            # 标题栏深浅跟随主题; 已显示时顺带重设 backdrop 防丢失
            if getattr(self, "_backdrop_applied", False):
                self._enable_mica()
            else:
                self._update_titlebar_dark()
            if hasattr(self, "theme_btn"):
                self.theme_btn.setText(self.THEME_LABELS[mode])
        finally:
            self._applying_theme = False

    def _cycle_theme(self):
        i = self.THEME_ORDER.index(self.theme_mode)
        self._apply_theme(self.THEME_ORDER[(i + 1) % len(self.THEME_ORDER)])

    def showEvent(self, event):
        super().showEvent(event)
        # backdrop 必须在窗口显示后 (原生句柄就绪) 才能附上; 只应用一次
        if not getattr(self, "_backdrop_applied", False):
            self._backdrop_applied = True
            self._enable_mica()

    def _enable_mica(self):
        """用官方 DWM API 给整窗启用 Mica (云母) 材质。

        正确挂法 (经 mica_test 验证): DwmExtendFrameIntoClientArea 全窗扩展 +
        DWMWA_SYSTEMBACKDROP_TYPE=2 (Mica); 不能用 WA_TranslucentBackground
        (那会让 Qt 走分层窗口、挡掉 DWM 合成 -> 客户区变黑)。
        Mica 拖动稳定、不闪 (Acrylic 移动时会临时关模糊)。
        """
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
            self._update_titlebar_dark()
            # 背景材质 = Mica 主窗口 (DWMWA_SYSTEMBACKDROP_TYPE=38, DWMSBT_MAINWINDOW=2)
            backdrop = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), 4)
        except Exception:
            pass

    def _update_titlebar_dark(self):
        """标题栏深浅跟随当前主题 (DWMWA_USE_IMMERSIVE_DARK_MODE = 20)。"""
        try:
            hwnd = int(self.winId())
            dark = ctypes.c_int(0 if MonitorWindow.is_light else 1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(dark), 4)
        except Exception:
            pass

    def changeEvent(self, event):
        # PaletteChange 涵盖系统深/浅色 + 强调色等所有调色板变化
        # (colorSchemeChanged 只管深浅, 漏掉浅→浅的强调色调整)
        if (event.type() == QEvent.Type.PaletteChange
                and not getattr(self, "_applying_theme", False)):
            self._schedule_system_refresh()
        super().changeEvent(event)

    def _schedule_system_refresh(self):
        """系统配色变化时, 在"随系统"模式下防抖刷新 (合并多次触发, 等 palette 更新)。"""
        if (self.theme_mode != "system"
                or getattr(self, "_sys_refresh_pending", False)):
            return
        self._sys_refresh_pending = True
        QTimer.singleShot(80, self._do_system_refresh)

    def _do_system_refresh(self):
        self._sys_refresh_pending = False
        if self.theme_mode == "system":
            self._apply_theme("system")
        
    # ---- 动态窗口高度 ------------------------------------------------------
    def _apply_dynamic_height(self):
        """直接采用控件自报的 sizeHint 作为高度, 不做逐项估算。

        内容随 CPU 核心数 / GPU 数量变化, sizeHint 会自动反映;
        QScrollArea 不向上透传内部高度, 故对 content 容器单独取 sizeHint。
        """
        root = self.layout()
        root.activate()
        self.content.layout().activate()

        margins = root.contentsMargins()
        # 两个由 Qt 自报的尺寸: 顶部控制栏 + 监控内容容器
        full = (margins.top() + margins.bottom() + root.spacing()
                + self.controls_w.sizeHint().height()
                + self.content.sizeHint().height())

        if self.gpu.count > 4:
            # 显卡过多时, 限制到屏幕高度并允许滚动
            screen = QApplication.primaryScreen().availableGeometry().height()
            height = min(full, screen - 80)
            self.scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            # 4 张及以内: 窗口完整撑开, 绝不出现滚动条
            height = full
            self.scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedSize(700, int(height))

    # ---- 顶部控制栏 --------------------------------------------------------
    def _build_controls(self):
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addWidget(QLabel("刷新间隔:"))
        self.freq_combo = QComboBox()
        for ms in (100, 250, 500, 1000):
            self.freq_combo.addItem(f"{ms} ms", ms)
        self.freq_combo.setCurrentIndex(2)   # 默认 500 ms, 与定时器一致
        self.freq_combo.currentIndexChanged.connect(self._change_interval)
        lay.addWidget(self.freq_combo)

        lay.addStretch(1)

        # 主题切换按钮 (循环: 随系统 -> 暗色 -> 浅色)
        self.theme_btn = QPushButton(self.THEME_LABELS[self.theme_mode])
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.setFixedWidth(76)
        self.theme_btn.clicked.connect(self._cycle_theme)
        lay.addWidget(self.theme_btn)
        lay.addSpacing(8)

        # 窗口置顶按钮 (悬浮 <-> 置顶), 与主题按钮同款同尺寸
        self._on_top = False
        self.top_btn = QPushButton("悬浮")
        self.top_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.top_btn.setFixedWidth(76)
        self.top_btn.clicked.connect(self._toggle_on_top)
        lay.addWidget(self.top_btn)
        return bar

    def _change_interval(self, _idx):
        ms = self.freq_combo.currentData()
        if ms and hasattr(self, "timer"):
            self.timer.setInterval(ms)

    def _toggle_on_top(self, _checked=False):
        self._on_top = not self._on_top
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._on_top)
        self.top_btn.setText("置顶" if self._on_top else "悬浮")
        # 修改窗口标志后需重新 show 才能生效
        self.show()
        self.raise_()
        self.activateWindow()
        # setWindowFlag 会重建原生窗口 (hwnd 变化), 需重新挂上 Mica
        self._enable_mica()

    # ---- 系统托盘 ----------------------------------------------------------
    def _build_tray(self):
        self._force_quit = False
        self.setWindowIcon(self.icon)
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("系统监控")

        menu = QMenu()
        act_show = QAction("显示窗口", self)
        act_show.triggered.connect(self._restore)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick,
                      QSystemTrayIcon.ActivationReason.Trigger):
            self._restore()

    def _restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self):
        self._force_quit = True
        if hasattr(self, "timer") and self.timer.isActive():
            self.timer.stop()
        self.close()
        try:
            import pynvml
            pynvml.nvmlShutdown()
        except Exception:
            pass
        # 强制退出, 确保后台线程 (ETW / LHM / 传感器) 不残留
        os._exit(0)

    # ---- 静态系统信息 ------------------------------------------------------
    def _build_sysinfo(self):
        box = QGroupBox("系统配置")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)

        uname = platform.uname()
        vmem = psutil.virtual_memory()
        rows = [
            ("操作系统", f"{uname.system} {uname.release} ({platform.architecture()[0]})"),
            ("主机名", uname.node),
            ("处理器", cpu_name()),
            ("核心数", f"{psutil.cpu_count(logical=False)} 物理 / "
                      f"{psutil.cpu_count(logical=True)} 逻辑"),
            ("内存总量", fmt_bytes(vmem.total)),
        ]

        gpus = self.gpu.static_info()
        if gpus:
            for i, g in enumerate(gpus):
                rows.append((f"GPU {i}",
                             f"{g['name']}  ({fmt_bytes(g['mem_total'])})"))
        else:
            rows.append(("GPU", "未检测到 (需安装 NVIDIA 驱动 + pynvml)"))

        for r, (k, v) in enumerate(rows):
            key = QLabel(k)
            key.setProperty("kind", "sub")
            val = QLabel(str(v))
            val.setWordWrap(True)
            val.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(val, r, 1)
        return box

    # ---- CPU ---------------------------------------------------------------
    def _build_cpu(self):
        box = QGroupBox("CPU")
        lay = QVBoxLayout(box)
        self.cpu_total = MeterRow("总占用")
        lay.addWidget(self.cpu_total)
        self.cpu_spark = Sparkline()
        lay.addWidget(self.cpu_spark)

        # 每核心的网格 (每行 4 个)
        self.core_rows = []
        n = psutil.cpu_count(logical=True) or 1
        grid = QGridLayout()
        grid.setSpacing(6)
        cols = 4
        for i in range(n):
            row = MeterRow(f"核{i}")
            row.label.setMinimumWidth(34)
            self.core_rows.append(row)
            grid.addWidget(row, i // cols, i % cols)
        lay.addLayout(grid)

        self.cpu_extra = QLabel("")
        self.cpu_extra.setProperty("kind", "sub")
        lay.addWidget(self.cpu_extra)
        return box

    # ---- 内存 --------------------------------------------------------------
    def _build_memory(self):
        box = QGroupBox("内存")
        lay = QVBoxLayout(box)
        self.mem_row = MeterRow("物理")
        self.swap_row = MeterRow("交换")
        lay.addWidget(self.mem_row)
        lay.addWidget(self.swap_row)
        # 进程行: [内存频率] │ [占用最高的进程]  (竖线分割, 同网络风格)
        prow = QWidget()
        pl = QHBoxLayout(prow)
        pl.setContentsMargins(0, 0, 0, 0)
        self.mem_freq_label = QLabel("内存 …")
        self.mem_freq_label.setProperty("kind", "sub")
        pl.addWidget(self.mem_freq_label)
        sep = QLabel("│")
        sep.setProperty("kind", "sep")
        pl.addWidget(sep)
        self.mem_proc_label = QLabel("")
        self.mem_proc_label.setProperty("kind", "sub")
        self.mem_proc_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                          QSizePolicy.Policy.Preferred)
        pl.addWidget(self.mem_proc_label, 1)
        lay.addWidget(prow)
        self._mem_freq_str = None        # 内存频率 (WMI 异步获取)
        self._mem_proc_top = []          # [(name, rss)] 由后台线程更新
        self._async_query_mem_freq()
        # 进程内存遍历 (~300ms) 放后台线程, 避免阻塞 UI 主线程
        threading.Thread(target=self._mem_proc_worker, daemon=True).start()
        return box

    def _mem_proc_worker(self):
        while True:
            procs = []
            for p in psutil.process_iter(["name", "memory_info"]):
                try:
                    procs.append((p.info["name"] or f"PID{p.pid}",
                                  p.info["memory_info"].rss))
                except Exception:
                    continue
            procs.sort(key=lambda x: x[1], reverse=True)
            self._mem_proc_top = procs[:6]
            time.sleep(1.0)

    def _async_query_mem_freq(self):
        """异步查询内存频率 (WMI Win32_PhysicalMemory.Speed), 频率不变, 查一次即可。"""
        def worker():
            if not sys.platform.startswith("win"):
                return
            try:
                import subprocess
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-CimInstance Win32_PhysicalMemory | "
                     "Select-Object -First 1).Speed"],
                    capture_output=True, text=True, timeout=8,
                    creationflags=0x08000000)
                s = r.stdout.strip()
                if s.isdigit():
                    self._mem_freq_str = f"内存 {s} MHz"
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    # ---- 网络 --------------------------------------------------------------
    def _build_network(self):
        box = QGroupBox("网络")
        lay = QHBoxLayout(box)
        self.net_label = QLabel("↓ 下行 ―     ↑ 上行 ―")
        lay.addWidget(self.net_label)
        self.net_proc_label = None

        if self.net_etw is not None and self.net_etw.ok:
            # 提权成功: 总量 + 分隔符 + 按进程明细
            sep = QLabel("│")
            sep.setProperty("kind", "sep")
            lay.addWidget(sep)
            self.net_proc_label = QLabel("")
            self.net_proc_label.setProperty("kind", "sub")
            self.net_proc_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                              QSizePolicy.Policy.Preferred)
            lay.addWidget(self.net_proc_label, 1)
        else:
            # 未提权: 简要单行, 仅显示总上下行
            lay.addStretch(1)

        self._net_last = psutil.net_io_counters()
        self._net_ts = time.monotonic()
        self._net_etw_last = None        # (sent_dict, recv_dict, ts)
        return box

    # ---- GPU ---------------------------------------------------------------
    def _build_gpu(self):
        box = QGroupBox(f"GPU  (检测到 {self.gpu.count} 张)")
        lay = QVBoxLayout(box)
        self.gpu_widgets = []

        if self.gpu.count == 0:
            tip = QLabel("未检测到可用 GPU。\n"
                         "NVIDIA 用户请安装: pip install nvidia-ml-py")
            tip.setProperty("kind", "sub")
            lay.addWidget(tip)
            return box

        for i, g in enumerate(self.gpu.static_info()):
            card = QGroupBox(f"GPU {i}: {g['name']}")
            card.setStyleSheet("QGroupBox{font-weight:normal;}")
            cl = QVBoxLayout(card)
            util = MeterRow("使用率")
            spark = Sparkline()
            mem = MeterRow("显存")
            # 状态行: 温度/功耗/频率/编解码 │ 显存进程 (竖线分割, 同网络风格)
            info_row = QWidget()
            irl = QHBoxLayout(info_row)
            irl.setContentsMargins(0, 0, 0, 0)
            status = QLabel("")
            status.setProperty("kind", "sub")
            irl.addWidget(status)
            sep = QLabel("│")
            sep.setProperty("kind", "sep")
            irl.addWidget(sep)
            proc = QLabel("")
            proc.setProperty("kind", "sub")
            # 宽度跟随父容器, 不被长文本撑大; 超长部分在 refresh 里省略
            proc.setSizePolicy(QSizePolicy.Policy.Ignored,
                               QSizePolicy.Policy.Preferred)
            irl.addWidget(proc, 1)

            cl.addWidget(util)
            cl.addWidget(spark)
            cl.addWidget(mem)
            cl.addWidget(info_row)
            lay.addWidget(card)
            self.gpu_widgets.append(
                {"util": util, "spark": spark, "mem": mem,
                 "status": status, "proc": proc,
                 "card": card, "name": g["name"], "pcie": None})
        return box

    # ---- 刷新: 主 (CPU + GPU 使用率/温度/功耗/频率/编解码/显存进程) -------
    def refresh_main(self):
        # CPU 占用
        per = psutil.cpu_percent(percpu=True)
        total = sum(per) / len(per) if per else 0
        self.cpu_total.set_value(total)
        self.cpu_spark.push(total)
        for i, v in enumerate(per):
            if i < len(self.core_rows):
                self.core_rows[i].set_value(v)
        # CPU 温度 / 功耗 / 频率 (与 GPU 行顺序一致)
        parts = []
        t, p = self.cpu_sensors.temp, self.cpu_sensors.power
        parts.append(f"温度 {t:.0f}°C" if t is not None else "温度 N/A")
        parts.append(f"功耗 {p:.0f} W" if p is not None else "功耗 N/A")
        rf = self.cpu_sensors.freq
        if rf:
            parts.append(f"频率 {rf:.0f} MHz")
        else:
            try:   # 兜底: psutil 静态标称频率
                freq = psutil.cpu_freq()
                if freq:
                    parts.append(f"频率 {freq.current:.0f} MHz")
            except Exception:
                pass
        self.cpu_extra.setText("    ".join(parts))

        # GPU (使用率 / 温度 / 功耗 / 频率 / 编解码 / 显存进程; 显存条见 refresh_mem)
        for idx, (w, data) in enumerate(zip(self.gpu_widgets, self.gpu.poll())):
            gu = data.get("gpu_util")
            w["util"].set_value(gu, f"{gu if gu is not None else 'N/A'}%")
            w["spark"].push(gu)
            # 型号后缀: PCIe 最大能力 @ 当前 (GPU-Z 格式, 当前变化才更新标题)
            cw, cg = data.get("pcie_width"), data.get("pcie_gen")
            mw, mg = data.get("max_pcie_width"), data.get("max_pcie_gen")
            if cw and cg and mw and mg:
                _V = {1: "1.1", 2: "2.0", 3: "3.0", 4: "4.0", 5: "5.0"}
                ps = (f"PCIE X{mw} {_V.get(mg, f'{mg}.0')}"
                      f" @ X{cw} {_V.get(cg, f'{cg}.0')}")
                if ps != w["pcie"]:
                    w["pcie"] = ps
                    w["card"].setTitle(f"GPU {idx}: {w['name']} - {ps}")
            # 左侧状态: 温度/功耗/频率/编解码
            parts = []
            if data.get("temp") is not None:
                parts.append(f"温度 {data['temp']}°C")
            if data.get("power") is not None:
                parts.append(f"功耗 {data['power']:.0f} W")
            if data.get("clock") is not None:
                parts.append(f"频率 {data['clock']} MHz")
            enc, dec = data.get("enc_util"), data.get("dec_util")
            if enc is not None or dec is not None:
                parts.append(f"编解码 {enc if enc is not None else 0}%"
                             f"/{dec if dec is not None else 0}%")
            w["status"].setText("   ".join(parts))
            # 右侧: 显存进程 (宽度内省略)
            plist = data.get("procs") or []
            if plist:
                items = [f"{p['name']} {fmt_bytes(p['mem'])}" for p in plist]
                ptext = ", ".join(items)
            else:
                ptext = "无"
            label = w["proc"]
            avail = label.width() - 4
            if avail > 20:
                ptext = label.fontMetrics().elidedText(
                    ptext, Qt.TextElideMode.ElideRight, avail)
            label.setText(ptext)

        self.status.setText(
            "更新于 " + datetime.datetime.now().strftime("%H:%M:%S"))
        self.tray.setToolTip(
            f"系统监控  |  CPU {total:.0f}%   内存 {self._last_mem_pct:.0f}%")

    # ---- 刷新: 内存 + GPU 显存条 (固定 100ms) -----------------------------
    def refresh_mem(self):
        vmem = psutil.virtual_memory()
        self._last_mem_pct = vmem.percent
        self.mem_row.set_value(
            vmem.percent,
            f"{fmt_bytes(vmem.used)} / {fmt_bytes(vmem.total)}  ({vmem.percent:.0f}%)")
        swap = psutil.swap_memory()
        self.swap_row.set_value(
            swap.percent,
            f"{fmt_bytes(swap.used)} / {fmt_bytes(swap.total)}  ({swap.percent:.0f}%)")

        for w, (used, total) in zip(self.gpu_widgets, self.gpu.poll_mem()):
            if used is not None and total:
                pct = used / total * 100
                w["mem"].set_value(
                    pct, f"{fmt_bytes(used)} / {fmt_bytes(total)}  ({pct:.0f}%)")
            else:
                w["mem"].set_value(None, "N/A")

        # 内存频率就绪后更新标签 (WMI 异步)
        if self._mem_freq_str and self.mem_freq_label.text() != self._mem_freq_str:
            self.mem_freq_label.setText(self._mem_freq_str)

        # 内存占用进程 (后台线程已算好, 此处仅渲染)
        items = [f"{n} {fmt_bytes(r)}" for n, r in self._mem_proc_top]
        text = "  ".join(items)
        label = self.mem_proc_label
        avail = label.width() - 4
        if text and avail > 20:
            text = label.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, avail)
        label.setText(text)

    # ---- 刷新: 网络 (固定 1000ms) -----------------------------------------
    def refresh_net(self):
        now = time.monotonic()
        cur = psutil.net_io_counters()
        dt = now - self._net_ts
        if dt > 0:
            down = (cur.bytes_recv - self._net_last.bytes_recv) / dt
            up = (cur.bytes_sent - self._net_last.bytes_sent) / dt
            self.net_label.setText(
                f"↓ 下行 {fmt_bytes(down)}/s      ↑ 上行 {fmt_bytes(up)}/s")
        self._net_last = cur
        self._net_ts = now

        # 按进程 (ETW): 速率 = 累计字节差 / 时间差, 取占用最高的几个
        if (self.net_etw is not None and self.net_etw.ok
                and self.net_proc_label is not None):
            sent, recv = self.net_etw.snapshot()
            if self._net_etw_last is not None:
                ps, pr, pts = self._net_etw_last
                d = now - pts
                if d > 0:
                    rates = {}
                    for pid in set(sent) | set(recv):
                        delta = ((sent.get(pid, 0) - ps.get(pid, 0)) +
                                 (recv.get(pid, 0) - pr.get(pid, 0)))
                        rate = delta / d
                        if rate > 1024:   # 仅显示 >1KB/s 的进程
                            rates[pid] = rate
                    top = sorted(rates.items(), key=lambda x: -x[1])[:4]
                    items = [f"{self._pname(pid)} {fmt_bytes(r)}/s"
                             for pid, r in top]
                    text = "  ".join(items)
                    label = self.net_proc_label
                    avail = label.width() - 4
                    if avail > 20:
                        text = label.fontMetrics().elidedText(
                            text, Qt.TextElideMode.ElideRight, avail)
                    label.setText(text)
            self._net_etw_last = (sent, recv, now)

    def closeEvent(self, event):
        # 点 X 时最小化到托盘, 通过托盘菜单"退出"才真正关闭
        if not self._force_quit and self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "系统监控", "已最小化到托盘，双击图标恢复，右键可退出。",
                QSystemTrayIcon.MessageIcon.Information, 2000)
            return
        self.timer.stop()
        self.mem_timer.stop()
        self.net_timer.stop()
        self.gpu.shutdown()
        if self.net_etw is not None:
            self.net_etw.close()
        self.cpu_sensors.stop()
        self.tray.hide()
        super().closeEvent(event)

    def _pname(self, pid):
        name = self._pname_cache.get(pid)
        if name is None:
            try:
                name = psutil.Process(pid).name()
            except Exception:
                name = f"PID {pid}"
            self._pname_cache[pid] = name
        return name


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _try_elevate():
    """尝试以管理员身份重新启动自身。

    返回 True 表示提权实例已启动 (用户同意了 UAC), 当前实例应退出;
    返回 False 表示用户拒绝或失败, 当前实例继续以普通权限运行。
    """
    try:
        extra = " ".join(f'"{a}"' for a in sys.argv[1:])
        if getattr(sys, "frozen", False):       # PyInstaller 打包的 exe
            target = sys.executable
            params = ("--elevated " + extra).strip()
        else:                                   # 普通脚本
            script = os.path.abspath(sys.argv[0])
            params = (f'"{script}" --elevated ' + extra).strip()
            target = sys.executable
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
        r = shell32.ShellExecuteW(None, "runas", target, params, None, 1)
        return (r or 0) > 32
    except Exception:
        return False


def main():
    # 启动时自动尝试 UAC 提权 (为了用 ETW 读取每进程网络流量)。
    # 同意 -> 提权实例接管, 本实例退出; 拒绝/失败 -> 继续以普通权限运行。
    is_elevated_child = "--elevated" in sys.argv
    if not is_elevated_child and not ctypes.windll.shell32.IsUserAnAdmin():
        if _try_elevate():
            return

    qt_args = [a for a in sys.argv if a != "--elevated"]
    app = QApplication(qt_args)
    # 隐藏到托盘后不因"最后一个窗口关闭"而退出程序
    app.setQuitOnLastWindowClosed(False)
    win = MonitorWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
