"""Диалог добавления аккаунта: API данные -> код из Telegram -> (2FA пароль)."""
from __future__ import annotations

import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from core.engine import Engine
from core.storage import Store
from .widgets import hbox, make_button


class AddAccountDialog(QDialog):
    def __init__(self, store: Store, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.engine = engine
        self.result_account: dict | None = None
        self.acc: dict | None = None
        self.state = 0  # 0 - данные, 1 - код, 2 - пароль
        self.busy = False

        self.setWindowTitle("Добавить аккаунт")
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        title = QLabel("Новый аккаунт Telegram")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        hint = QLabel(
            "api_id и api_hash берутся на https://my.telegram.org → API development tools.\n"
            "Одни и те же данные можно использовать для всех аккаунтов."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)
        self.api_id = QLineEdit(str(store.settings.get("api_id", "")))
        self.api_id.setPlaceholderText("12345678")
        self.api_hash = QLineEdit(store.settings.get("api_hash", ""))
        self.api_hash.setPlaceholderText("0123456789abcdef0123456789abcdef")
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("+79991234567")
        self.code = QLineEdit()
        self.code.setPlaceholderText("Код из Telegram")
        self.password = QLineEdit()
        self.password.setPlaceholderText("Облачный пароль (2FA)")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("api_id", self.api_id)
        form.addRow("api_hash", self.api_hash)
        form.addRow("Телефон", self.phone)
        self.code_label = QLabel("Код")
        self.pass_label = QLabel("Пароль 2FA")
        form.addRow(self.code_label, self.code)
        form.addRow(self.pass_label, self.password)
        root.addLayout(form)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        root.addWidget(self.status)

        self.next_btn = make_button("Получить код", "primary", self.on_next)
        cancel = make_button("Отмена", "", self.reject)
        root.addLayout(hbox(None, cancel, self.next_btn))
        self._update_state()

    def _update_state(self) -> None:
        for w in (self.api_id, self.api_hash, self.phone):
            w.setEnabled(self.state == 0)
        for w in (self.code_label, self.code):
            w.setVisible(self.state == 1)
        for w in (self.pass_label, self.password):
            w.setVisible(self.state == 2)
        self.next_btn.setText(["Получить код", "Войти", "Подтвердить пароль"][self.state])
        self.adjustSize()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        self.next_btn.setEnabled(not busy)
        self.status.setText(text)

    def _fail(self, err: BaseException) -> None:
        self._set_busy(False)
        self.status.setText("")
        QMessageBox.warning(self, "Ошибка", f"{type(err).__name__}: {err}")

    def on_next(self) -> None:
        if self.busy:
            return
        if self.state == 0:
            api_id = self.api_id.text().strip()
            api_hash = self.api_hash.text().strip()
            phone = self.phone.text().strip().replace(" ", "")
            if not api_id.isdigit() or len(api_hash) < 20 or not phone:
                QMessageBox.warning(self, "Проверьте данные", "Заполните api_id (число), api_hash и телефон.")
                return
            if any(a.get("phone") == phone for a in self.store.accounts):
                QMessageBox.warning(self, "Дубликат", "Этот номер уже добавлен.")
                return
            acc_id = uuid.uuid4().hex
            self.acc = {
                "id": acc_id,
                "session": f"acc_{acc_id[:10]}",
                "phone": phone,
                "api_id": int(api_id),
                "api_hash": api_hash,
                "title": phone,
                "enabled": True,
                "monitor_all": False,
                "chats": {},
            }
            self.store.settings["api_id"] = api_id
            self.store.settings["api_hash"] = api_hash
            self.store.save_settings()
            self._set_busy(True, "Отправляю код…")
            self.engine.run(self.engine.login_begin(self.acc), self._code_sent, self._fail)
        elif self.state == 1:
            code = self.code.text().strip()
            if not code:
                return
            self._set_busy(True, "Проверяю код…")
            self.engine.run(self.engine.login_code(self.acc["id"], code), self._after_code, self._fail)
        else:
            pw = self.password.text()
            if not pw:
                return
            self._set_busy(True, "Проверяю пароль…")
            self.engine.run(self.engine.login_password(self.acc["id"], pw), self._finish, self._fail)

    def _code_sent(self, _result) -> None:
        self.state = 1
        self._set_busy(False, "Код отправлен в Telegram (приложение или SMS).")
        self._update_state()
        self.code.setFocus()

    def _after_code(self, result) -> None:
        if result == "password":
            self.state = 2
            self._set_busy(False, "Включена двухэтапная проверка — введите облачный пароль.")
            self._update_state()
            self.password.setFocus()
        else:
            self._finish(result)

    def _finish(self, info: dict) -> None:
        name = info.get("name") or self.acc["phone"]
        uname = f" (@{info['username']})" if info.get("username") else ""
        self.acc["title"] = f"{name}{uname}"
        self.result_account = self.acc
        self.accept()

    def reject(self) -> None:
        if self.acc and self.result_account is None:
            self.engine.run(self.engine.login_cancel(self.acc["id"]))
        super().reject()
