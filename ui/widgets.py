"""Небольшие переиспользуемые виджеты."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

STATUS_INFO = {
    "online": ("● Онлайн", "#2ecc71"),
    "connecting": ("● Подключение…", "#f5a524"),
    "stopped": ("● Остановлен", "#8a93a6"),
    "unauthorized": ("● Нужен вход", "#e5484d"),
    "error": ("● Ошибка", "#e5484d"),
}


def make_button(text: str, kind: str = "", slot=None, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if slot:
        b.clicked.connect(slot)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def set_kind(button: QPushButton, kind: str) -> None:
    button.setProperty("kind", kind)
    button.style().unpolish(button)
    button.style().polish(button)


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def page_header(title: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    lay.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("muted")
        s.setWordWrap(True)
        lay.addWidget(s)
    return w


class StatCard(QFrame):
    def __init__(self, title: str, value: str = "0") -> None:
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("muted")
        self._value = QLabel(value)
        self._value.setObjectName("statValue")
        lay.addWidget(self._title)
        lay.addWidget(self._value)

    def set(self, value) -> None:
        self._value.setText(str(value))


def hbox(*widgets, spacing: int = 8, stretch_end: bool = False) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        else:
            lay.addWidget(w)
    if stretch_end:
        lay.addStretch(1)
    return lay
