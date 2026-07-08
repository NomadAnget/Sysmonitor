import sys

from PyQt6.QtWidgets import QApplication

from .single_instance import acquire_single_instance, notify_existing_instance, IPC_NAME
from .window import MonitorWindow


def main():
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
