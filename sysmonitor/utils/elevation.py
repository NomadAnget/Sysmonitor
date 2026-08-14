import ctypes
import sys


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def try_elevate(extra_arg=""):
    args = [*sys.argv[1:]]
    if extra_arg and extra_arg not in args:
        args.append(extra_arg)
    extra = " ".join(f'"{a}"' for a in args)
    compiled = "__compiled__" in globals()
    if getattr(sys, "frozen", False) or compiled:
        params = extra
    else:
        params = "-m sysmonitor " + extra
    executable = sys.argv[0] if compiled else sys.executable
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    shell32.ShellExecuteW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    r = shell32.ShellExecuteW(None, "runas", executable, params.strip(), None, 1)
    return (r or 0) > 32
