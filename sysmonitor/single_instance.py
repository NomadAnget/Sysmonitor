import ctypes
import time

MUTEX_NAME = "Local\\SysMonitor_SingleInstance_Mutex"
IPC_NAME = "SysMonitor_SingleInstance_IPC"


def acquire_single_instance(retries=1):
    k32 = ctypes.windll.kernel32
    for i in range(retries):
        handle = k32.CreateMutexW(None, False, MUTEX_NAME)
        if k32.GetLastError() != 183:
            return handle
        k32.CloseHandle(handle)
        if i < retries - 1:
            time.sleep(0.4)
    return None


def notify_existing_instance():
    try:
        from PyQt6.QtNetwork import QLocalSocket

        sock = QLocalSocket()
        sock.connectToServer(IPC_NAME)
        sock.waitForConnected(500)
        sock.disconnectFromServer()
    except Exception:
        pass
