import sys

from PyQt6.QtWidgets import QApplication

from .single_instance import acquire_single_instance, notify_existing_instance, IPC_NAME
from .splash import SplashWindow
from .pawnio import ensure_pawnio
from .monitors import CpuSensors, GpuBackend, NetworkETW
from .window import MonitorWindow

_STEPS = (
    "正在加载系统信息…",
    "正在检查硬件驱动…",
    "正在初始化温度传感器…",
    "正在检测显示适配器…",
    "正在启动网络监控…",
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

    splash.set_step(0, len(_STEPS), _STEPS[0])
    QApplication.processEvents()

    splash.set_step(1, len(_STEPS), _STEPS[1])
    ensure_pawnio()

    splash.set_step(2, len(_STEPS), _STEPS[2])
    cpu_sensors = CpuSensors()

    splash.set_step(3, len(_STEPS), _STEPS[3])
    gpu = GpuBackend()

    splash.set_step(4, len(_STEPS), _STEPS[4])
    net_etw = NetworkETW()

    splash.set_status("正在启动界面…")

    win = MonitorWindow(gpu=gpu, net_etw=net_etw, cpu_sensors=cpu_sensors)
    win.start_single_instance_server(IPC_NAME)

    splash.close()
    del splash

    win.show()
    sys.exit(app.exec())
