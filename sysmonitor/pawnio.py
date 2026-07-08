import os
import subprocess

from .utils import res_path


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


def install_pawnio():
    setup = res_path("libs", "PawnIO_setup.exe")
    if not os.path.exists(setup):
        return False
    try:
        r = subprocess.run(
            [setup, "-silent"],
            capture_output=True,
            timeout=120,
        )
        return r.returncode == 0 or r.returncode == 3010
    except Exception:
        return False


def ensure_pawnio():
    if is_pawnio_installed():
        return True
    if not _is_admin():
        return False
    return install_pawnio()
