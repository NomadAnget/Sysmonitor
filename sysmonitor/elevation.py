import ctypes
import os
import sys


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def try_elevate():
    try:
        extra = " ".join(f'"{a}"' for a in sys.argv[1:])
        if getattr(sys, "frozen", False):
            target = sys.executable
            params = ("--elevated " + extra).strip()
        else:
            target = sys.executable
            params = ("-m sysmonitor --elevated " + extra).strip()
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        r = shell32.ShellExecuteW(None, "runas", target, params, None, 1)
        return (r or 0) > 32
    except Exception:
        return False
