import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import psutil
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..utils.utils import fmt_bytes
from .monitors import GpuBackend, NetworkETW, CpuSensors


@dataclass
class CpuData:
    total: float
    per: list
    freqs: list
    power: object = None
    temp: object = None
    freq: object = None
    extra_text: str = ""
    procs: list = field(default_factory=list)


@dataclass
class MemData:
    mem_pct: float = 0.0
    mem_used: int = 0
    mem_total: int = 0
    swap_pct: float = 0.0
    swap_used: int = 0
    swap_total: int = 0
    gpu_mem: list = field(default_factory=list)
    freq: object = None
    procs: list = field(default_factory=list)


@dataclass
class NetData:
    down: float = 0.0
    up: float = 0.0
    procs_text: str = ""


@dataclass
class GpuItem:
    util: object = None
    mem_pct: object = None
    mem_used: object = None
    mem_total: object = None
    temp: object = None
    power: object = None
    clock: object = None
    enc_util: object = None
    dec_util: object = None
    pcie_str: object = None
    status_text: str = ""
    procs_text: str = ""


class MonitorData(QObject):
    cpu_updated = pyqtSignal(object)
    mem_updated = pyqtSignal(object)
    net_updated = pyqtSignal(object)
    gpu_updated = pyqtSignal(list)

    def __init__(self, gpu=None, net_etw=None, cpu_sensors=None, parent=None):
        super().__init__(parent)

        self.gpu = gpu if gpu is not None else GpuBackend()
        self.net_etw = net_etw if net_etw is not None else NetworkETW()
        self.cpu_sensors = cpu_sensors if cpu_sensors is not None else CpuSensors()

        self._mem_freq_str = None
        self._mem_proc_top = []
        self._cpu_proc_top = []
        self._pname_cache = {}

        self._net_last = psutil.net_io_counters()
        self._net_ts = time.monotonic()
        self._net_etw_last = None

        self._stop = False
        self._interval = 0.5

        self._event_cpu = threading.Event()
        self._event_mem = threading.Event()
        self._event_net = threading.Event()

        psutil.cpu_percent(percpu=True)

        self._timer_main = QTimer(self)
        self._timer_main.timeout.connect(self._event_cpu.set)
        self._timer_main.start(500)

        self._timer_mem = QTimer(self)
        self._timer_mem.timeout.connect(self._event_mem.set)
        self._timer_mem.start(500)

        self._timer_net = QTimer(self)
        self._timer_net.timeout.connect(self._event_net.set)
        self._timer_net.start(1000)

        threading.Thread(target=self._proc_worker, daemon=True).start()
        threading.Thread(target=self._cpu_worker, daemon=True).start()
        threading.Thread(target=self._mem_worker, daemon=True).start()
        threading.Thread(target=self._net_worker, daemon=True).start()
        self._async_query_mem_freq()

    def set_interval(self, ms):
        self._interval = ms / 1000.0
        self._timer_main.setInterval(ms)
        self.gpu.set_interval(ms)

    def _cpu_worker(self):
        while not self._stop:
            self._event_cpu.wait()
            if self._stop:
                break
            self._event_cpu.clear()
            try:
                self._do_cpu()
            except Exception:
                pass

    def _mem_worker(self):
        while not self._stop:
            self._event_mem.wait()
            if self._stop:
                break
            self._event_mem.clear()
            try:
                self._do_mem()
            except Exception:
                pass

    def _net_worker(self):
        while not self._stop:
            self._event_net.wait()
            if self._stop:
                break
            self._event_net.clear()
            try:
                self._do_net()
            except Exception:
                pass

    def _proc_worker(self):
        while True:
            mem_procs = []
            cpu_procs = []
            for p in psutil.process_iter(["name", "memory_info", "cpu_percent"]):
                try:
                    name = p.info["name"] or f"PID{p.pid}"
                    mem = p.info["memory_info"]
                    if mem:
                        mem_procs.append((name, mem.rss))
                    cpu = p.info["cpu_percent"]
                    if cpu and name.lower() != "system idle process":
                        cpu_procs.append((name, cpu))
                except Exception:
                    continue
            mem_procs.sort(key=lambda x: x[1], reverse=True)
            cpu_procs.sort(key=lambda x: x[1], reverse=True)
            self._mem_proc_top = mem_procs[:6]
            self._cpu_proc_top = cpu_procs[:5]
            time.sleep(1.0)

    def _async_query_mem_freq(self):
        def worker():
            if not sys.platform.startswith("win"):
                return
            try:
                r = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "(Get-CimInstance Win32_PhysicalMemory | "
                        "Select-Object -First 1).Speed",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    creationflags=0x08000000,
                )
                s = r.stdout.strip()
                if s.isdigit():
                    self._mem_freq_str = f"内存 {s} MHz"
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _do_cpu(self):
        per = psutil.cpu_percent(percpu=True)
        total = sum(per) / len(per) if per else 0
        freqs = self.cpu_sensors.per_core_freqs

        p = self.cpu_sensors.power
        t = self.cpu_sensors.temp
        if t is not None:
            parts = [f"温度 {t:.0f}°C"]
        else:
            parts = ["温度 N/A"]
        parts.append(f"功耗 {p:.0f} W" if p is not None else "功耗 N/A")
        rf = self.cpu_sensors.freq
        if rf:
            parts.append(f"频率 {rf:.0f} MHz")
        else:
            try:
                freq = psutil.cpu_freq()
                if freq:
                    parts.append(f"频率 {freq.current:.0f} MHz")
            except Exception:
                pass
        extra_text = "    ".join(parts)

        self.cpu_updated.emit(
            CpuData(
                total=total,
                per=per,
                freqs=freqs,
                power=p,
                temp=t,
                freq=rf,
                extra_text=extra_text,
                procs=list(self._cpu_proc_top),
            )
        )

        gpu_items = []
        for data in self.gpu.poll():
            gu = data.get("gpu_util")
            mem_used = data.get("mem_used")
            mem_total = data.get("mem_total")
            if mem_used is not None and mem_total:
                mem_pct = mem_used / mem_total * 100
            else:
                mem_pct = None

            cw, cg = data.get("pcie_width"), data.get("pcie_gen")
            mw, mg = data.get("max_pcie_width"), data.get("max_pcie_gen")
            pcie_str = None
            if cw and cg and mw and mg:
                _V = {1: "1.1", 2: "2.0", 3: "3.0", 4: "4.0", 5: "5.0"}
                pcie_str = (
                    f"PCIE X{mw} {_V.get(mg, f'{mg}.0')}"
                    f" @ X{cw} {_V.get(cg, f'{cg}.0')}"
                )

            parts = []
            gt = data.get("temp")
            if gt is not None:
                parts.append(f"温度 {gt:.0f}°C")
            if data.get("power") is not None:
                parts.append(f"功耗 {data['power']:.0f} W")
            if data.get("clock") is not None:
                parts.append(f"频率 {data['clock']} MHz")
            enc, dec = data.get("enc_util"), data.get("dec_util")
            if enc is not None or dec is not None:
                parts.append(
                    f"编解码 {enc if enc is not None else 0}%"
                    f"/{dec if dec is not None else 0}%"
                )
            status_text = "   ".join(parts)

            plist = data.get("procs") or []
            if plist:
                items = [f"{p['name']} {fmt_bytes(p['mem'])}" for p in plist]
                procs_text = ", ".join(items)
            else:
                procs_text = "无"

            gpu_items.append(
                GpuItem(
                    util=gu,
                    mem_pct=mem_pct,
                    mem_used=mem_used,
                    mem_total=mem_total,
                    temp=gt,
                    power=data.get("power"),
                    clock=data.get("clock"),
                    enc_util=data.get("enc_util"),
                    dec_util=data.get("dec_util"),
                    pcie_str=pcie_str,
                    status_text=status_text,
                    procs_text=procs_text,
                )
            )
        self.gpu_updated.emit(gpu_items)

    def _do_mem(self):
        vmem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        gpu_mem = self.gpu.poll_mem()

        self.mem_updated.emit(
            MemData(
                mem_pct=vmem.percent,
                mem_used=vmem.used,
                mem_total=vmem.total,
                swap_pct=swap.percent,
                swap_used=swap.used,
                swap_total=swap.total,
                gpu_mem=gpu_mem,
                freq=self._mem_freq_str,
                procs=list(self._mem_proc_top),
            )
        )

    def _do_net(self):
        now = time.monotonic()
        cur = psutil.net_io_counters()
        dt = now - self._net_ts
        if dt > 0:
            down = (cur.bytes_recv - self._net_last.bytes_recv) / dt
            up = (cur.bytes_sent - self._net_last.bytes_sent) / dt
        else:
            down = up = 0.0
        self._net_last = cur
        self._net_ts = now

        procs_text = ""
        if self.net_etw is not None and self.net_etw.ok:
            sent, recv = self.net_etw.snapshot()
            if self._net_etw_last is not None:
                ps, pr, pts = self._net_etw_last
                d = now - pts
                if d > 0:
                    rates = {}
                    for pid in set(sent) | set(recv):
                        delta = (sent.get(pid, 0) - ps.get(pid, 0)) + (
                            recv.get(pid, 0) - pr.get(pid, 0)
                        )
                        rate = delta / d
                        if rate > 1024:
                            rates[pid] = rate
                    top = sorted(rates.items(), key=lambda x: -x[1])[:4]
                    items = [f"{self._pname(pid)} {fmt_bytes(r)}/s" for pid, r in top]
                    procs_text = "  ".join(items)
            self._net_etw_last = (sent, recv, now)

        self.net_updated.emit(NetData(down=down, up=up, procs_text=procs_text))

    def _pname(self, pid):
        name = self._pname_cache.get(pid)
        if name is None:
            try:
                name = psutil.Process(pid).name()
            except Exception:
                name = f"PID {pid}"
            self._pname_cache[pid] = name
        return name

    def stop(self):
        self._stop = True
        self._event_cpu.set()
        self._event_mem.set()
        self._event_net.set()
        self._timer_main.stop()
        self._timer_mem.stop()
        self._timer_net.stop()
        self.gpu.shutdown()
        if self.net_etw is not None:
            self.net_etw.close()
        self.cpu_sensors.stop()
