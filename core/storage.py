"""Хранилище настроек, аккаунтов и истории (JSON-файлы в папке data/)."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = BASE_DIR / "sessions"
MAX_ACCOUNTS = 10

DEFAULT_SETTINGS: dict = {
    # --- внешний вид ---
    "theme": "Тёмная",
    "accent": "#4f8cff",
    "radius": 10,
    "font_size": 11,
    "opacity": 100,
    # --- Telegram API (запоминаем последние введённые) ---
    "api_id": "",
    "api_hash": "",
    "proxy": "",
    # --- поведение ---
    "services": {"cryptobot": True, "xrocket": True, "wallet": True},
    "claim_all_accounts": True,   # каждый аккаунт активирует чек сам (для мульти-чеков)
    "monitor_all": False,         # мониторить ВСЕ чаты на ВСЕХ аккаунтах
    "auto_subscribe": True,       # автоматически подписываться на каналы, если чек требует
    "click_delay_ms": 0,          # задержка перед активацией (0 = мгновенно)
    "retries": 5,                 # попыток активации
    "reply_timeout": 15,          # сколько секунд ждать ответ бота
    "min_amount": 0.0,            # игнорировать чеки меньше этой суммы (0 = все)
    "sound": True,
    "autostart": False,           # запускать мониторинг при старте программы
    # --- уведомления в Telegram (токен и ID — в core/notifier.py) ---
    "notify_enabled": True,       # присылать сообщение об УСПЕШНОЙ активации чека
    "notify_startup": True,       # при запуске один раз присылать список активных аккаунтов
    # --- скорость ---
    "safety_poll_sec": 1.0,       # как часто (сек) принудительно перечитывать чаты и сообщения (0 = выкл.)
}


def _read(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return copy.deepcopy(default)


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Store:
    def __init__(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        SESSIONS_DIR.mkdir(exist_ok=True)
        self._settings_path = DATA_DIR / "settings.json"
        self._accounts_path = DATA_DIR / "accounts.json"
        self._history_path = DATA_DIR / "history.json"

        saved = _read(self._settings_path, {})
        self.settings: dict = copy.deepcopy(DEFAULT_SETTINGS)
        for k, v in saved.items():
            if k == "services" and isinstance(v, dict):
                self.settings["services"].update(v)
            else:
                self.settings[k] = v
        self.accounts: list[dict] = _read(self._accounts_path, [])
        self.history: list[dict] = _read(self._history_path, [])

    def save_settings(self) -> None:
        _write(self._settings_path, self.settings)

    def save_accounts(self) -> None:
        _write(self._accounts_path, self.accounts)

    def save_history(self) -> None:
        self.history = self.history[-5000:]
        _write(self._history_path, self.history)

    def account(self, acc_id: str) -> dict | None:
        for a in self.accounts:
            if a["id"] == acc_id:
                return a
        return None
