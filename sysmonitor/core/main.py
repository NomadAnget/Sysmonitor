import sys

from PyQt6.QtWidgets import QApplication

from .single_instance import (
    IPC_NAME,
    acquire_single_instance,
    notify_existing_instance,
    release_single_instance,
)
from .splash import SplashWindow

_STEPS = (
    "检查 PawnIO 驱动…",
    "初始化 CPU 传感器…",
    "检测 GPU…",
    "初始化网络监控…",
)


def main():
    app = QApplication(sys.argv)

    handle = acquire_single_instance()
    if handle is None:
        notify_existing_instance()
        return

    app.setQuitOnLastWindowClosed(False)

    splash = SplashWindow()
    splash.show()
    QApplication.processEvents()

    splash.set_step(0, len(_STEPS), _STEPS[0])

    from ..utils.elevation import is_admin, try_elevate
    from ..utils.pawnio import install_pawnio_elevated, is_pawnio_ready

    temp_requested = "--enable-cpu-temperature" in sys.argv
    if not is_admin():
        if splash.prompt(
            "CPU 温度读取需要管理员权限。\n\n"
            "继续后 SysMonitor 将重启并显示 Windows 权限确认。",
            "启用并重启",
            "跳过",
        ):
            splash.close()
            release_single_instance(handle)
            if try_elevate("--enable-cpu-temperature"):
                return
            splash.show()
            splash.prompt(
                "未获得管理员权限。SysMonitor 将继续启动，但 CPU 温度不可用。",
                "知道了",
            )
    elif not is_pawnio_ready() and (
        temp_requested
        or splash.prompt(
            "CPU 温度读取需要安装 PawnIO 驱动。",
            "安装",
            "跳过",
        )
    ):
        splash.set_step(0, len(_STEPS), "正在安装 CPU 温度驱动…")
        if not install_pawnio_elevated():
            splash.prompt(
                "未能安装 PawnIO 驱动，SysMonitor 将继续启动，但 CPU 温度不可用。",
                "知道了",
            )

    from ..data.monitors import CpuSensors

    splash.set_step(1, len(_STEPS), _STEPS[1])
    cpu_sensors = CpuSensors()

    from ..data.monitors import GpuBackend

    splash.set_step(2, len(_STEPS), _STEPS[2])
    gpu = GpuBackend()

    from ..data.monitors import NetworkETW

    splash.set_step(3, len(_STEPS), _STEPS[3])
    net_etw = NetworkETW()

    from ..data.monitor_data import MonitorData
    from .window import MonitorWindow

    data = MonitorData(gpu=gpu, net_etw=net_etw, cpu_sensors=cpu_sensors)
    win = MonitorWindow(data=data)
    win.start_single_instance_server(IPC_NAME)

    splash.close()
    del splash

    win.show()
    sys.exit(app.exec())
