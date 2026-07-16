import logging
import os
import subprocess

from .utils import res_path

logging.basicConfig(level=logging.INFO)


def _is_admin():
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


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


def _device_working():
    try:
        import ctypes
        from ctypes import wintypes

        handle = ctypes.windll.kernel32.CreateFileW(
            "\\\\.\\PawnIO",
            0,
            0,
            None,
            3,
            0,
            None,
        )
        if handle != -1:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


def _run_setup(args):
    setup = res_path("libs", "PawnIO_setup.exe")
    if not os.path.exists(setup):
        import logging

        logging.warning("PawnIO setup not found at %s", setup)
        return False
    try:
        r = subprocess.run([setup] + args, capture_output=True, text=True, timeout=120)
        ok = r.returncode == 0 or r.returncode == 3010
        import logging

        logging.info(
            "PawnIO setup %s exit=%d%s%s",
            args,
            r.returncode,
            " REBOOT_REQUIRED" if r.returncode == 3010 else "",
            " ok" if ok else "",
        )
        if not ok and (r.stdout.strip() or r.stderr.strip()):
            logging.warning(
                "PawnIO setup %s stdout=%s stderr=%s",
                args,
                r.stdout.strip(),
                r.stderr.strip(),
            )
        return ok
    except subprocess.TimeoutExpired:
        import logging

        logging.warning("PawnIO setup %s timed out", args)
        return False
    except Exception as e:
        import logging

        logging.warning("PawnIO setup %s failed: %s", args, e)
        return False


def install_pawnio():
    return _run_setup(["-install", "-silent"])


def uninstall_pawnio():
    return _run_setup(["-uninstall", "-silent"])


def ensure_pawnio():
    if is_pawnio_installed() and _device_working():
        return True
    if not _is_admin():
        return False
    return install_pawnio()
