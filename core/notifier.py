"""Уведомления в Telegram через Bot API.

Шлёт сообщение ТОЛЬКО когда чек успешно активирован + один раз за запуск программы —
список активных аккаунтов. Ничего не блокирует: отправка идёт в отдельной очереди.
Использует только стандартную библиотеку (urllib) — новых зависимостей нет.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import time
import urllib.error
import urllib.request

# ============ НАСТРОЙКИ УВЕДОМЛЕНИЙ (вписаны прямо в код) ============
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "9116253354:AAEGeVfToUUzaLPBQqKiM1UDRzxwnqKdx7Y"   # токен бота-уведомителя
OWNER_ID = int(os.environ.get("OWNER_ID") or 7383168722)                                       # ваш Telegram ID
# =======================================================================

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
SERVICE_NAMES = {"cryptobot": "CryptoBot", "xrocket": "xRocket", "wallet": "Wallet"}


class Notifier:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None
        self._last_warn = 0.0

    # ---- настройки ----
    def _cfg(self):
        enabled = bool(self.engine.settings.get("notify_enabled", True))
        return enabled, BOT_TOKEN, OWNER_ID

    # ---- публичное API (можно вызывать из любого потока) ----
    def notify(self, text: str) -> None:
        enabled, token, chat = self._cfg()
        if not enabled:
            return
        self.engine.loop.call_soon_threadsafe(self._enqueue, (token, chat, text))

    def notify_success(self, rec: dict) -> None:
        e = html.escape
        service = SERVICE_NAMES.get(rec.get("service", ""), rec.get("service", "") or "—")
        lines = [
            "✅ <b>Чек успешно активирован!</b>",
            "",
            f"💰 Сумма: <b>{e(rec.get('amount') or '—')}</b>",
            f"🏦 Сервис: {e(service)}",
            f"👤 Аккаунт: {e(str(rec.get('account') or '—'))}",
            f"💬 Источник: {e(str(rec.get('chat') or '—'))}",
            f"⚡ Скорость: {int(rec.get('ms') or 0)} мс",
            f"🕒 {e(str(rec.get('time') or ''))}",
        ]
        self.notify("\n".join(lines))

    def notify_startup(self, accounts: list[dict]) -> None:
        e = html.escape
        lines = [f"🟢 <b>Check Hunter запущен</b>", f"Активных аккаунтов: <b>{len(accounts)}</b>", ""]
        for i, a in enumerate(accounts, 1):
            uname = f" (@{a['username']})" if a.get("username") else ""
            lines.append(f"{i}. <b>{e(a['name'])}</b>{e(uname)}")
            phone = f"📱 {e(a['phone'])} • " if a.get("phone") else ""
            lines.append(f"    {phone}ID {a.get('user_id') or '—'} • 💬 {e(a['chats'])}")
        self.notify("\n".join(lines))

    # ---- внутреннее ----
    def _enqueue(self, item) -> None:  # выполняется в потоке движка
        if self._queue is None:
            self._queue = asyncio.Queue()
        self._queue.put_nowait(item)
        if self._worker is None or self._worker.done():
            self._worker = self.engine.loop.create_task(self._run())

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            token, chat, text = await self._queue.get()
            try:
                await self._deliver(token, chat, text)
            except Exception as ex:  # noqa: BLE001
                self._warn(f"Ошибка отправки уведомления: {type(ex).__name__}")
            finally:
                self._queue.task_done()
            await asyncio.sleep(0.05)

    async def _deliver(self, token: str, chat, text: str) -> None:
        loop = asyncio.get_running_loop()
        delay = 1.0
        err = ""
        for _ in range(4):
            ok, err, retry_after, fatal = await loop.run_in_executor(None, self._post, token, chat, text)
            if ok:
                return
            if fatal:
                break
            await asyncio.sleep(retry_after + 0.5 if retry_after else delay)
            delay *= 2
        self._warn(f"Не удалось отправить уведомление в Telegram: {err}")

    @staticmethod
    def _post(token: str, chat, text: str):
        """-> (ok, ошибка, retry_after, фатальная_ошибка)"""
        payload = json.dumps(
            {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        ).encode("utf-8")
        req = urllib.request.Request(
            API_URL.format(token=token), data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = json.loads(r.read().decode("utf-8"))
            return bool(body.get("ok")), "", 0, False
        except urllib.error.HTTPError as ex:
            try:
                body = json.loads(ex.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                body = {}
            desc = str(body.get("description") or "")
            retry = int((body.get("parameters") or {}).get("retry_after") or 0)
            if ex.code in (401, 404):
                return False, "неверный токен бота (поправьте BOT_TOKEN в core/notifier.py)", 0, True
            if ex.code == 403:
                return False, "бот не может написать вам — откройте его в Telegram и нажмите /start", 0, True
            if ex.code == 400:
                return False, f"{desc or 'неверный запрос'} (поправьте OWNER_ID в core/notifier.py)", 0, True
            return False, f"HTTP {ex.code} {desc}".strip(), retry, False
        except Exception as ex:  # noqa: BLE001  (нет сети и т.п.) — токен в текст не попадает
            return False, type(ex).__name__, 0, False

    def _warn(self, text: str) -> None:
        if time.monotonic() - self._last_warn < 60:  # не засоряем журнал
            return
        self._last_warn = time.monotonic()
        self.engine.log.emit("warn", "Уведомления", text)
