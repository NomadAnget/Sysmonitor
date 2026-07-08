import sys

from PyQt6.QtWidgets import QApplication

from .elevation import is_admin, try_elevate
from .single_instance import acquire_single_instance, notify_existing_instance, IPC_NAME
from .window import MonitorWindow


def main():
    if sys.platform.startswith("win") and not is_admin():
        if try_elevate():
            return
        print("警告: 提权失败，温度传感器可能不可用", file=sys.stderr)

    app = QApplication(sys.argv)

    _mutex_handle = acquire_single_instance()
    if _mutex_handle is None:
        notify_existing_instance()
        return

    app.setQuitOnLastWindowClosed(False)
    win = MonitorWindow()
    win.start_single_instance_server(IPC_NAME)
    win.show()
    sys.exit(app.exec())
