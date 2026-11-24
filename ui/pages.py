"""Страницы приложения: Панель, Аккаунты, Чаты, История, Настройки."""
from __future__ import annotations

import csv
import html
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.engine import Engine
from core.storage import MAX_ACCOUNTS, Store

from .dialogs import AddAccountDialog
from .theme import ACCENT_PRESETS, THEMES
from .widgets import STATUS_INFO, StatCard, card, hbox, make_button, page_header, set_kind

LEVEL_COLORS = {"info": "#9fb3d9", "ok": "#2ecc71", "warn": "#f5a524", "error": "#ff5d73"}

RESULT_TEXT = {
    "success": ("✅ Успешно", "#2ecc71"),
    "fail": ("⚠ Отказ", "#f5a524"),
    "error": ("❌ Ошибка", "#ff5d73"),
    "timeout": ("⏱ Таймаут", "#ff5d73"),
    "skipped": ("⏭ Пропущен", "#8a93a6"),
}


def _page_layout(widget: QWidget) -> QVBoxLayout:
    lay = QVBoxLayout(widget)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(16)
    return lay


# ====================================================================== Панель
class DashboardPage(QWidget):
    toggle_requested = pyqtSignal()

    def __init__(self, store: Store, engine: Engine) -> None:
        super().__init__()
        self.store = store
        self.engine = engine
        lay = _page_layout(self)
        lay.addWidget(page_header("Панель управления", "Запустите мониторинг — бот будет ловить и активировать чеки на всех аккаунтах."))

        top = card()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(20, 16, 20, 16)
        row = hbox()
        self.state_label = QLabel("Мониторинг остановлен")
        self.state_label.setObjectName("sectionTitle")
        self.state_sub = QLabel("")
        self.state_sub.setObjectName("muted")
        col = QVBoxLayout()
        col.addWidget(self.state_label)
        col.addWidget(self.state_sub)
        row.addLayout(col)
        row.addStretch(1)
        self.toggle_btn = make_button("▶  Запустить", "primary", self.toggle_requested.emit)
        self.toggle_btn.setProperty("kind", "primary")
        self.toggle_btn.setMinimumHeight(48)
        row.addWidget(self.toggle_btn)
        tl.addLayout(row)
        lay.addWidget(top)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.c_found = StatCard("Найдено чеков")
        self.c_ok = StatCard("Активировано")
        self.c_fail = StatCard("Неудачи")
        self.c_sum = StatCard("Получено всего", "—")
        self.c_acc = StatCard("Аккаунтов онлайн", "0/0")
        self.c_speed = StatCard("Средняя скорость", "—")
        for i, c in enumerate((self.c_found, self.c_ok, self.c_fail, self.c_sum, self.c_acc, self.c_speed)):
            grid.addWidget(c, i // 3, i % 3)
        lay.addLayout(grid)

        log_card = card()
        ll = QVBoxLayout(log_card)
        ll.setContentsMargins(16, 12, 16, 12)
        head = hbox()
        t = QLabel("Журнал событий")
        t.setObjectName("sectionTitle")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(make_button("Очистить", "ghost", lambda: self.log_view.clear()))
        ll.addLayout(head)
        self.log_view = QTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(3000)
        ll.addWidget(self.log_view)
        lay.addWidget(log_card, 1)
        self.refresh_stats()

    def append_log(self, level: str, account: str, text: str) -> None:
        color = LEVEL_COLORS.get(level, "#9fb3d9")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(
            f'<span style="color:#7d8699">[{ts}]</span> '
            f'<b style="color:#c9d3ea">{html.escape(account)}</b> '
            f'<span style="color:{color}">{html.escape(text)}</span>'
        )
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_running(self, running: bool) -> None:
        if running:
            self.toggle_btn.setText("■  Остановить")
            set_kind(self.toggle_btn, "danger")
            self.state_label.setText("🟢 Мониторинг активен")
            self.state_sub.setText("Все включённые аккаунты слушают выбранные чаты.")
        else:
            self.toggle_btn.setText("▶  Запустить")
            set_kind(self.toggle_btn, "primary")
            self.state_label.setText("⚪ Мониторинг остановлен")
            self.state_sub.setText("Нажмите «Запустить», чтобы начать ловить чеки.")

    def refresh_stats(self) -> None:
        hist = self.store.history
        real = [h for h in hist if h.get("status") != "skipped"]
        ok = [h for h in real if h["status"] == "success"]
        self.c_found.set(len(real))
        self.c_ok.set(len(ok))
        self.c_fail.set(len(real) - len(ok))
        sums: dict[str, float] = {}
        for h in ok:
            if h.get("value") and h.get("ticker"):
                sums[h["ticker"]] = sums.get(h["ticker"], 0) + h["value"]
        self.c_sum.set(" · ".join(f"{v:g} {k}" for k, v in list(sums.items())[:3]) or "—")
        if ok:
            self.c_speed.set(f"{sum(h.get('ms', 0) for h in ok) // len(ok)} мс")
        else:
            self.c_speed.set("—")

    def set_online(self, online: int, total: int) -> None:
        self.c_acc.set(f"{online}/{total}")


# ==================================================================== Аккаунты
class AccountsPage(QWidget):
    open_chats = pyqtSignal(str)
    accounts_changed = pyqtSignal()

    def __init__(self, store: Store, engine: Engine) -> None:
        super().__init__()
        self.store = store
        self.engine = engine
        self.statuses: dict[str, str] = {}
        self._loading = False

        lay = _page_layout(self)
        lay.addWidget(page_header("Аккаунты", f"До {MAX_ACCOUNTS} аккаунтов. Все активные аккаунты работают одновременно."))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Аккаунт", "Телефон", "Статус", "Чатов", "Активен"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3, 4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._item_changed)
        self.table.doubleClicked.connect(lambda *_: self._chats())
        lay.addWidget(self.table, 1)

        lay.addLayout(
            hbox(
                make_button("＋ Добавить аккаунт", "primary", self._add),
                make_button("💬 Выбрать чаты", "", self._chats),
                make_button("⟳ Переподключить", "", self._reconnect),
                None,
                make_button("🗑 Удалить", "danger", self._remove),
            )
        )
        self.reload()

    def _selected(self) -> dict | None:
        r = self.table.currentRow()
        if r < 0 or r >= len(self.store.accounts):
            QMessageBox.information(self, "Аккаунты", "Сначала выберите аккаунт в списке.")
            return None
        return self.store.accounts[r]

    def reload(self) -> None:
        self._loading = True
        self.table.setRowCount(len(self.store.accounts))
        for r, a in enumerate(self.store.accounts):
            self.table.setItem(r, 0, QTableWidgetItem(a.get("title", "")))
            self.table.setItem(r, 1, QTableWidgetItem(a.get("phone", "")))
            self.table.setItem(r, 2, QTableWidgetItem(""))
            self._paint_status(r, self.statuses.get(a["id"], "stopped"))
            chats = "все" if (a.get("monitor_all") or self.store.settings.get("monitor_all")) else str(len(a.get("chats") or {}))
            self.table.setItem(r, 3, QTableWidgetItem(chats))
            it = QTableWidgetItem("")
            it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            it.setCheckState(Qt.CheckState.Checked if a.get("enabled", True) else Qt.CheckState.Unchecked)
            self.table.setItem(r, 4, it)
        self._loading = False

    def _paint_status(self, row: int, status: str) -> None:
        text, color = STATUS_INFO.get(status, STATUS_INFO["stopped"])
        it = self.table.item(row, 2)
        if it:
            it.setText(text)
            it.setForeground(QBrush(QColor(color)))

    def set_status(self, acc_id: str, status: str) -> None:
        self.statuses[acc_id] = status
        for r, a in enumerate(self.store.accounts):
            if a["id"] == acc_id:
                self._paint_status(r, status)

    def online_count(self) -> int:
        ids = {a["id"] for a in self.store.accounts if a.get("enabled", True)}
        return sum(1 for k, v in self.statuses.items() if v == "online" and k in ids)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 4:
            return
        acc = self.store.accounts[item.row()]
        acc["enabled"] = item.checkState() == Qt.CheckState.Checked
        self.store.save_accounts()
        if self.engine.running:
            if acc["enabled"]:
                self.engine.run(self.engine.start_account(acc))
            else:
                self.engine.run(self.engine.stop_account(acc["id"]))
                self.set_status(acc["id"], "stopped")
        self.accounts_changed.emit()

    def _add(self) -> None:
        if len(self.store.accounts) >= MAX_ACCOUNTS:
            QMessageBox.warning(self, "Лимит", f"Максимум {MAX_ACCOUNTS} аккаунтов.")
            return
        dlg = AddAccountDialog(self.store, self.engine, self)
        if dlg.exec() and dlg.result_account:
            acc = dlg.result_account
            self.store.accounts.append(acc)
            self.store.save_accounts()
            self.reload()
            self.accounts_changed.emit()
            if self.engine.running:
                self.engine.run(self.engine.start_account(acc))
            QMessageBox.information(
                self, "Готово", f"Аккаунт «{acc['title']}» добавлен.\nТеперь выберите чаты для мониторинга."
            )
            self.open_chats.emit(acc["id"])

    def _chats(self) -> None:
        acc = self._selected()
        if acc:
            self.open_chats.emit(acc["id"])

    def _reconnect(self) -> None:
        acc = self._selected()
        if acc:
            self.engine.run(self.engine.start_account(acc))

    def _remove(self) -> None:
        acc = self._selected()
        if not acc:
            return
        if QMessageBox.question(self, "Удалить аккаунт", f"Удалить «{acc['title']}» и его сессию?") != QMessageBox.StandardButton.Yes:
            return
        self.engine.run(self.engine.stop_account(acc["id"]))
        self.engine.delete_session(acc)
        self.store.accounts.remove(acc)
        self.store.save_accounts()
        self.reload()
        self.accounts_changed.emit()


# ======================================================================== Чаты
class ChatsPage(QWidget):
    changed = pyqtSignal()

    ICONS = {"channel": "📢", "group": "👥", "user": "👤", "bot": "🤖", "?": "💬"}

    def __init__(self, store: Store, engine: Engine) -> None:
        super().__init__()
        self.store = store
        self.engine = engine
        self.cache: dict[str, list[dict]] = {}
        self._loading = False

        lay = _page_layout(self)
        lay.addWidget(page_header("Чаты и каналы", "Отметьте, какие чаты мониторить на каждом аккаунте."))

        top = card()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(16, 14, 16, 14)
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_account)
        self.load_btn = make_button("⟳ Загрузить чаты", "primary", self._load)
        tl.addLayout(hbox(QLabel("Аккаунт:"), self.combo, self.load_btn))
        self.cb_acc_all = QCheckBox("Мониторить ВСЕ чаты этого аккаунта")
        self.cb_acc_all.toggled.connect(self._acc_all)
        self.cb_global_all = QCheckBox("Мониторить ВСЕ чаты на ВСЕХ аккаунтах")
        self.cb_global_all.toggled.connect(self._global_all)
        tl.addWidget(self.cb_acc_all)
        tl.addWidget(self.cb_global_all)
        lay.addWidget(top)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Поиск по названию…")
        self.search.textChanged.connect(self._filter)
        self.kind = QComboBox()
        for label, key in (("Все типы", ""), ("Каналы", "channel"), ("Группы", "group"), ("Личные", "user"), ("Боты", "bot")):
            self.kind.addItem(label, key)
        self.kind.currentIndexChanged.connect(self._filter)
        lay.addLayout(hbox(self.search, self.kind))

        self.list = QListWidget()
        self.list.itemChanged.connect(self._item_changed)
        lay.addWidget(self.list, 1)

        self.counter = QLabel("")
        self.counter.setObjectName("muted")
        lay.addLayout(
            hbox(
                make_button("Выбрать все видимые", "", lambda: self._bulk(True)),
                make_button("Снять все видимые", "", lambda: self._bulk(False)),
                None,
                self.counter,
            )
        )
        self.reload_accounts()

    # ---- аккаунты ----
    def reload_accounts(self, select: str | None = None) -> None:
        cur = select or self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        for a in self.store.accounts:
            self.combo.addItem(f"{a.get('title')}  ·  {a.get('phone')}", a["id"])
        idx = self.combo.findData(cur) if cur else 0
        self.combo.setCurrentIndex(max(0, idx) if self.store.accounts else -1)
        self.combo.blockSignals(False)
        self._on_account()

    def select_account(self, acc_id: str) -> None:
        self.reload_accounts(acc_id)

    def _acc(self) -> dict | None:
        return self.store.account(self.combo.currentData()) if self.combo.currentData() else None

    def _on_account(self) -> None:
        acc = self._acc()
        self.load_btn.setEnabled(acc is not None)
        self._loading = True
        self.cb_acc_all.setChecked(bool(acc and acc.get("monitor_all")))
        self.cb_global_all.setChecked(bool(self.store.settings.get("monitor_all")))
        self._loading = False
        self._populate()

    def _acc_all(self, on: bool) -> None:
        acc = self._acc()
        if acc and not self._loading:
            acc["monitor_all"] = on
            self.store.save_accounts()
            self.changed.emit()

    def _global_all(self, on: bool) -> None:
        if not self._loading:
            self.store.settings["monitor_all"] = on
            self.store.save_settings()
            self.changed.emit()

    # ---- список ----
    def _populate(self) -> None:
        self._loading = True
        self.list.clear()
        acc = self._acc()
        if acc:
            saved = dict(acc.get("chats") or {})
            entries = list(self.cache.get(acc["id"], []))
            known = {str(e["id"]) for e in entries}
            for cid, title in saved.items():
                if cid not in known:
                    entries.append({"id": int(cid), "title": title, "kind": "?"})
            entries.sort(key=lambda e: (str(e["id"]) not in saved, e["title"].lower()))
            for e in entries:
                it = QListWidgetItem(f"{self.ICONS.get(e['kind'], '💬')}  {e['title']}")
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if str(e["id"]) in saved else Qt.CheckState.Unchecked)
                it.setData(Qt.ItemDataRole.UserRole, str(e["id"]))
                it.setData(Qt.ItemDataRole.UserRole + 1, e["kind"])
                it.setData(Qt.ItemDataRole.UserRole + 2, e["title"])
                self.list.addItem(it)
        self._loading = False
        self._filter()
        self._update_counter()

    def _load(self) -> None:
        acc = self._acc()
        if not acc:
            return
        self.load_btn.setEnabled(False)
        self.load_btn.setText("Загружаю…")

        def ok(res):
            self.cache[acc["id"]] = res
            self.load_btn.setEnabled(True)
            self.load_btn.setText("⟳ Загрузить чаты")
            self._populate()

        def err(e):
            self.load_btn.setEnabled(True)
            self.load_btn.setText("⟳ Загрузить чаты")
            QMessageBox.warning(self, "Ошибка", f"Не удалось загрузить чаты:\n{e}")

        self.engine.run(self.engine.fetch_dialogs(acc["id"]), ok, err)

    def _filter(self) -> None:
        q = self.search.text().strip().lower()
        kind = self.kind.currentData()
        for i in range(self.list.count()):
            it = self.list.item(i)
            title = (it.data(Qt.ItemDataRole.UserRole + 2) or "").lower()
            k = it.data(Qt.ItemDataRole.UserRole + 1)
            it.setHidden(bool((q and q not in title) or (kind and k != kind)))

    def _save_from_list(self) -> None:
        acc = self._acc()
        if not acc:
            return
        chats = {}
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                chats[it.data(Qt.ItemDataRole.UserRole)] = it.data(Qt.ItemDataRole.UserRole + 2)
        acc["chats"] = chats
        self.store.save_accounts()
        self._update_counter()
        self.changed.emit()

    def _item_changed(self, _item) -> None:
        if not self._loading:
            self._save_from_list()

    def _bulk(self, on: bool) -> None:
        self._loading = True
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._loading = False
        self._save_from_list()

    def _update_counter(self) -> None:
        acc = self._acc()
        n = len((acc or {}).get("chats") or {})
        self.counter.setText(f"Выбрано: {n} · Всего в списке: {self.list.count()}")


# ===================================================================== История
class HistoryPage(QWidget):
    history_cleared = pyqtSignal()
    COLS = ["Время", "Аккаунт", "Чат", "Сервис", "Сумма", "Статус", "мс", "Ответ бота"]

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        lay = _page_layout(self)
        lay.addWidget(page_header("История активаций", "Все найденные чеки и результат по каждому."))
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(len(self.COLS) - 1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)
        lay.addLayout(
            hbox(
                make_button("Экспорт в CSV", "", self._export),
                None,
                make_button("Очистить историю", "danger", self._clear),
            )
        )
        for rec in reversed(self.store.history[-500:]):
            self._insert(rec, at_top=False)

    def _insert(self, rec: dict, at_top: bool = True) -> None:
        row = 0 if at_top else self.table.rowCount()
        self.table.insertRow(row)
        text, color = RESULT_TEXT.get(rec.get("status", ""), (rec.get("status", ""), "#8a93a6"))
        vals = [
            rec.get("time", ""), rec.get("account", ""), rec.get("chat", ""), rec.get("service", ""),
            rec.get("amount", ""), text, str(rec.get("ms", "")), rec.get("detail", ""),
        ]
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            if c == 5:
                it.setForeground(QBrush(QColor(color)))
            self.table.setItem(row, c, it)

    def add(self, rec: dict) -> None:
        self._insert(rec)

    def _clear(self) -> None:
        if QMessageBox.question(self, "История", "Очистить всю историю?") != QMessageBox.StandardButton.Yes:
            return
        self.store.history.clear()
        self.store.save_history()
        self.table.setRowCount(0)
        self.history_cleared.emit()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт", "checks_history.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["time", "account", "chat", "service", "amount", "status", "ms", "detail"])
            for r in self.store.history:
                w.writerow([r.get("time"), r.get("account"), r.get("chat"), r.get("service"),
                            r.get("amount"), r.get("status"), r.get("ms"), r.get("detail")])


# =================================================================== Настройки
class SettingsPage(QWidget):
    appearance_changed = pyqtSignal()
    behavior_changed = pyqtSignal()

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.s = store.settings

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.addWidget(page_header("Настройки", "Внешний вид и поведение бота. Изменения применяются сразу."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 8, 8, 8)
        lay.setSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---------- внешний вид ----------
        box, g = self._section("🎨 Внешний вид")
        self.theme = QComboBox()
        self.theme.addItems(list(THEMES.keys()))
        self.theme.setCurrentText(self.s["theme"])
        self.theme.currentTextChanged.connect(lambda v: self._set("theme", v, True))
        g.addWidget(QLabel("Тема"), 0, 0)
        g.addWidget(self.theme, 0, 1)

        g.addWidget(QLabel("Акцентный цвет"), 1, 0)
        accent_row = hbox(spacing=6)
        for c in ACCENT_PRESETS:
            b = QPushButton()
            b.setFixedSize(28, 28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton{{background:{c};border:2px solid transparent;border-radius:14px;padding:0}}QPushButton:hover{{border-color:white}}")
            b.clicked.connect(lambda _=False, col=c: self._set("accent", col, True))
            accent_row.addWidget(b)
        accent_row.addWidget(make_button("Свой цвет…", "", self._pick_color))
        accent_row.addStretch(1)
        g.addLayout(accent_row, 1, 1)

        self.radius = QSlider(Qt.Orientation.Horizontal)
        self.radius.setRange(0, 24)
        self.radius.setValue(int(self.s["radius"]))
        self.radius.valueChanged.connect(lambda v: self._set("radius", v, True))
        g.addWidget(QLabel("Скругление углов"), 2, 0)
        g.addWidget(self.radius, 2, 1)

        self.font = QSpinBox()
        self.font.setRange(8, 20)
        self.font.setValue(int(self.s["font_size"]))
        self.font.valueChanged.connect(lambda v: self._set("font_size", v, True))
        g.addWidget(QLabel("Размер шрифта"), 3, 0)
        g.addWidget(self.font, 3, 1)

        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(60, 100)
        self.opacity.setValue(int(self.s["opacity"]))
        self.opacity.valueChanged.connect(lambda v: self._set("opacity", v, True))
        g.addWidget(QLabel("Прозрачность окна"), 4, 0)
        g.addWidget(self.opacity, 4, 1)
        g.setColumnStretch(1, 1)
        lay.addWidget(box)

        # ---------- поведение ----------
        box, g = self._section("⚙ Поведение бота")
        r = 0
        for key, text in (("cryptobot", "Ловить чеки CryptoBot (@send / @CryptoBot)"),
                          ("xrocket", "Ловить чеки xRocket (@xrocket)"),
                          ("wallet", "Ловить чеки @wallet")):
            cb = QCheckBox(text)
            cb.setChecked(bool(self.s["services"].get(key, True)))
            cb.toggled.connect(lambda v, k=key: self._set_service(k, v))
            g.addWidget(cb, r, 0, 1, 2)
            r += 1
        r = self._check(g, r, "claim_all_accounts", "Каждый аккаунт активирует чек сам (для мульти-чеков)")
        r = self._check(g, r, "auto_subscribe", "Автоматически подписываться на каналы, если чек требует подписку")
        r = self._check(g, r, "sound", "Звуковое уведомление при успешной активации")
        r = self._check(g, r, "autostart", "Запускать мониторинг автоматически при старте программы")
        r = self._check(g, r, "notify_enabled", "Присылать в Telegram сообщение об успешной активации чека")
        r = self._check(g, r, "notify_startup", "При запуске присылать в Telegram список активных аккаунтов")

        self.min_amount = QDoubleSpinBox()
        self.min_amount.setRange(0, 1_000_000)
        self.min_amount.setDecimals(4)
        self.min_amount.setValue(float(self.s["min_amount"]))
        self.min_amount.setToolTip("0 — активировать любые чеки")
        self.min_amount.valueChanged.connect(lambda v: self._set("min_amount", v))
        g.addWidget(QLabel("Минимальная сумма чека (0 = все)"), r, 0)
        g.addWidget(self.min_amount, r, 1)
        r += 1
        r = self._spin(g, r, "click_delay_ms", "Задержка перед активацией, мс", 0, 10000)
        r = self._spin(g, r, "retries", "Количество попыток", 1, 10)
        r = self._spin(g, r, "reply_timeout", "Ожидание ответа бота, сек", 3, 60)
        g.setColumnStretch(1, 1)
        lay.addWidget(box)

        # ---------- сеть ----------
        box, g = self._section("🌐 Сеть")
        self.proxy = QLineEdit(self.s.get("proxy", ""))
        self.proxy.setPlaceholderText("socks5://user:pass@host:port  (пусто = без прокси)")
        self.proxy.editingFinished.connect(lambda: self._set("proxy", self.proxy.text().strip()))
        g.addWidget(QLabel("Прокси"), 0, 0)
        g.addWidget(self.proxy, 0, 1)
        note = QLabel("Прокси применяется при следующем подключении аккаунтов (перезапустите мониторинг).")
        note.setObjectName("muted")
        note.setWordWrap(True)
        g.addWidget(note, 1, 0, 1, 2)
        g.setColumnStretch(1, 1)
        lay.addWidget(box)
        lay.addStretch(1)

    # ---- помощники ----
    def _section(self, title: str):
        box = card()
        v = QVBoxLayout(box)
        v.setContentsMargins(20, 16, 20, 18)
        v.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("sectionTitle")
        v.addWidget(t)
        g = QGridLayout()
        g.setSpacing(12)
        v.addLayout(g)
        return box, g

    def _set(self, key: str, value, appearance: bool = False) -> None:
        self.s[key] = value
        self.store.save_settings()
        (self.appearance_changed if appearance else self.behavior_changed).emit()

    def _set_service(self, key: str, value: bool) -> None:
        self.s["services"][key] = value
        self.store.save_settings()

    def _check(self, g: QGridLayout, row: int, key: str, text: str) -> int:
        cb = QCheckBox(text)
        cb.setChecked(bool(self.s[key]))
        cb.toggled.connect(lambda v: self._set(key, v))
        g.addWidget(cb, row, 0, 1, 2)
        return row + 1

    def _spin(self, g: QGridLayout, row: int, key: str, text: str, lo: int, hi: int) -> int:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(int(self.s[key]))
        sp.valueChanged.connect(lambda v: self._set(key, v))
        g.addWidget(QLabel(text), row, 0)
        g.addWidget(sp, row, 1)
        return row + 1

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.s["accent"]), self, "Акцентный цвет")
        if c.isValid():
            self._set("accent", c.name(), True)
