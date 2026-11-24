"""Темы оформления: палитры + генератор QSS с настраиваемыми акцентом, скруглением и шрифтом."""
from __future__ import annotations

from PyQt6.QtGui import QColor

THEMES: dict[str, dict] = {
    "Тёмная": dict(bg="#0f1115", sidebar="#13161c", surface="#181c24", surface2="#1f242e",
                   border="#2a303c", text="#e7eaf0", muted="#8a93a6"),
    "Полночь": dict(bg="#0a0e1c", sidebar="#0d1226", surface="#121a34", surface2="#18224a",
                    border="#243059", text="#e5ebff", muted="#8191c4"),
    "AMOLED": dict(bg="#000000", sidebar="#050505", surface="#0c0c0e", surface2="#141417",
                   border="#232328", text="#f2f2f5", muted="#7d7d88"),
    "Кибер": dict(bg="#0b0613", sidebar="#100819", surface="#170d26", surface2="#201236",
                  border="#37205c", text="#f1e8ff", muted="#9a82c4"),
    "Лес": dict(bg="#0c130f", sidebar="#0f1813", surface="#142019", surface2="#1b2b22",
                border="#27402f", text="#e6f3ea", muted="#82a290"),
    "Светлая": dict(bg="#f3f5f9", sidebar="#ffffff", surface="#ffffff", surface2="#eef1f7",
                    border="#d9dee9", text="#1a1f2b", muted="#6b7487"),
}

ACCENT_PRESETS = [
    "#4f8cff", "#7c5cff", "#22c3a6", "#2ecc71", "#f5a524", "#ff5d73", "#ff7a3d", "#e056fd",
]

QSS = """
* { font-family: "Segoe UI", "Inter", "SF Pro Text", "Roboto", "Arial"; font-size: @fs@pt; outline: 0; }
QWidget { background: transparent; color: @text@; }
QMainWindow, QDialog, QWidget#root { background: @bg@; }
QToolTip { background: @surface2@; color: @text@; border: 1px solid @border@; padding: 5px 8px; border-radius: 6px; }

QFrame#sidebar { background: @sidebar@; border-right: 1px solid @border@; }
QFrame#card { background: @surface@; border: 1px solid @border@; border-radius: @r@px; }
QLabel#appTitle { font-size: @fs2@pt; font-weight: 700; }
QLabel#pageTitle { font-size: @fs3@pt; font-weight: 700; }
QLabel#muted { color: @muted@; }
QLabel#statValue { font-size: @fs3@pt; font-weight: 700; }
QLabel#sectionTitle { font-weight: 700; font-size: @fs1@pt; }
QLabel#pill { border-radius: 11px; padding: 3px 12px; background: @surface2@; border: 1px solid @border@; }

QPushButton#nav { text-align: left; padding: 11px 16px; border: none; border-radius: @r@px; color: @muted@; font-weight: 600; background: transparent; }
QPushButton#nav:hover { background: @surface2@; color: @text@; }
QPushButton#nav:checked { background: @accent@; color: @onaccent@; }

QPushButton { background: @surface2@; border: 1px solid @border@; border-radius: @r@px; padding: 8px 16px; font-weight: 600; }
QPushButton:hover { border-color: @accent@; }
QPushButton:pressed { background: @border@; }
QPushButton:disabled { color: @muted@; background: @surface@; }
QPushButton[kind="primary"] { background: @accent@; color: @onaccent@; border: 1px solid @accent@; }
QPushButton[kind="primary"]:hover { background: @accent_h@; }
QPushButton[kind="danger"] { background: @danger@; color: white; border: 1px solid @danger@; }
QPushButton[kind="danger"]:hover { background: @danger_h@; }
QPushButton[kind="big"] { padding: 14px 30px; font-size: @fs2@pt; }
QPushButton[kind="ghost"] { background: transparent; }

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: @surface2@; border: 1px solid @border@; border-radius: @r@px; padding: 7px 10px;
    selection-background-color: @accent@; selection-color: @onaccent@; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: @accent@; }
QTextEdit#log { background: @surface@; border: none; padding: 8px; font-family: "Cascadia Mono", "Consolas", "Menlo", monospace; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: @surface2@; border: 1px solid @border@; selection-background-color: @accent@; selection-color: @onaccent@; padding: 4px; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 18px; border: none; }

QCheckBox { spacing: 10px; background: transparent; }
QCheckBox::indicator, QListView::indicator, QTableView::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid @border@; background: @surface2@; }
QCheckBox::indicator:checked, QListView::indicator:checked, QTableView::indicator:checked { background: @accent@; border-color: @accent@; }
QCheckBox::indicator:hover { border-color: @accent@; }

QListWidget, QTableWidget { background: @surface@; border: 1px solid @border@; border-radius: @r@px; gridline-color: @border@; alternate-background-color: @surface@; }
QListWidget::item { padding: 8px 10px; border-radius: 6px; margin: 1px 4px; }
QListWidget::item:hover { background: @surface2@; }
QListWidget::item:selected { background: @surface2@; color: @text@; }
QTableWidget::item { padding: 6px 8px; border-bottom: 1px solid @border@; }
QTableWidget::item:selected { background: @surface2@; color: @text@; }
QHeaderView::section { background: @surface2@; color: @muted@; border: none; border-bottom: 1px solid @border@; padding: 9px 8px; font-weight: 600; }
QTableCornerButton::section { background: @surface2@; border: none; }

QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @border@; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: @muted@; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: @border@; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSlider::groove:horizontal { height: 6px; background: @surface2@; border-radius: 3px; border: 1px solid @border@; }
QSlider::sub-page:horizontal { background: @accent@; border-radius: 3px; }
QSlider::handle:horizontal { background: @accent@; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }

QMessageBox { background: @bg@; }
"""


def _onaccent(accent: QColor) -> str:
    lum = 0.299 * accent.red() + 0.587 * accent.green() + 0.114 * accent.blue()
    return "#101216" if lum > 160 else "#ffffff"


def build_qss(theme: str, accent: str, radius: int, font_size: int) -> str:
    pal = dict(THEMES.get(theme, THEMES["Тёмная"]))
    ac = QColor(accent)
    if not ac.isValid():
        ac = QColor("#4f8cff")
    pal.update(
        accent=ac.name(),
        accent_h=ac.lighter(115).name(),
        onaccent=_onaccent(ac),
        danger="#e5484d",
        danger_h="#f0595e",
        r=int(radius),
        fs=int(font_size),
        fs1=int(font_size) + 1,
        fs2=int(font_size) + 3,
        fs3=int(font_size) + 8,
    )
    qss = QSS
    for k, v in pal.items():
        qss = qss.replace(f"@{k}@", str(v))
    return qss


def palette_of(theme: str) -> dict:
    return THEMES.get(theme, THEMES["Тёмная"])
