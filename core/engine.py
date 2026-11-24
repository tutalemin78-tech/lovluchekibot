"""Движок: управляет всеми аккаунтами Telethon в отдельном потоке с asyncio-циклом."""
from __future__ import annotations

import asyncio
import re
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

try:
    from PyQt6.QtCore import QObject, pyqtSignal
except ImportError:  # запуск без графики (сервер / Render)
    from .qt_shim import QObject, pyqtSignal
from telethon import TelegramClient, errors, events, functions, types, utils
from telethon.sessions import StringSession

from .parser import (
    CHECK_BTN_WORDS,
    CLAIM_BTN_WORDS,
    CheckLink,
    classify,
    extract_links,
    is_confirmed_success,
    parse_amount,
)
from .notifier import Notifier
from .storage import SESSIONS_DIR, Store

BOT_USERNAMES = ("send", "CryptoBot", "xrocket", "tonRocketBot", "wallet")


def parse_proxy(value: str):
    value = (value or "").strip()
    if not value:
        return None
    u = urlparse(value if "://" in value else "socks5://" + value)
    scheme = (u.scheme or "socks5").lower()
    ptype = "http" if scheme.startswith("http") else ("socks4" if scheme == "socks4" else "socks5")
    if not u.hostname or not u.port:
        return None
    return {
        "proxy_type": ptype,
        "addr": u.hostname,
        "port": u.port,
        "username": u.username,
        "password": u.password,
        "rdns": True,
    }


class AccountWorker:
    """Один подключённый аккаунт: слушает чаты и мгновенно активирует чеки."""

    def __init__(self, engine: "Engine", acc: dict) -> None:
        self.engine = engine
        self.acc = acc
        self.client: TelegramClient | None = None
        self.status = "stopped"
        self.seen: set[tuple[str, str]] = set()
        self.bots: dict[str, object] = {}
        self.bot_ids: set[int] = set()
        self.titles: dict[int, str] = {}
        self.me = None
        self._rx_seq = 0                      # счётчик сообщений от ботов (для мгновенного пробуждения)
        self._waiters: list[asyncio.Future] = []

    # ---- утилиты ----
    @property
    def label(self) -> str:
        return self.acc.get("title") or self.acc.get("phone") or self.acc["id"][:6]

    def log(self, level: str, text: str) -> None:
        self.engine.log.emit(level, self.label, text)

    def set_status(self, status: str) -> None:
        self.status = status
        self.engine.account_status.emit(self.acc["id"], status)

    # ---- жизненный цикл ----
    async def start(self) -> None:
        self.set_status("connecting")
        try:
            self.client = self.engine.make_client(self.acc)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.set_status("unauthorized")
                self.log("error", "Аккаунт не авторизован — удалите и добавьте его заново.")
                await self.client.disconnect()
                return
            self.me = await self.client.get_me()
            self.client.add_event_handler(self.on_new, events.NewMessage(incoming=True))
            self.client.add_event_handler(self.on_edit, events.MessageEdited(incoming=True))
            for cid, title in (self.acc.get("chats") or {}).items():
                try:
                    self.titles[int(cid)] = title
                except ValueError:
                    pass
            await self._resolve_bots()
            self.set_status("online")
            self.log("ok", "Подключён и слушает чаты.")
            self.engine.spawn(self._warmup())
            self.engine.spawn(self._safety_poll())
        except Exception as e:  # noqa: BLE001
            self.set_status("error")
            self.log("error", f"Ошибка подключения: {e!r}")

    async def stop(self) -> None:
        try:
            if self.client:
                await self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.client = None
        self.set_status("stopped")

    async def _resolve_bots(self) -> None:
        assert self.client
        for name in BOT_USERNAMES:
            try:
                ent = await self.client.get_entity(name)
                self.bots[name.lower()] = ent
                self.bot_ids.add(ent.id)
            except Exception:  # noqa: BLE001
                pass

    def describe(self) -> dict:
        me = self.me
        name = " ".join(x for x in (getattr(me, "first_name", ""), getattr(me, "last_name", "")) if x)
        n = len(self.acc.get("chats") or {})
        all_chats = self.engine.settings.get("monitor_all") or self.acc.get("monitor_all")
        return {
            "name": name or self.label,
            "username": getattr(me, "username", "") or "",
            "phone": self.acc.get("phone") or "",
            "user_id": getattr(me, "id", ""),
            "chats": "все чаты" if all_chats else f"{n} выбр.",
        }

    async def _safety_poll(self) -> None:
        """Страховка: одним лёгким запросом раз в N сек. смотрит последние посты выбранных чатов.
        Если Telegram вдруг задержал push-обновление, чек всё равно будет пойман. Дубли отсекает seen."""
        last_top: dict[int, int] = {}
        while self.client is not None and self.engine.running:
            interval = float(self.engine.settings.get("safety_poll_sec") or 0)
            await asyncio.sleep(interval if interval > 0 else 2)
            if interval <= 0 or self.status != "online" or not self.client:
                continue
            try:
                peers = []
                for cid in list((self.acc.get("chats") or {}))[:100]:
                    try:
                        peers.append(types.InputDialogPeer(await self.client.get_input_entity(int(cid))))
                    except Exception:  # noqa: BLE001
                        pass
                if not peers:
                    continue
                res = await self.client(functions.messages.GetPeerDialogsRequest(peers=peers))
                for d in res.dialogs:
                    top = getattr(d, "top_message", None)
                    if not top:
                        continue
                    cid = utils.get_peer_id(d.peer)
                    prev = last_top.get(cid)
                    last_top[cid] = top
                    if prev is None or top <= prev:   # первый проход только запоминает состояние
                        continue
                    ids = list(range(max(prev + 1, top - 4), top + 1))
                    for m in await self.client.get_messages(cid, ids=ids):
                        if m:
                            await self.handle(m, cid)
            except errors.FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass

    def _poke(self) -> None:
        """Пришло сообщение от бота — мгновенно будим ожидающую активацию."""
        self._rx_seq += 1
        for f in self._waiters:
            if not f.done():
                f.set_result(None)

    async def _wait_reply(self, timeout: float, seq: int) -> None:
        if seq != self._rx_seq:   # пока читали сообщения, уже пришло новое — не ждём
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            if fut in self._waiters:
                self._waiters.remove(fut)

    async def _warmup(self) -> None:
        """Подгружаем диалоги, чтобы Telegram присылал обновления по всем каналам/чатам."""
        try:
            assert self.client
            async for d in self.client.iter_dialogs():
                self.titles[d.id] = d.name or str(d.id)
        except Exception:  # noqa: BLE001
            pass

    # ---- обработчики ----
    async def on_new(self, event) -> None:
        if event.chat_id in self.bot_ids:
            self._poke()
        await self.handle(event.message, event.chat_id)

    async def on_edit(self, event) -> None:
        if event.chat_id in self.bot_ids:
            self._poke()
        await self.handle(event.message, event.chat_id)

    async def handle(self, msg, chat_id) -> None:
        try:
            if not self.engine.running or not self.engine.watching(self.acc, chat_id):
                return
            if chat_id in self.bot_ids:
                return
            services = self.engine.settings.get("services", {})
            links = [l for l in extract_links(msg) if services.get(l.service, True)]
            if links:
                for link in links:
                    self.engine.spawn(self.claim(link, chat_id))
                return
            # Запасной вариант: чек в виде callback-кнопки «Получить»
            sender = getattr(msg, "sender_id", None)
            via = getattr(msg, "via_bot_id", None)
            if (sender in self.bot_ids or via in self.bot_ids) and msg.buttons:
                key = ("callback", f"{chat_id}:{msg.id}")
                if key in self.seen:
                    return
                self.seen.add(key)
                if await self._press(msg, CLAIM_BTN_WORDS):
                    self.log("ok", f"🖱 Нажата кнопка «Получить» в «{self.titles.get(chat_id, chat_id)}»")
        except Exception as e:  # noqa: BLE001
            self.log("error", f"Ошибка обработки сообщения: {e!r}")

    # ---- активация ----
    async def claim(self, link: CheckLink, chat_id: int) -> None:
        eng = self.engine
        st = eng.settings
        seen = self.seen if st.get("claim_all_accounts", True) else eng.global_seen
        key = (link.service, link.param)
        if key in seen:
            return
        seen.add(key)
        t0 = time.perf_counter()
        chat_title = self.titles.get(chat_id) or str(chat_id)
        self.log("info", f"🎯 Чек {link.service} {link.amount_text or ''} найден в «{chat_title}» — активирую…")

        min_amount = float(st.get("min_amount") or 0)
        if min_amount and link.amount and link.amount[0] < min_amount:
            self.log("warn", f"Пропущен: сумма {link.amount_text} меньше порога {min_amount:g}")
            self._record(link, chat_title, "skipped", "Меньше минимальной суммы", t0)
            return

        delay = int(st.get("click_delay_ms") or 0)
        if delay > 0:
            await asyncio.sleep(delay / 1000)

        status, detail = "timeout", ""
        for attempt in range(1, max(1, int(st.get("retries", 3))) + 1):
            try:
                status, detail = await self._run_claim(link)
            except errors.FloodWaitError as e:
                status, detail = "error", f"FloodWait {e.seconds}s"
                if e.seconds <= 10:
                    await asyncio.sleep(e.seconds + 0.5)
                    continue
                break
            except Exception as e:  # noqa: BLE001
                status, detail = "error", repr(e)
            if status in ("success", "fail"):
                break
            self.log("warn", f"Попытка {attempt}: {status} {detail[:80]}")
            await asyncio.sleep(0.3)

        if status not in ("success", "fail"):
            seen.discard(key)  # позволим повторить, если чек встретится снова
        self._record(link, chat_title, status, detail, t0)

    def _record(self, link: CheckLink, chat_title: str, status: str, detail: str, t0: float) -> None:
        ms = int((time.perf_counter() - t0) * 1000)
        amount = parse_amount(detail) if status == "success" else None
        amount = amount or link.amount
        rec = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "account": self.label,
            "chat": chat_title,
            "service": link.service,
            "param": link.param,
            "amount": f"{amount[0]:g} {amount[1]}" if amount else "",
            "value": amount[0] if amount else None,
            "ticker": amount[1] if amount else "",
            "status": status,
            "detail": " ".join((detail or "").split())[:300],
            "ms": ms,
        }
        if status == "success":
            if is_confirmed_success(detail):
                self.engine.notifier.notify_success(rec)
            self.log("ok", f"✅ Чек активирован! {rec['amount']} за {ms} мс")
        elif status == "fail":
            self.log("warn", f"⚠ Не удалось: {rec['detail'][:140]}")
        elif status == "skipped":
            pass
        else:
            self.log("error", f"❌ Ошибка/таймаут: {rec['detail'][:140]}")
        self.engine.claim_result.emit(rec)

    async def _run_claim(self, link: CheckLink) -> tuple[str, str]:
        assert self.client
        client = self.client
        st = self.engine.settings
        bot = self.bots.get(link.bot.lower()) or await client.get_input_entity(link.bot)
        sent = await client.send_message(bot, f"/start {link.param}", parse_mode=None)
        deadline = time.monotonic() + float(st.get("reply_timeout", 15))
        handled: set = set()
        clicked: set[int] = set()
        subscribed = False
        last = ""
        while time.monotonic() < deadline:
            seq = self._rx_seq
            msgs = await client.get_messages(bot, limit=8, min_id=sent.id)
            for m in sorted(msgs, key=lambda x: x.id):
                if m.out:
                    continue
                sig = (m.id, m.edit_date)
                if sig in handled:
                    continue
                handled.add(sig)
                text = m.raw_text or ""
                last = text or last
                verdict = classify(text)
                if verdict == "success":
                    return "success", text
                if verdict == "fail":
                    return "fail", text
                if verdict == "captcha":
                    return "fail", "Требуется капча: " + text
                if verdict == "subscribe":
                    if st.get("auto_subscribe", True) and not subscribed:
                        subscribed = True
                        await self._subscribe_and_check(m)
                        continue
                    return "fail", "Нужна подписка: " + text
                if m.buttons and m.id not in clicked:
                    clicked.add(m.id)
                    await self._press(m, CLAIM_BTN_WORDS)
            await self._wait_reply(0.25, seq)
        return "timeout", last

    async def _press(self, m, words) -> bool:
        for i, row in enumerate(m.buttons or []):
            for j, b in enumerate(row):
                if getattr(b, "data", None) and any(w in (b.text or "").lower() for w in words):
                    try:
                        await m.click(i, j)
                    except Exception:  # noqa: BLE001  (таймаут ответа бота — это нормально)
                        pass
                    return True
        return False

    async def _subscribe_and_check(self, m) -> None:
        assert self.client
        client = self.client
        joined = 0
        for row in m.buttons or []:
            for b in row:
                url = getattr(b, "url", None)
                if not url or "start=" in url:
                    continue
                try:
                    inv = re.search(r"t\.me/(?:\+|joinchat/)([\w-]+)", url)
                    if inv:
                        await client(functions.messages.ImportChatInviteRequest(inv.group(1)))
                        joined += 1
                        continue
                    pub = re.search(r"t\.me/([A-Za-z0-9_]{4,})/?(?:\d+)?$", url)
                    if pub and not pub.group(1).lower().endswith("bot"):
                        ent = await client.get_input_entity(pub.group(1))
                        await client(functions.channels.JoinChannelRequest(ent))
                        joined += 1
                except errors.UserAlreadyParticipantError:
                    pass
                except Exception as e:  # noqa: BLE001
                    self.log("warn", f"Не удалось подписаться ({url}): {e!r}")
        if joined:
            self.log("info", f"Подписался на {joined} канал(ов), проверяю…")
        await asyncio.sleep(0.5)
        await self._press(m, CHECK_BTN_WORDS)


class Engine(QObject):
    log = pyqtSignal(str, str, str)          # level, account, text
    account_status = pyqtSignal(str, str)    # account id, status
    claim_result = pyqtSignal(dict)
    running_changed = pyqtSignal(bool)
    _call = pyqtSignal(object)

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.running = False
        self.workers: dict[str, AccountWorker] = {}
        self.logins: dict[str, dict] = {}
        self.global_seen: set[tuple[str, str]] = set()
        self.notifier = Notifier(self)
        self._startup_reported = False
        self._tasks: set[asyncio.Task] = set()
        self._watchdog: asyncio.Task | None = None
        self._call.connect(self._invoke)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    @property
    def settings(self) -> dict:
        return self.store.settings

    # ---- межпоточные вызовы ----
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _invoke(self, fn) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print("UI callback error:", e)

    def run(self, coro, ok=None, err=None):
        """Запускает корутину в потоке движка, колбэки выполняются в GUI-потоке."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def done(f):
            try:
                result = f.result()
            except BaseException as e:  # noqa: BLE001
                error = e
                if err:
                    self._call.emit(lambda: err(error))
                return
            if ok:
                self._call.emit(lambda: ok(result))

        fut.add_done_callback(done)
        return fut

    def spawn(self, coro) -> None:
        task = self.loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- клиенты ----
    def make_client(self, acc: dict) -> TelegramClient:
        return TelegramClient(
            StringSession(acc["string_session"]) if acc.get("string_session") else str(SESSIONS_DIR / acc["session"]),
            int(acc["api_id"]),
            acc["api_hash"],
            proxy=parse_proxy(self.settings.get("proxy", "")),
            device_model="Desktop",
            system_version="Windows 10",
            app_version="5.0",
            lang_code="ru",
            system_lang_code="ru-RU",
            connection_retries=-1,
            retry_delay=1,
            auto_reconnect=True,
            request_retries=5,
            flood_sleep_threshold=20,
        )

    def watching(self, acc: dict, chat_id: int) -> bool:
        if self.settings.get("monitor_all") or acc.get("monitor_all"):
            return True
        return str(chat_id) in (acc.get("chats") or {})

    # ---- запуск / остановка ----
    async def start_all(self) -> None:
        self.running = True
        self.global_seen.clear()
        self.running_changed.emit(True)
        await asyncio.gather(
            *(self.start_account(a) for a in self.store.accounts if a.get("enabled", True)),
            return_exceptions=True,
        )
        await self._report_accounts()
        if not self._watchdog or self._watchdog.done():
            self._watchdog = self.loop.create_task(self._watch())

    async def _report_accounts(self) -> None:
        """Показывает все активные (подключённые) аккаунты; в Telegram — один раз за запуск программы."""
        active = [w for w in self.workers.values() if w.status == "online"]
        if not active:
            return
        infos = [w.describe() for w in active]
        self.log.emit("ok", "Система", f"Активные аккаунты ({len(infos)}):")
        for i, a in enumerate(infos, 1):
            uname = f" @{a['username']}" if a["username"] else ""
            phone = f" • {a['phone']}" if a["phone"] else ""
            self.log.emit("ok", "Система", f"  {i}. {a['name']}{uname}{phone} • ID {a['user_id']} • чаты: {a['chats']}")
        if not self._startup_reported and self.settings.get("notify_startup", True):
            self._startup_reported = True
            self.notifier.notify_startup(infos)

    async def stop_all(self) -> None:
        self.running = False
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
        await asyncio.gather(*(w.stop() for w in self.workers.values()), return_exceptions=True)
        self.workers.clear()
        self.running_changed.emit(False)
        self.log.emit("info", "Система", "Мониторинг остановлен.")

    async def start_account(self, acc: dict) -> None:
        old = self.workers.get(acc["id"])
        if old:
            await old.stop()
        w = AccountWorker(self, acc)
        self.workers[acc["id"]] = w
        await w.start()

    async def stop_account(self, acc_id: str) -> None:
        w = self.workers.pop(acc_id, None)
        if w:
            await w.stop()

    async def _watch(self) -> None:
        """Следит, чтобы соединения были живы, и «будит» поток обновлений."""
        while True:
            await asyncio.sleep(15)
            for w in list(self.workers.values()):
                if w.status not in ("online", "connecting") or not w.client:
                    continue
                try:
                    if not w.client.is_connected():
                        w.set_status("connecting")
                        await w.client.connect()
                    await w.client(functions.updates.GetStateRequest())
                    if w.status != "online":
                        w.set_status("online")
                        w.log("ok", "Соединение восстановлено.")
                except Exception as e:  # noqa: BLE001
                    w.log("warn", f"Проблема связи, переподключаюсь: {e!r}")

    # ---- вход в аккаунт ----
    async def login_begin(self, acc: dict) -> bool:
        client = self.make_client(acc)
        await client.connect()
        sent = await client.send_code_request(acc["phone"])
        self.logins[acc["id"]] = {"client": client, "acc": acc, "hash": sent.phone_code_hash}
        return True

    async def login_code(self, acc_id: str, code: str):
        d = self.logins[acc_id]
        try:
            await d["client"].sign_in(d["acc"]["phone"], code.strip(), phone_code_hash=d["hash"])
        except errors.SessionPasswordNeededError:
            return "password"
        return await self._login_done(acc_id)

    async def login_password(self, acc_id: str, password: str):
        d = self.logins[acc_id]
        await d["client"].sign_in(password=password)
        return await self._login_done(acc_id)

    async def _login_done(self, acc_id: str) -> dict:
        d = self.logins.pop(acc_id)
        me = await d["client"].get_me()
        await d["client"].disconnect()
        name = " ".join(x for x in (me.first_name, me.last_name) if x) or d["acc"]["phone"]
        return {"name": name, "username": me.username or "", "user_id": me.id}

    async def login_cancel(self, acc_id: str, drop_session: bool = True) -> None:
        d = self.logins.pop(acc_id, None)
        if d:
            try:
                await d["client"].disconnect()
            except Exception:  # noqa: BLE001
                pass
            if drop_session:
                self.delete_session(d["acc"])

    @staticmethod
    def delete_session(acc: dict) -> None:
        for p in SESSIONS_DIR.glob(acc.get("session", "_none_") + ".session*"):
            try:
                p.unlink()
            except OSError:
                pass

    # ---- список чатов ----
    async def fetch_dialogs(self, acc_id: str) -> list[dict]:
        acc = self.store.account(acc_id)
        if not acc:
            raise RuntimeError("Аккаунт не найден")
        w = self.workers.get(acc_id)
        temp = False
        if w and w.client and w.client.is_connected() and w.status == "online":
            client = w.client
        else:
            client = self.make_client(acc)
            await client.connect()
            temp = True
            if not await client.is_user_authorized():
                await client.disconnect()
                raise RuntimeError("Аккаунт не авторизован")
        try:
            out = []
            async for d in client.iter_dialogs():
                ent = d.entity
                if getattr(ent, "bot", False):
                    kind = "bot"
                elif d.is_channel and not d.is_group:
                    kind = "channel"
                elif d.is_group:
                    kind = "group"
                else:
                    kind = "user"
                out.append({"id": d.id, "title": d.name or str(d.id), "kind": kind})
                if w:
                    w.titles[d.id] = d.name or str(d.id)
            return out
        finally:
            if temp:
                await client.disconnect()
