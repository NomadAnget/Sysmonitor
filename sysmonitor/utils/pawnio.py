import ctypes
import logging
import os
from ctypes import wintypes

from .utils import res_path

logging.basicConfig(level=logging.INFO)


def is_pawnio_installed():
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO",
        ) as k:
            return winreg.QueryValueEx(k, "DisplayVersion")[0]
    except Exception:
        return None


def install_pawnio_elevated():
    setup = res_path("libs", "PawnIO_setup.exe")
    if not os.path.exists(setup):
        logging.warning("PawnIO setup not found at %s", setup)
        return False

    class _ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    try:
        info = _ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "runas"
        info.lpFile = setup
        info.lpParameters = "-install -silent"
        info.nShow = 1
        shell32 = ctypes.windll.shell32
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            raise ctypes.WinError()

        if ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 120000) == 0x102:
            logging.warning("PawnIO setup timed out")
            return False
        exit_code = wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        ok = exit_code.value in (0, 3010) or is_pawnio_ready()
        logging.info("PawnIO setup exit=%d%s", exit_code.value, " ok" if ok else "")
        return ok
    except Exception as e:
        logging.warning("PawnIO setup failed: %s", e)
        return False
    finally:
        if "info" in locals() and info.hProcess:
            ctypes.windll.kernel32.CloseHandle(info.hProcess)


def is_pawnio_ready():
    return bool(is_pawnio_installed())
