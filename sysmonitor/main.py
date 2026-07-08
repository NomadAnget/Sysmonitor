import ctypes
import sys

from PyQt6.QtWidgets import QApplication

from .elevation import is_admin, try_elevate
from .single_instance import (
    acquire_single_instance,
    notify_existing_instance,
    MUTEX_NAME,
    IPC_NAME,
)
from .window import MonitorWindow

_mutex_handle = None


def main():
    global _mutex_handle
    is_elevated_child = "--elevated" in sys.argv
    qt_args = [a for a in sys.argv if a != "--elevated"]
    app = QApplication(qt_args)

    _mutex_handle = acquire_single_instance(retries=5 if is_elevated_child else 1)
    if _mutex_handle is None:
        notify_existing_instance()
        return

    if not is_elevated_child and not ctypes.windll.shell32.IsUserAnAdmin():
        if try_elevate():
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
            return

    app.setQuitOnLastWindowClosed(False)
    win = MonitorWindow()
    win.start_single_instance_server(IPC_NAME)
    win.show()
    sys.exit(app.exec())
