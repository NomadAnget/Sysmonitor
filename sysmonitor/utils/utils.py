import os
import sys
import platform

from .config import ThemeConfig
from PyQt6.QtGui import QColor


def res_path(*parts):
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.abspath(__file__))
        while True:
            if os.path.exists(os.path.join(base, "libs")):
                break
            parent = os.path.dirname(base)
            if parent == base:
                base = None
                break
            base = parent
    return os.path.join(base, *parts) if base else os.path.join(*parts)


def fmt_bytes(n):
    if n is None:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def cpu_name():
    if platform.system() == "Windows":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if val:
                return val.strip()
        except Exception:
            pass

    name = platform.processor() or ""
    if not name and platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        name = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
    return name or "Unknown CPU"


def level_color(value):
    if value is None:
        return "#888888"

    if ThemeConfig.is_light:
        color = {1: "#aee2c3", 2: "#fde047", 3: "#fca5a5"}
    else:
        color = {1: "#1f9d57", 2: "#f59e0b", 3: "#e5484d"}

    if value < 60:
        return color[1]
    elif value < 85:
        return color[2]
    else:
        return color[3]


def bar_style(value):
    colors = level_color(value)
    return (
        f"QProgressBar{{border:1px solid {ThemeConfig.c_border};border-radius:4px;"
        f"background:{ThemeConfig.c_track};text-align:center;"
        f"color:{ThemeConfig.c_text};height:18px;}}"
        f"QProgressBar::chunk{{background:{colors};border-radius:3px;}}"
    )
