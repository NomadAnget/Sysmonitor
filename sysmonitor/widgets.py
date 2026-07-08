from collections import deque

from PyQt6.QtCore import Qt, QPointF, QRectF, QSize
from PyQt6.QtGui import (
    QColor,
    QPainter,
    QPen,
    QBrush,
    QPolygonF,
    QLinearGradient,
)
from PyQt6.QtWidgets import QWidget, QLabel, QProgressBar, QHBoxLayout

from .config import ThemeConfig
from .utils import level_color, bar_style


class MeterRow(QWidget):
    def __init__(self, label):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(label)
        self.label.setMinimumWidth(70)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self._level = level_color(0)
        self.bar.setStyleSheet(bar_style(0))
        lay.addWidget(self.label)
        lay.addWidget(self.bar, 1)

    def set_value(self, value, text=None):
        v = 0 if value is None else int(value)
        self.bar.setValue(v)
        lvl = level_color(value)
        if lvl != self._level:
            self._level = lvl
            self.bar.setStyleSheet(bar_style(value))
        self.bar.setFormat(text if text is not None else f"{v}%")

    def restyle(self):
        v = self.bar.value()
        self._level = level_color(v)
        self.bar.setStyleSheet(bar_style(v))


class Sparkline(QWidget):
    def __init__(self, capacity=600, color="#3fb950", dynamic_color=True):
        super().__init__()
        self._data = deque(maxlen=capacity)
        self._color = QColor(color)
        self._dynamic = dynamic_color
        self.setMinimumHeight(64)

    def sizeHint(self):
        return QSize(200, 70)

    def push(self, value):
        v = 0.0 if value is None else max(0.0, min(100.0, float(value)))
        self._data.append(v)
        if self._dynamic:
            self._color = QColor(level_color(v))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), self.palette().window().color())
        p.setPen(QPen(QColor(ThemeConfig.c_border), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        p.setPen(QPen(QColor(ThemeConfig.c_border), 1))
        for frac in (0.25, 0.5, 0.75):
            y = int(h * frac)
            p.drawLine(1, y, w - 1, y)

        n = len(self._data)
        cap = self._data.maxlen
        if n >= 2:
            step = w / (cap - 1)
            pts = []
            for i, v in enumerate(self._data):
                x = w - (n - 1 - i) * step
                y = h - (v / 100.0) * (h - 2) - 1
                pts.append(QPointF(x, y))

            fill = QPolygonF(pts + [QPointF(pts[-1].x(), h), QPointF(pts[0].x(), h)])
            grad = QLinearGradient(0, 0, 0, h)
            top = QColor(self._color)
            top.setAlpha(90)
            bot = QColor(self._color)
            bot.setAlpha(12)
            grad.setColorAt(0, top)
            grad.setColorAt(1, bot)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawPolygon(fill)

            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self._color, 2))
            p.drawPolyline(QPolygonF(pts))

        if n >= 1:
            p.setPen(QColor(ThemeConfig.c_text))
            p.drawText(
                QRectF(0, 2, w - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                f"{self._data[-1]:.0f}%",
            )
