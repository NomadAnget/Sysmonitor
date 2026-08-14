import time

from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

from ..utils.utils import res_path
from ..utils.config import resolve_colors, ThemeConfig


class SplashWindow(QWidget):
    def __init__(self):
        super().__init__(None)
        resolve_colors("system")
        self._answer = None
        self._setup_ui()
        self._center()

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(400, 300)

        self._icon_path = res_path("libs", "logo.ico")
        if self._icon_path:
            self.setWindowIcon(QIcon(self._icon_path))

        c = ThemeConfig
        bg = c.c_bg
        fg = c.c_text
        border = c.c_border
        accent = c.c_accent

        self.setStyleSheet(
            f"SplashWindow{{background:{bg};border:1px solid {border};}}"
            f"QLabel{{background:transparent;color:{fg};"
            f"font-family:'Segoe UI','Microsoft YaHei',sans-serif;}}"
            f"QPushButton{{background:{c.c_combo_bg};color:{fg};"
            f"border:1px solid {border};border-radius:4px;padding:5px 16px;}}"
            f"QPushButton:hover{{border-color:{accent};}}"
            f"QPushButton#btnPrimary{{background:{accent};border-color:{accent};"
            f"color:white;}}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(0)

        layout.addStretch(1)

        if self._icon_path:
            pix = QIcon(self._icon_path).pixmap(40, 40)
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

        layout.addSpacing(20)

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

        self._status = QLabel("正在启动…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fs = self._status.font()
        fs.setPointSize(10)
        self._status.setFont(fs)
        layout.addWidget(self._status)

        # 交互式确认提示（默认隐藏，prompt() 时显示）
        self._prompt = QLabel("")
        self._prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._prompt.setWordWrap(True)
        self._prompt.hide()
        layout.addWidget(self._prompt)

        self._btn_wrap = QWidget()
        bl = QHBoxLayout(self._btn_wrap)
        bl.setContentsMargins(0, 10, 0, 0)
        bl.setSpacing(8)
        bl.addStretch(1)
        self._btn_secondary = QPushButton("跳过")
        self._btn_primary = QPushButton("启用")
        self._btn_primary.setObjectName("btnPrimary")
        bl.addWidget(self._btn_secondary)
        bl.addWidget(self._btn_primary)
        bl.addStretch(1)
        self._btn_wrap.hide()
        layout.addWidget(self._btn_wrap)

        self._btn_primary.clicked.connect(lambda: self._finish_prompt(True))
        self._btn_secondary.clicked.connect(lambda: self._finish_prompt(False))

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

    def prompt(self, text, primary="确定", secondary=None):
        """在加载界面内显示提示与按钮，阻塞等待用户点击。

        返回 True（主按钮）/ False（次按钮）。
        secondary 为 None 时仅显示主按钮（用于仅确认的场景）。
        """
        self._prompt.setText(text)
        self._btn_primary.setText(primary)
        if secondary:
            self._btn_secondary.setText(secondary)
            self._btn_secondary.show()
        else:
            self._btn_secondary.hide()

        self._status.hide()
        self._prompt.show()
        self._btn_wrap.show()
        QApplication.processEvents()

        self._answer = None
        while self._answer is None:
            QApplication.processEvents()
            time.sleep(0.01)

        self._prompt.hide()
        self._btn_wrap.hide()
        self._status.show()
        return self._answer

    def _finish_prompt(self, answer):
        self._answer = answer
