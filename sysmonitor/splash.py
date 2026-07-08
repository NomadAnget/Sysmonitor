from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar, QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

from .utils import res_path
from .config import resolve_colors, ThemeConfig


class SplashWindow(QWidget):
    def __init__(self):
        super().__init__(None)
        resolve_colors("system")
        self._setup_ui()
        self._center()

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(400, 240)

        c = ThemeConfig
        bg = c.c_bg
        fg = c.c_text
        border = c.c_border
        accent = c.c_accent

        self.setStyleSheet(
            f"SplashWindow{{background:{bg};border:1px solid {border};}}"
            f"QLabel{{background:transparent;color:{fg};"
            f"font-family:'Segoe UI','Microsoft YaHei',sans-serif;}}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(0)

        icon_path = res_path("libs", "logo.ico")
        if icon_path:
            pix = QIcon(icon_path).pixmap(40, 40)
            icon_lbl = QLabel()
            icon_lbl.setPixmap(pix)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(icon_lbl)

        layout.addSpacing(6)

        title = QLabel("系统监控")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = title.font()
        f.setPointSize(14)
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        layout.addStretch(1)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar{{background:{bg};border:1px solid {border};"
            f"border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{accent};border-radius:2px;}}"
        )
        layout.addWidget(self._progress)

        layout.addSpacing(10)

        self._status = QLabel("正在准备…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fs = self._status.font()
        fs.setPointSize(10)
        self._status.setFont(fs)
        layout.addWidget(self._status)

        layout.addStretch(1)

    def _center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )

    def set_step(self, index, total, text, fail=False):
        prefix = "✗ " if fail else "✓ "
        self._status.setText(prefix + text)
        self._progress.setValue(int((index + 1) / total * 100))
        QApplication.processEvents()

    def set_status(self, text):
        self._status.setText(text)
        self._progress.setValue(100)
        QApplication.processEvents()
