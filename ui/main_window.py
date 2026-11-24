"""Главное окно: боковое меню + страницы."""
from __future__ import annotations

import asyncio

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.engine import Engine
from core.storage import Store

from .pages import AccountsPage, ChatsPage, DashboardPage, HistoryPage, SettingsPage
from .theme import build_qss


class MainWindow(QMainWindow):
    def __init__(self, store: Store, engine: Engine) -> None:
        super().__init__()
        self.store = store
        self.engine = engine
        self.setWindowTitle("Check Hunter — автоактивация чеков xRocket / CryptoBot")
        self.resize(1180, 760)
        self.setMinimumSize(980, 640)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # ----- боковая панель -----
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 22, 14, 18)
        sl.setSpacing(6)
        title = QLabel("🚀 Check Hunter")
        title.setObjectName("appTitle")
        sub = QLabel("Автоактивация чеков")
        sub.setObjectName("muted")
        sl.addWidget(title)
        sl.addWidget(sub)
        sl.addSpacing(18)

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(store, engine)
        self.accounts = AccountsPage(store, engine)
        self.chats = ChatsPage(store, engine)
        self.history = HistoryPage(store)
        self.settings = SettingsPage(store)

        self.nav_buttons: list[QPushButton] = []
        for i, (label, page) in enumerate(
            (
                ("📊  Панель", self.dashboard),
                ("👤  Аккаунты", self.accounts),
                ("💬  Чаты", self.chats),
                ("🧾  История", self.history),
                ("⚙  Настройки", self.settings),
            )
        ):
            self.stack.addWidget(page)
            b = QPushButton(label)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self.goto(idx))
            sl.addWidget(b)
            self.nav_buttons.append(b)
        sl.addStretch(1)
        self.footer = QLabel("")
        self.footer.setObjectName("pill")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(self.footer)

        h.addWidget(side)
        h.addWidget(self.stack, 1)

        # ----- связи -----
        engine.log.connect(self.dashboard.append_log)
        engine.account_status.connect(self.on_status)
        engine.claim_result.connect(self.on_claim)
        engine.running_changed.connect(self.on_running)
        self.dashboard.toggle_requested.connect(self.toggle)
        self.accounts.open_chats.connect(self.open_chats)
        self.accounts.accounts_changed.connect(self.on_accounts_changed)
        self.chats.changed.connect(self.accounts.reload)
        self.history.history_cleared.connect(self.dashboard.refresh_stats)
        self.settings.appearance_changed.connect(self.apply_theme)

        self.apply_theme()
        self.goto(0)
        self.on_running(False)
        self.update_online()
        self.dashboard.append_log("info", "Система", "Готово. Добавьте аккаунты, выберите чаты и нажмите «Запустить».")
        if store.settings.get("autostart") and store.accounts:
            QTimer.singleShot(600, self.toggle)

    # ----- навигация -----
    def goto(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == idx)

    def open_chats(self, acc_id: str) -> None:
        self.chats.select_account(acc_id)
        self.goto(2)

    def on_accounts_changed(self) -> None:
        self.chats.reload_accounts()
        self.update_online()

    # ----- тема -----
    def apply_theme(self) -> None:
        s = self.store.settings
        app = QApplication.instance()
        app.setStyleSheet(build_qss(s["theme"], s["accent"], s["radius"], s["font_size"]))
        self.setWindowOpacity(max(0.6, min(1.0, int(s["opacity"]) / 100)))

    # ----- события движка -----
    def toggle(self) -> None:
        if not self.engine.running:
            if not any(a.get("enabled", True) for a in self.store.accounts):
                QMessageBox.information(self, "Нет аккаунтов", "Сначала добавьте и включите хотя бы один аккаунт.")
                self.goto(1)
                return
            self.dashboard.append_log("info", "Система", "Запуск мониторинга…")
            self.engine.run(self.engine.start_all())
        else:
            self.engine.run(self.engine.stop_all())

    def on_running(self, running: bool) -> None:
        self.dashboard.set_running(running)
        if not running:
            for a in self.store.accounts:
                self.accounts.set_status(a["id"], "stopped")
        self.update_online()

    def on_status(self, acc_id: str, status: str) -> None:
        self.accounts.set_status(acc_id, status)
        self.update_online()

    def update_online(self) -> None:
        online = self.accounts.online_count()
        total = len([a for a in self.store.accounts if a.get("enabled", True)])
        self.dashboard.set_online(online, total)
        self.footer.setText(f"{'🟢' if self.engine.running else '⚪'}  Онлайн: {online}/{total}")

    def on_claim(self, rec: dict) -> None:
        self.store.history.append(rec)
        self.store.save_history()
        self.history.add(rec)
        self.dashboard.refresh_stats()
        if rec.get("status") == "success" and self.store.settings.get("sound", True):
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:  # noqa: BLE001
                QApplication.beep()

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            fut = asyncio.run_coroutine_threadsafe(self.engine.stop_all(), self.engine.loop)
            fut.result(timeout=6)
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
