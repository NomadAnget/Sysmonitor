from PyQt6.QtGui import QColor


class ThemeConfig:
    c_bg = "#0d1117"
    c_text = "#e6edf3"
    c_sub_text = "#8b949e"
    c_border = "#30363d"
    c_accent = "#1f6feb"
    c_combo_bg = "#161b22"
    c_track = "#0d1117"
    c_card = "transparent"
    is_light = False


THEME_ORDER = ("system", "dark", "light")
THEME_LABELS = {"system": "随系统", "dark": "暗色", "light": "浅色"}


def resolve_colors(mode):
    cls = ThemeConfig
    if mode == "dark":
        (
            cls.c_bg,
            cls.c_text,
            cls.c_sub_text,
            cls.c_border,
            cls.c_accent,
            cls.c_combo_bg,
        ) = ("#0d1117", "#e6edf3", "#8b949e", "#30363d", "#1f6feb", "#161b22")
        cls.is_light = False
    elif mode == "light":
        (
            cls.c_bg,
            cls.c_text,
            cls.c_sub_text,
            cls.c_border,
            cls.c_accent,
            cls.c_combo_bg,
        ) = ("#f3f3f3", "#1f2328", "#6e7781", "#d0d7de", "#0969da", "#ffffff")
        cls.is_light = True
    else:
        from PyQt6.QtWidgets import QApplication

        pal = QApplication.palette()
        cls.c_bg = pal.window().color().name()
        cls.c_text = pal.windowText().color().name()
        cls.c_sub_text = pal.mid().color().name()
        cls.c_border = pal.mid().color().name()
        cls.c_accent = pal.highlight().color().name()
        cls.c_combo_bg = pal.base().color().name()
        _b = pal.window().color()
        cls.is_light = (0.299 * _b.red() + 0.587 * _b.green() + 0.114 * _b.blue()) > 140
    cls.c_track = _shade(cls.c_bg, cls.is_light)
    cls.c_card = "transparent"


def _shade(hex_color, lighter, amount=5):
    c = QColor(hex_color)
    d = amount if lighter else -amount
    vals = [max(0, min(255, v - d)) for v in (c.red(), c.green(), c.blue())]
    return QColor(vals[0], vals[1], vals[2]).name()
