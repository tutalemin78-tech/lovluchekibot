"""Движок: управляет всеми аккаунтами Telethon в отдельном потоке с asyncio-циклом."""
from __future__ import annotations

import asyncio
import collections
import html
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
    rejected_links,
)
from .notifier import Notifier
from .storage import SESSIONS_DIR, Store

BOT_USERNAMES = ("send", "CryptoBot", "xrocket", "tonRocketBot", "wallet")
DIALOGS_REFRESH_SEC = 15   # как часто обновлять список чатов/каналов


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


class _Seen(set):
    """set с ограничением размера: самые старые записи вытесняются — память не растёт месяцами."""

    def __init__(self, limit: int = 20000) -> None:
        super().__init__()
        self._order: collections.deque = collections.deque()
        self._limit = limit

    def add(self, item) -> None:  # noqa: D401
        if item in self:
            return
        super().add(item)
        self._order.append(item)
        if len(self._order) > self._limit:
            super().discard(self._order.popleft())

    def clear(self) -> None:
        super().clear()
        self._order.clear()


class AccountWorker:
    """Один подключённый аккаунт: слушает чаты и мгновенно активирует чеки."""

    def __init__(self, engine: "Engine", acc: dict) -> None:
        self.engine = engine
        self.acc = acc
        self.client: TelegramClient | None = None
        self.status = "stopped"
        self.seen: set = _Seen()
        self.bots: dict[str, object] = {}
        self.bot_ids: set[int] = set()
        self.titles: dict[int, str] = {}
        self.me = None
        self._rx_seq = 0                      # счётчик сообщений от ботов (для мгновенного пробуждения)
        self._waiters: list[asyncio.Future] = []
        self.dialogs: dict[int, tuple[str, str]] = {}   # id чата -> (название, тип)
        self.dialogs_ts = 0.0                           # когда список чатов обновляли в последний раз
        self.last_event = 0.0                           # когда пришло последнее сообщение
        self.poll_ts = 0.0                              # когда отработала страховочная проверка
        self._pushed: dict[int, int] = {}               # chat_id -> последний id, доставленный push-обновлением
        self._top_seen: dict[int, int] = {}             # chat_id -> последний известный id сообщения
        self._poll_slow = 1.0                           # множитель интервала при FloodWait
        self._ok_streak = 0
        self.status_ts = time.time()
        self.fail_count = 0

    # ---- утилиты ----
    @property
    def label(self) -> str:
        return self.acc.get("title") or self.acc.get("phone") or self.acc["id"][:6]

    def log(self, level: str, text: str) -> None:
        self.engine.log.emit(level, self.label, text)

    def set_status(self, status: str) -> None:
        self.status = status
        self.status_ts = time.time()
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

    async def status_lines(self, i: int) -> tuple[list[str], int]:
        """Подробный статус аккаунта для /status. Возвращает (строки, число проблем)."""
        e = html.escape
        d = self.describe()
        problems = 0
        uname = f" (@{d['username']})" if d["username"] else ""
        names = {"online": "🟢 онлайн", "connecting": "🟡 подключается", "error": "🔴 ошибка подключения",
                 "unauthorized": "🔴 сессия недействительна", "stopped": "⚪ остановлен"}
        lines = [f"<b>{i}. {e(d['name'])}</b>{e(uname)} — {names.get(self.status, self.status)}"]
        if self.status != "online" or not self.client:
            return lines, 1
        now = time.time()
        t0 = time.perf_counter()
        try:  # живая проверка: реально спрашиваем Telegram прямо сейчас
            await asyncio.wait_for(self.client(functions.updates.GetStateRequest()), 8)
            lines.append(f"   📡 связь с Telegram: ок ({int((time.perf_counter() - t0) * 1000)} мс)")
        except Exception:  # noqa: BLE001
            lines.append("   📡 связь с Telegram: ❌ нет ответа")
            problems += 1
        counts = {"channel": 0, "group": 0, "private": 0}
        for _t, kind in self.dialogs.values():
            if kind in counts:
                counts[kind] += 1
        if self.engine.settings.get("monitor_all") or self.acc.get("monitor_all"):
            total = sum(counts.values())
            if not self.dialogs_ts:
                lines.append("   👁 список чатов ещё загружается…")
                problems += 1
            else:
                lines.append(f"   👁 слежу за ВСЕМИ чатами: <b>{total}</b> (каналов {counts['channel']}, "
                             f"групп {counts['group']}, личных {counts['private']})")
        else:
            chats = self.acc.get("chats") or {}
            missing = []
            for cid, title in chats.items():
                try:
                    if int(cid) not in self.dialogs:
                        missing.append(str(title))
                except ValueError:
                    missing.append(str(title))
            lines.append(f"   👁 слежу за выбранными: <b>{len(chats) - len(missing)}/{len(chats)}</b>")
            if missing and self.dialogs_ts:
                lines.append("   ⚠ нет в аккаунте: " + e(", ".join(missing[:5])))
                problems += 1
        if self.dialogs_ts:
            lines.append(f"   🔄 список чатов обновлён {int(now - self.dialogs_ts)} с назад "
                         f"(новые чаты и каналы подхватываются каждые 15 с)")
        lines.append("   💬 последнее сообщение получено " + (f"{int(now - self.last_event)} с назад" if self.last_event else "— пока не было"))
        interval = float(self.engine.settings.get("safety_poll_sec") or 0)
        if interval > 0:
            if self.poll_ts and now - self.poll_ts < max(30, interval * 10):
                lines.append(f"   🛡 страховочная проверка: ок ({int(now - self.poll_ts)} с назад)")
            elif self.poll_ts:
                lines.append("   🛡 страховочная проверка: ⚠ давно не отрабатывала")
                problems += 1
        # диалоги с ботами: если аккаунт ни разу не запускал бота, активация может не пройти
        shown: set[int] = set()
        bots = []
        for name in BOT_USERNAMES:
            ent = self.bots.get(name.lower())
            if ent is None or ent.id in shown:
                continue
            shown.add(ent.id)
            try:
                msgs = await asyncio.wait_for(self.client.get_messages(ent, limit=1), 8)
                bots.append(f"@{getattr(ent, 'username', None) or name} " + ("✓" if msgs else "⚠ не запущен"))
                if not msgs:
                    problems += 1
            except Exception:  # noqa: BLE001
                bots.append(f"@{name} ?")
        if bots:
            lines.append("   🤖 боты: " + e(", ".join(bots)))
        if any("не запущен" in b for b in bots):
            lines.append("   ➡ откройте этих ботов с этого аккаунта и нажмите Start (один раз)")
        return lines, problems

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

    async def _check_top(self, cid: int, top: int) -> None:
        """Сравнивает последнее сообщение чата с известным. Если появилось новое, которое push
        не доставил, — достаёт его и обрабатывает. Первый раз только запоминает состояние."""
        prev = self._top_seen.get(cid)
        if prev is None:
            self._top_seen[cid] = top
            return
        if top <= prev:
            return
        pushed = self._pushed.get(cid, 0)
        if top > pushed:
            ids = list(range(max(prev + 1, pushed + 1, top - 4), top + 1))
            msgs = await self.client.get_messages(cid, ids=ids)   # при ошибке состояние не двигаем — повтор на след. цикле
            self._pushed[cid] = top
            for m in msgs:
                if m:
                    await self.handle(m, cid)
        self._top_seen[cid] = top

    async def _safety_poll(self) -> None:
        """Страховка: раз в N сек. (по умолчанию 1) одним лёгким запросом перечитывает последние
        сообщения чатов. Если Telegram задержал/потерял push-обновление — сообщение всё равно будет
        обработано. При monitor_all смотрит 100 самых свежих диалогов аккаунта (остальные — раз в
        15 с через обновление списка чатов), иначе — выбранные чаты. Если push сработал, лишних
        запросов нет. При FloodWait сама притормаживает и потом ускоряется обратно."""
        while self.client is not None and self.engine.running:
            interval = float(self.engine.settings.get("safety_poll_sec") or 0)
            await asyncio.sleep(interval * self._poll_slow if interval > 0 else 2)
            if interval <= 0 or self.status != "online" or not self.client:
                continue
            try:
                if self.engine.settings.get("monitor_all") or self.acc.get("monitor_all"):
                    res = await self.client(functions.messages.GetDialogsRequest(
                        offset_date=None, offset_id=0, offset_peer=types.InputPeerEmpty(), limit=100, hash=0))
                else:
                    peers = []
                    for cid in list((self.acc.get("chats") or {}))[:100]:
                        try:
                            peers.append(types.InputDialogPeer(await self.client.get_input_entity(int(cid))))
                        except Exception:  # noqa: BLE001
                            pass
                    if not peers:
                        self.poll_ts = time.time()
                        continue
                    res = await self.client(functions.messages.GetPeerDialogsRequest(peers=peers))
                for d in res.dialogs:
                    try:
                        top = getattr(d, "top_message", None)
                        if top:
                            await self._check_top(utils.get_peer_id(d.peer), top)
                    except (errors.FloodWaitError, asyncio.CancelledError):
                        raise
                    except Exception:  # noqa: BLE001
                        pass
                self.poll_ts = time.time()
                self._ok_streak += 1
                if self._poll_slow > 1 and self._ok_streak >= 60:
                    self._poll_slow = max(1.0, self._poll_slow / 2)
                    self._ok_streak = 0
            except errors.FloodWaitError as e:
                self._poll_slow = min(self._poll_slow * 2, 8.0)
                self._ok_streak = 0
                self.log("warn", f"Telegram просит притормозить ({e.seconds} с): страховочная проверка временно реже")
                await asyncio.sleep(min(e.seconds, 60) + 1)
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
        """Загружает диалоги сразу и затем каждую минуту: новые чаты и каналы подхватываются автоматически,
        а Telegram продолжает присылать по ним обновления."""
        first = True
        while self.client is not None and self.engine.running:
            try:
                found: dict[int, tuple[str, str]] = {}
                async for d in self.client.iter_dialogs():
                    try:   # заодно перечитываем последнее сообщение КАЖДОГО чата (страховка для чатов вне топ-100)
                        top = getattr(getattr(d, "message", None), "id", None)
                        if top and self.status == "online" and self.engine.watching(self.acc, d.id):
                            await self._check_top(d.id, top)
                    except (errors.FloodWaitError, asyncio.CancelledError):
                        raise
                    except Exception:  # noqa: BLE001
                        pass
                    if d.is_channel and not d.is_group:
                        kind = "channel"
                    elif d.is_group:
                        kind = "group"
                    elif getattr(d.entity, "bot", False):
                        kind = "bot"
                    else:
                        kind = "private"
                    found[d.id] = (d.name or str(d.id), kind)
                if not first:
                    added = [v[0] for k, v in found.items() if k not in self.dialogs and v[1] != "bot"]
                    if added:
                        more = f" и ещё {len(added) - 5}" if len(added) > 5 else ""
                        self.log("ok", f"➕ Новые чаты/каналы подхвачены ({len(added)}): {', '.join(added[:5])}{more}")
                self.dialogs = found
                for k, v in found.items():
                    self.titles[k] = v[0]
                self.dialogs_ts = time.time()
                first = False
            except errors.FloodWaitError as e:
                await asyncio.sleep(min(e.seconds, 120))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(DIALOGS_REFRESH_SEC)

    # ---- обработчики ----
    def _stamp(self, event) -> None:
        self.last_event = time.time()
        try:
            if event.message.id > self._pushed.get(event.chat_id, 0):
                self._pushed[event.chat_id] = event.message.id
        except Exception:  # noqa: BLE001
            pass

    async def on_new(self, event) -> None:
        self._stamp(event)
        if event.chat_id in self.bot_ids:
            self._poke()
        await self.handle(event.message, event.chat_id)

    async def on_edit(self, event) -> None:
        self._stamp(event)
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
            rej = rejected_links(msg)
            if rej:
                self.log("warn", f"Ссылка на бота есть, но не похожа на чек — пропущена: {rej[0]}")
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
        # лог — после отправки запроса, чтобы ничто не задерживало клик
        asyncio.get_running_loop().call_soon(
            self.log, "info", f"🎯 Чек {link.service} {link.amount_text or ''} найден в «{chat_title}» — активирую…")

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
                if type(e).__name__ == "UserIsBlockedError":   # аккаунт когда-то заблокировал бота — снимаем блок
                    await self._unblock_bots()
            if status in ("success", "fail"):
                break
            self.log("warn", f"Попытка {attempt}: {status} {detail[:80]}")
            await asyncio.sleep(0.3 if status == "timeout" else 1.0)

        if status not in ("success", "fail"):
            seen.discard(key)  # позволим повторить, если чек встретится снова
        self._record(link, chat_title, status, detail, t0)

    def _record(self, link: CheckLink, chat_title: str, status: str, detail: str, t0: float) -> None:
        ms = int((time.perf_counter() - t0) * 1000)
        self.engine.stats[status] = self.engine.stats.get(status, 0) + 1
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
                self.engine.notifier.notify_success(rec, self.describe())
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
                if verdict == "retry":
                    return "error", "Бот просит повторить: " + text
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

    async def _unblock_bots(self) -> None:
        for ent in list(self.bots.values()):
            try:
                await self.client(functions.contacts.UnblockRequest(id=ent))
            except Exception:  # noqa: BLE001
                pass

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
        self.global_seen: set = _Seen()
        self.notifier = Notifier(self)
        self._startup_reported = False
        self.stats: dict[str, int] = {}
        self.started_at = time.time()
        self._tasks: set[asyncio.Task] = set()
        self._watchdog: asyncio.Task | None = None
        self._call.connect(self._invoke)
        self.loop = asyncio.new_event_loop()
        self.loop.set_exception_handler(
            lambda _l, ctx: print("loop error:", ctx.get("message"), repr(ctx.get("exception")), flush=True))
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
        self.notifier.start_commands()
        if not self._watchdog or self._watchdog.done():
            self._watchdog = self.loop.create_task(self._watch())

    async def status_parts(self) -> list[str]:
        """Текст команды /status (несколько сообщений, если аккаунтов много)."""
        workers = list(self.workers.values())
        results = await asyncio.gather(*(w.status_lines(i) for i, w in enumerate(workers, 1)), return_exceptions=True)
        blocks, problems = [], 0
        for r in results:
            if isinstance(r, Exception):
                problems += 1
                continue
            blocks.append("\n".join(r[0]))
            problems += r[1]
        online = sum(1 for w in workers if w.status == "online")
        if not workers:
            problems += 1
        ok = problems == 0 and online == len(workers)
        up = int(time.time() - self.started_at)
        done = self.stats.get("success", 0)
        failed = sum(v for k, v in self.stats.items() if k not in ("success", "skipped"))
        interval = float(self.settings.get("safety_poll_sec") or 0)
        head = [
            "✅ <b>ВСЁ РАБОТАЕТ — все аккаунты активны, чаты и каналы под контролем</b>" if ok
            else "⚠️ <b>ЕСТЬ ПРОБЛЕМЫ — смотрите отметки ⚠/❌ ниже</b>",
            f"🟢 Активных аккаунтов: <b>{online}/{len(workers)}</b>",
            f"⏱ Работаю: {up // 3600} ч {up % 3600 // 60} мин",
            f"🎯 Чеков активировано: <b>{done}</b> • неудачных попыток: {failed}",
            "⚡ Ловлю сообщения мгновенно (push)" + (f" + страховочная проверка каждые {interval:g} с" if interval > 0 else ""),
        ]
        parts, cur = [], "\n".join(head)
        for b in blocks:
            if len(cur) + len(b) + 2 > 3500:
                parts.append(cur)
                cur = b
            else:
                cur += "\n\n" + b
        parts.append(cur)
        return parts

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
        """Самолечение: держит соединения живыми, «будит» поток обновлений, переподключает зависшие
        аккаунты и перезапускает приём команд /status, если он упал."""
        while True:
            await asyncio.sleep(15)
            try:
                self.notifier.start_commands()
            except Exception:  # noqa: BLE001
                pass
            for w in list(self.workers.values()):
                try:
                    if w.status == "error" and time.time() - w.status_ts > 60:
                        w.log("warn", "Переподключаю аккаунт после ошибки…")
                        await self.start_account(w.acc)
                        continue
                    if w.status not in ("online", "connecting") or not w.client:
                        continue
                    if not w.client.is_connected():
                        w.set_status("connecting")
                        await w.client.connect()
                    await asyncio.wait_for(w.client(functions.updates.GetStateRequest()), 20)
                    w.fail_count = 0
                    if w.status != "online":
                        w.set_status("online")
                        w.log("ok", "Соединение восстановлено.")
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    w.fail_count += 1
                    w.log("warn", f"Проблема связи ({w.fail_count}), переподключаюсь: {e!r}")
                    if w.fail_count >= 8:   # ~2 минуты без связи — полностью пересоздаём подключение
                        w.log("warn", "Связь не восстанавливается — пересоздаю подключение.")
                        try:
                            await self.start_account(w.acc)
                        except Exception:  # noqa: BLE001
                            pass

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
