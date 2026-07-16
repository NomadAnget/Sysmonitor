import sys

from PyQt6.QtWidgets import QApplication

from .single_instance import acquire_single_instance, notify_existing_instance, IPC_NAME
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

    from ..utils.pawnio import ensure_pawnio, uninstall_pawnio

    splash.set_step(0, len(_STEPS), _STEPS[0])
    ensure_pawnio()

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

    app.aboutToQuit.connect(uninstall_pawnio)
    win.show()
    sys.exit(app.exec())
