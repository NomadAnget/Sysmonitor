import ctypes
import datetime
import os
import platform
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
from PyQt6.QtCore import QTimer
from PyQt6.QtNetwork import QLocalServer

from ..utils.config import ThemeConfig, THEME_ORDER, THEME_LABELS, resolve_colors
from ..utils.utils import res_path, fmt_bytes, cpu_name
from ..ui.widgets import MeterRow, Sparkline, CoreGrid
from ..data.monitor_data import MonitorData
from .single_instance import IPC_NAME


class MonitorWindow(QWidget):
    def __init__(self, data=None, parent=None):
        super().__init__(parent)

        self.theme_mode = "system"
        resolve_colors("system")
        self.icon = QIcon(res_path("libs", "logo.ico"))
        self.setWindowTitle("系统监控")
        self.setFixedWidth(700)
        self.setStyleSheet(self._build_qss())

        self._data = data if data is not None else MonitorData()
        if self._data.gpu.kind == "sim":
            self.setWindowTitle("系统监控 — AMD 仿真模式")
            self._sim_mode = True
        else:
            self._sim_mode = False
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

        self._data.cpu_updated.connect(self._on_cpu)
        self._data.mem_updated.connect(self._on_mem)
        self._data.net_updated.connect(self._on_net)
        self._data.gpu_updated.connect(self._on_gpu)

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
        if ms:
            self._data.set_interval(ms)

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
        self.close()
        self._data.stop()
        QTimer.singleShot(0, QApplication.instance().quit)

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

        gpus = self._data.gpu.static_info()
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
        return box

    def _build_network(self):
        box = QGroupBox("网络")
        lay = QHBoxLayout(box)
        self.net_label = QLabel("↓ 下行 ―     ↑ 上行 ―")
        lay.addWidget(self.net_label)
        self.net_proc_label = None

        if self._data.net_etw is not None and self._data.net_etw.ok:
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
        return box

    def _build_gpu(self):
        box = QGroupBox(f"GPU  (检测到 {self._data.gpu.count} 张)")
        lay = QVBoxLayout(box)
        self.gpu_widgets = []

        if self._data.gpu.count == 0:
            tip = QLabel(
                "未检测到可用 GPU。\nNVIDIA 用户请安装: pip install nvidia-ml-py"
            )
            tip.setProperty("kind", "sub")
            lay.addWidget(tip)
            return box

        for i, g in enumerate(self._data.gpu.static_info()):
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

    def _on_cpu(self, data):
        self.cpu_total.set_value(data.total)
        self.cpu_spark.push(data.total)

        if self._compact_mode:
            self.core_grid.update_data(data.per, data.freqs)
        else:
            for i, v in enumerate(data.per):
                if i < len(self.core_rows):
                    f = data.freqs[i] if i < len(data.freqs) and data.freqs[i] else None
                    if f:
                        self.core_rows[i].set_value(v, f"{v:.0f}%  {f:.0f}MHz")
                    else:
                        self.core_rows[i].set_value(v)

        self.cpu_extra.setText(data.extra_text)

        cp_items = [f"{n} {v:.0f}%" for n, v in data.procs]
        cp_text = "  ".join(cp_items)
        cp_avail = self.cpu_proc_label.width() - 4
        if cp_text and cp_avail > 20:
            cp_text = self.cpu_proc_label.fontMetrics().elidedText(
                cp_text, Qt.TextElideMode.ElideRight, cp_avail
            )
        if self.cpu_proc_label.text() != cp_text:
            self.cpu_proc_label.setText(cp_text or "")

        self.status.setText("更新于 " + datetime.datetime.now().strftime("%H:%M:%S"))
        self.tray.setToolTip(
            f"系统监控  |  CPU {data.total:.0f}%   内存 {self._last_mem_pct:.0f}%"
        )

    def _on_gpu(self, items):
        for idx, (w, item) in enumerate(zip(self.gpu_widgets, items)):
            w["util"].set_value(
                item.util,
                f"{item.util if item.util is not None else 'N/A'}%",
            )
            w["spark"].push(item.util)

            if item.pcie_str and item.pcie_str != w["pcie"]:
                w["pcie"] = item.pcie_str
                w["card"].setTitle(f"GPU {idx}: {w['name']} - {item.pcie_str}")

            w["status"].setText(item.status_text)

            ptext = item.procs_text
            label = w["proc"]
            avail = label.width() - 4
            if avail > 20:
                ptext = label.fontMetrics().elidedText(
                    ptext, Qt.TextElideMode.ElideRight, avail
                )
            label.setText(ptext)

    def _on_mem(self, data):
        self._last_mem_pct = data.mem_pct
        self.mem_row.set_value(
            data.mem_pct,
            f"{fmt_bytes(data.mem_used)} / {fmt_bytes(data.mem_total)}  "
            f"({data.mem_pct:.0f}%)",
        )
        self.swap_row.set_value(
            data.swap_pct,
            f"{fmt_bytes(data.swap_used)} / {fmt_bytes(data.swap_total)}  "
            f"({data.swap_pct:.0f}%)",
        )

        for w, (used, total) in zip(self.gpu_widgets, data.gpu_mem):
            if used is not None and total:
                pct = used / total * 100
                w["mem"].set_value(
                    pct, f"{fmt_bytes(used)} / {fmt_bytes(total)}  ({pct:.0f}%)"
                )
            else:
                w["mem"].set_value(None, "N/A")

        if data.freq and self.mem_freq_label.text() != data.freq:
            self.mem_freq_label.setText(data.freq)

        items = [f"{n} {fmt_bytes(r)}" for n, r in data.procs]
        text = "  ".join(items)
        label = self.mem_proc_label
        avail = label.width() - 4
        if text and avail > 20:
            text = label.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, avail
            )
        label.setText(text)

    def _on_net(self, data):
        self.net_label.setText(
            f"↓ 下行 {fmt_bytes(data.down)}/s      ↑ 上行 {fmt_bytes(data.up)}/s"
        )

        if self.net_proc_label is not None and data.procs_text:
            text = data.procs_text
            label = self.net_proc_label
            avail = label.width() - 4
            if avail > 20:
                text = label.fontMetrics().elidedText(
                    text, Qt.TextElideMode.ElideRight, avail
                )
            label.setText(text)

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
        self._data.stop()
        self.tray.hide()
        super().closeEvent(event)
