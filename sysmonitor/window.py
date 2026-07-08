import ctypes
import datetime
import os
import platform
import subprocess
import sys
import threading
import time
import winreg

import psutil
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QScrollArea,
    QFrame,
    QSystemTrayIcon,
    QMenu,
    QSizePolicy,
    QComboBox,
    QPushButton,
)
from PyQt6.QtNetwork import QLocalServer

from .config import ThemeConfig, THEME_ORDER, THEME_LABELS, resolve_colors
from .utils import res_path, fmt_bytes, cpu_name
from .widgets import MeterRow, Sparkline, CoreGrid
from .monitors import GpuBackend, NetworkETW, CpuSensors
from .single_instance import IPC_NAME


class MonitorWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.theme_mode = "system"
        resolve_colors("system")
        self.icon = QIcon(res_path("libs", "logo.ico"))
        self.setWindowTitle("系统监控")
        self.setFixedWidth(700)
        self.setStyleSheet(self._build_qss())

        self.gpu = GpuBackend()
        if self.gpu.kind == "sim":
            self.setWindowTitle("系统监控 — AMD 仿真模式")
            self._sim_mode = True
        else:
            self._sim_mode = False
        self.net_etw = NetworkETW()
        self.cpu_sensors = CpuSensors()
        self._pname_cache = {}
        self._last_mem_pct = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.controls_w = self._build_controls()
        root.addWidget(self.controls_w)

        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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

        psutil.cpu_percent(percpu=True)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_main)
        interval = self.freq_combo.currentData()
        self.timer.start(interval)
        self.gpu.set_interval(interval)
        self.mem_timer = QTimer(self)
        self.mem_timer.timeout.connect(self.refresh_mem)
        self.mem_timer.start(500)
        self.net_timer = QTimer(self)
        self.net_timer.timeout.connect(self.refresh_net)
        self.net_timer.start(1000)

        self.refresh_mem()
        self.refresh_net()
        self.refresh_main()

        self._apply_theme(self.theme_mode)
        self._apply_dynamic_height()

        QApplication.instance().styleHints().colorSchemeChanged.connect(
            lambda *_: self._schedule_system_refresh()
        )

    def _build_qss(self):
        c = ThemeConfig
        return (
            f"QWidget{{background:transparent;color:{c.c_text};"
            f"font-family:'Segoe UI','Microsoft YaHei',sans-serif;font-size:13px;}}"
            f"QLabel{{background:transparent;}}"
            f"QGroupBox{{background:{c.c_card};border:1px solid {c.c_border};"
            f"border-radius:6px;margin-top:10px;padding-top:8px;font-weight:bold;}}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 4px;}}"
            f"QComboBox{{background:{c.c_combo_bg};border:1px solid {c.c_border};"
            f"border-radius:4px;padding:3px 8px; font-weight: normal;}}"
            f"QComboBox::drop-down {{subcontrol-origin: padding; subcontrol-position: top right; width: 0px; border-left-width: 0px; border-style: none;}}"
            f"QComboBox::down-arrow {{image: none; width: 0px; height: 0px;}}"
            f"QComboBox QAbstractItemView{{background:{c.c_combo_bg};color:{c.c_text};"
            f"selection-background-color:{c.c_accent};border:1px solid {c.c_border};}}"
            f'QLabel[kind="sub"]{{color:{c.c_sub_text};}}'
            f'QLabel[kind="sep"]{{color:{c.c_border};}}'
            f"QPushButton{{background:{c.c_combo_bg};border:1px solid {c.c_border};"
            f"border-radius:4px;padding:3px 10px;}}"
            f"QPushButton:hover{{border:1px solid {c.c_accent};}}"
            f"QMenu{{background:{c.c_combo_bg};color:{c.c_text};"
            f"border:1px solid {c.c_border};}}"
            f"QMenu::item:selected{{background:{c.c_accent};}}"
            f"QScrollBar:vertical{{background:transparent;width:10px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:{c.c_border};"
            f"border-radius:4px;min-height:20px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
        )

    def is_windows_dark_mode(self):
        try:
            reg_path = (
                r"Software\Microsoft\Windows\CurrentVersion"
                r"\Themes\Personalize"
            )
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as k:
                value, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return value == 0
        except Exception:
            return True

    def _apply_theme(self, mode):
        self.theme_mode = mode
        resolve_colors(mode)
        self._applying_theme = True
        try:
            self.setStyleSheet(self._build_qss())
            for m in self.findChildren(MeterRow):
                m.restyle()
            for sp in self.findChildren(Sparkline):
                sp.update()
            if getattr(self, "_backdrop_applied", False):
                self._enable_mica()
            else:
                self._update_titlebar_dark()
            if hasattr(self, "theme_btn"):
                self.theme_btn.setText(THEME_LABELS[mode])
        finally:
            self._applying_theme = False

    def _cycle_theme(self):
        i = THEME_ORDER.index(self.theme_mode)
        self._apply_theme(THEME_ORDER[(i + 1) % len(THEME_ORDER)])

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_backdrop_applied", False):
            self._backdrop_applied = True
            self._enable_mica()

    def _enable_mica(self):
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
            self._update_titlebar_dark()
            backdrop = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), 4)
        except Exception:
            pass

    def _update_titlebar_dark(self):
        try:
            hwnd = int(self.winId())
            dark = ctypes.c_int(0 if ThemeConfig.is_light else 1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
        except Exception:
            pass

    def changeEvent(self, event):
        if event.type() == QEvent.Type.PaletteChange and not getattr(
            self, "_applying_theme", False
        ):
            self._schedule_system_refresh()
        super().changeEvent(event)

    def _schedule_system_refresh(self):
        if self.theme_mode != "system" or getattr(self, "_sys_refresh_pending", False):
            return
        self._sys_refresh_pending = True
        QTimer.singleShot(80, self._do_system_refresh)

    def _do_system_refresh(self):
        self._sys_refresh_pending = False
        if self.theme_mode == "system":
            self._apply_theme("system")

    def _apply_dynamic_height(self):
        root = self.layout()
        root.activate()
        self.content.layout().activate()

        margins = root.contentsMargins()
        full = (
            margins.top()
            + margins.bottom()
            + root.spacing()
            + self.controls_w.sizeHint().height()
            + self.content.sizeHint().height()
        )

        screen = QApplication.primaryScreen().availableGeometry().height()
        max_h = screen - 80
        if full > max_h:
            height = max_h
            self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            height = full
            self.scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
        self.setFixedSize(700, int(height))

    def _build_controls(self):
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addWidget(QLabel("刷新间隔:"))
        self.freq_combo = QComboBox()
        for ms in (100, 250, 500, 1000):
            self.freq_combo.addItem(f"{ms} ms", ms)
        self.freq_combo.setCurrentIndex(2)
        self.freq_combo.currentIndexChanged.connect(self._change_interval)
        lay.addWidget(self.freq_combo)

        lay.addStretch(1)

        self.theme_btn = QPushButton(THEME_LABELS[self.theme_mode])
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.setFixedWidth(76)
        self.theme_btn.clicked.connect(self._cycle_theme)
        lay.addWidget(self.theme_btn)
        lay.addSpacing(8)

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
            self.gpu.set_interval(ms)

    def _toggle_on_top(self, _checked=False):
        self._on_top = not self._on_top
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._on_top)
        self.top_btn.setText("置顶" if self._on_top else "悬浮")
        self.show()
        self.raise_()
        self.activateWindow()
        self._enable_mica()

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
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self._restore()

    def _restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def start_single_instance_server(self, name):
        QLocalServer.removeServer(name)
        self._ipc = QLocalServer(self)
        self._ipc.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._ipc.newConnection.connect(self._on_instance_ping)
        self._ipc.listen(name)

    def _on_instance_ping(self):
        while self._ipc.hasPendingConnections():
            conn = self._ipc.nextPendingConnection()
            conn.close()
        self._restore()

    def _quit(self):
        self._force_quit = True
        if hasattr(self, "timer") and self.timer.isActive():
            self.timer.stop()
        self.close()
        self.gpu.shutdown()
        os._exit(0)

    def _build_sysinfo(self):
        box = QGroupBox("系统配置")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)

        uname = platform.uname()
        vmem = psutil.virtual_memory()
        rows = [
            (
                "操作系统",
                f"{uname.system} {uname.release} ({platform.architecture()[0]})",
            ),
            ("主机名", uname.node),
            ("处理器", cpu_name()),
            (
                "核心数",
                f"{psutil.cpu_count(logical=False)} 物理 / "
                f"{psutil.cpu_count(logical=True)} 逻辑",
            ),
            ("内存总量", fmt_bytes(vmem.total)),
        ]

        gpus = self.gpu.static_info()
        if gpus:
            for i, g in enumerate(gpus):
                label = f"{g['name']}"
                if g.get("mem_total"):
                    label += f"  ({fmt_bytes(g['mem_total'])})"
                rows.append((f"GPU {i}", label))
        else:
            rows.append(
                ("GPU", "未检测到 (NVIDIA: nvidia-ml-py, AMD: pythonnet + LHM)")
            )

        for r, (k, v) in enumerate(rows):
            key = QLabel(k)
            key.setProperty("kind", "sub")
            val = QLabel(str(v))
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(val, r, 1)
        return box

    def _build_cpu(self):
        box = QGroupBox("CPU")
        lay = QVBoxLayout(box)
        self.cpu_total = MeterRow("总占用")
        lay.addWidget(self.cpu_total)
        self.cpu_spark = Sparkline()
        lay.addWidget(self.cpu_spark)

        self._compact_mode = False
        n = psutil.cpu_count(logical=True) or 1
        if n > 32:
            self._compact_mode = True
            self.core_grid = CoreGrid(n)
            lay.addWidget(self.core_grid)
        else:
            self.core_rows = []
            grid = QGridLayout()
            grid.setSpacing(6)
            cols = 4
            for i in range(n):
                row = MeterRow(f"核{i}")
                row.label.setMinimumWidth(34)
                self.core_rows.append(row)
                grid.addWidget(row, i // cols, i % cols)
            lay.addLayout(grid)

        cpu_row = QWidget()
        cpu_lay = QHBoxLayout(cpu_row)
        cpu_lay.setContentsMargins(0, 0, 0, 0)
        self.cpu_extra = QLabel("")
        cpu_lay.addWidget(self.cpu_extra)
        sep = QLabel("│")
        sep.setProperty("kind", "sep")
        cpu_lay.addWidget(sep)
        self.cpu_proc_label = QLabel("")
        self.cpu_proc_label.setProperty("kind", "sub")
        self.cpu_proc_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        cpu_lay.addWidget(self.cpu_proc_label, 1)
        lay.addWidget(cpu_row)

        self._cpu_proc_top = []
        return box

    def _build_memory(self):
        box = QGroupBox("内存")
        lay = QVBoxLayout(box)
        self.mem_row = MeterRow("物理")
        self.swap_row = MeterRow("交换")
        lay.addWidget(self.mem_row)
        lay.addWidget(self.swap_row)
        prow = QWidget()
        pl = QHBoxLayout(prow)
        pl.setContentsMargins(0, 0, 0, 0)
        self.mem_freq_label = QLabel("内存 …")
        pl.addWidget(self.mem_freq_label)
        sep = QLabel("│")
        sep.setProperty("kind", "sep")
        pl.addWidget(sep)
        self.mem_proc_label = QLabel("")
        self.mem_proc_label.setProperty("kind", "sub")
        self.mem_proc_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        pl.addWidget(self.mem_proc_label, 1)
        lay.addWidget(prow)
        self._mem_freq_str = None
        self._mem_proc_top = []
        self._async_query_mem_freq()
        threading.Thread(target=self._proc_worker, daemon=True).start()
        return box

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

    def _build_network(self):
        box = QGroupBox("网络")
        lay = QHBoxLayout(box)
        self.net_label = QLabel("↓ 下行 ―     ↑ 上行 ―")
        lay.addWidget(self.net_label)
        self.net_proc_label = None

        if self.net_etw is not None and self.net_etw.ok:
            sep = QLabel("│")
            sep.setProperty("kind", "sep")
            lay.addWidget(sep)
            self.net_proc_label = QLabel("")
            self.net_proc_label.setProperty("kind", "sub")
            self.net_proc_label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            lay.addWidget(self.net_proc_label, 1)
        else:
            lay.addStretch(1)

        self._net_last = psutil.net_io_counters()
        self._net_ts = time.monotonic()
        self._net_etw_last = None
        return box

    def _build_gpu(self):
        box = QGroupBox(f"GPU  (检测到 {self.gpu.count} 张)")
        lay = QVBoxLayout(box)
        self.gpu_widgets = []

        if self.gpu.count == 0:
            tip = QLabel(
                "未检测到可用 GPU。\nNVIDIA 用户请安装: pip install nvidia-ml-py"
            )
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
            info_row = QWidget()
            irl = QHBoxLayout(info_row)
            irl.setContentsMargins(0, 0, 0, 0)
            status = QLabel("")
            irl.addWidget(status)
            sep = QLabel("│")
            sep.setProperty("kind", "sep")
            irl.addWidget(sep)
            proc = QLabel("")
            proc.setProperty("kind", "sub")
            proc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            irl.addWidget(proc, 1)

            cl.addWidget(util)
            cl.addWidget(spark)
            cl.addWidget(mem)
            cl.addWidget(info_row)
            lay.addWidget(card)
            self.gpu_widgets.append(
                {
                    "util": util,
                    "spark": spark,
                    "mem": mem,
                    "status": status,
                    "proc": proc,
                    "card": card,
                    "name": g["name"],
                    "pcie": None,
                }
            )
        return box

    def refresh_main(self):
        per = psutil.cpu_percent(percpu=True)
        total = sum(per) / len(per) if per else 0
        self.cpu_total.set_value(total)
        self.cpu_spark.push(total)
        core_freqs = self.cpu_sensors.per_core_freqs
        if self._compact_mode:
            self.core_grid.update_data(per, core_freqs)
        else:
            for i, v in enumerate(per):
                if i < len(self.core_rows):
                    f = core_freqs[i] if i < len(core_freqs) and core_freqs[i] else None
                    if f:
                        self.core_rows[i].set_value(v, f"{v:.0f}%  {f:.0f}MHz")
                    else:
                        self.core_rows[i].set_value(v)

        proc_count = len(psutil.pids())
        p = self.cpu_sensors.power
        parts = [f"进程 {proc_count}"]
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
        self.cpu_extra.setText("    ".join(parts))
        cp_items = [f"{n} {v:.0f}%" for n, v in self._cpu_proc_top]
        cp_text = "  ".join(cp_items)
        cp_avail = self.cpu_proc_label.width() - 4
        if cp_text and cp_avail > 20:
            cp_text = self.cpu_proc_label.fontMetrics().elidedText(
                cp_text, Qt.TextElideMode.ElideRight, cp_avail
            )
        if self.cpu_proc_label.text() != cp_text:
            self.cpu_proc_label.setText(cp_text or "")

        for idx, (w, data) in enumerate(zip(self.gpu_widgets, self.gpu.poll())):
            gu = data.get("gpu_util")
            w["util"].set_value(gu, f"{gu if gu is not None else 'N/A'}%")
            w["spark"].push(gu)

            cw, cg = data.get("pcie_width"), data.get("pcie_gen")
            mw, mg = data.get("max_pcie_width"), data.get("max_pcie_gen")
            if cw and cg and mw and mg:
                _V = {1: "1.1", 2: "2.0", 3: "3.0", 4: "4.0", 5: "5.0"}
                ps = (
                    f"PCIE X{mw} {_V.get(mg, f'{mg}.0')}"
                    f" @ X{cw} {_V.get(cg, f'{cg}.0')}"
                )
                if ps != w["pcie"]:
                    w["pcie"] = ps
                    w["card"].setTitle(f"GPU {idx}: {w['name']} - {ps}")

            parts = []
            if data.get("temp") is not None:
                parts.append(f"温度 {data['temp']}°C")
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
            w["status"].setText("   ".join(parts))

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
                    ptext, Qt.TextElideMode.ElideRight, avail
                )
            label.setText(ptext)

        self.status.setText("更新于 " + datetime.datetime.now().strftime("%H:%M:%S"))
        self.tray.setToolTip(
            f"系统监控  |  CPU {total:.0f}%   内存 {self._last_mem_pct:.0f}%"
        )

    def refresh_mem(self):
        vmem = psutil.virtual_memory()
        self._last_mem_pct = vmem.percent
        self.mem_row.set_value(
            vmem.percent,
            f"{fmt_bytes(vmem.used)} / {fmt_bytes(vmem.total)}  ({vmem.percent:.0f}%)",
        )
        swap = psutil.swap_memory()
        self.swap_row.set_value(
            swap.percent,
            f"{fmt_bytes(swap.used)} / {fmt_bytes(swap.total)}  ({swap.percent:.0f}%)",
        )

        for w, (used, total) in zip(self.gpu_widgets, self.gpu.poll_mem()):
            if used is not None and total:
                pct = used / total * 100
                w["mem"].set_value(
                    pct, f"{fmt_bytes(used)} / {fmt_bytes(total)}  ({pct:.0f}%)"
                )
            else:
                w["mem"].set_value(None, "N/A")

        if self._mem_freq_str and self.mem_freq_label.text() != self._mem_freq_str:
            self.mem_freq_label.setText(self._mem_freq_str)

        items = [f"{n} {fmt_bytes(r)}" for n, r in self._mem_proc_top]
        text = "  ".join(items)
        label = self.mem_proc_label
        avail = label.width() - 4
        if text and avail > 20:
            text = label.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, avail
            )

        label.setText(text)

    def refresh_net(self):
        now = time.monotonic()
        cur = psutil.net_io_counters()
        dt = now - self._net_ts
        if dt > 0:
            down = (cur.bytes_recv - self._net_last.bytes_recv) / dt
            up = (cur.bytes_sent - self._net_last.bytes_sent) / dt
            self.net_label.setText(
                f"↓ 下行 {fmt_bytes(down)}/s      ↑ 上行 {fmt_bytes(up)}/s"
            )
        self._net_last = cur
        self._net_ts = now

        if (
            self.net_etw is not None
            and self.net_etw.ok
            and self.net_proc_label is not None
        ):
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
                    text = "  ".join(items)
                    label = self.net_proc_label
                    avail = label.width() - 4
                    if avail > 20:
                        text = label.fontMetrics().elidedText(
                            text, Qt.TextElideMode.ElideRight, avail
                        )
                    label.setText(text)
            self._net_etw_last = (sent, recv, now)

    def closeEvent(self, event):
        if not self._force_quit and self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "系统监控",
                "已最小化到托盘，双击图标恢复，右键可退出。",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
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
