"""Check Hunter без графики — для Render / VPS / Docker.

Ничего нажимать не нужно: при старте сам подключает все аккаунты и начинает ловить чеки.
Всё настраивается переменными окружения (см. ИНСТРУКЦИЯ-RENDER.txt):
    API_ID, API_HASH      — с https://my.telegram.org
    SESSION_1 … SESSION_10 (или SESSIONS через пробел) — строки сессий из make_session.py
    CHATS                 — (необязательно) ID чатов через запятую; если пусто — слушаем ВСЕ чаты аккаунтов
    PROXY                 — (необязательно) socks5://user:pass@host:port
    PORT                  — Render задаёт сам
"""
from __future__ import annotations

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from core.engine import Engine
from core.storage import MAX_ACCOUNTS, Store


def say(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def load_sessions() -> list[str]:
    out: list[str] = []
    out += [x for x in os.environ.get("SESSIONS", "").replace(",", " ").split() if x]
    for i in range(1, 21):
        v = (os.environ.get(f"SESSION_{i}") or "").strip()
        if v:
            out.append(v)
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq[:MAX_ACCOUNTS]


def main() -> None:
    api_id = (os.environ.get("API_ID") or "").strip()
    api_hash = (os.environ.get("API_HASH") or "").strip()
    sessions = load_sessions()
    if not api_id.isdigit() or not api_hash or not sessions:
        say("ОШИБКА: задайте переменные окружения API_ID, API_HASH и SESSION_1 (строка сессии из make_session.py).")
        sys.exit(1)

    chats = {c.strip(): c.strip() for c in (os.environ.get("CHATS") or "").split(",") if c.strip()}
    store = Store()
    store.settings["monitor_all"] = not chats
    store.settings["proxy"] = (os.environ.get("PROXY") or "").strip()
    store.accounts = [
        {
            "id": f"s{i}", "title": os.environ.get(f"NAME_{i}") or f"Аккаунт {i}", "phone": "",
            "session": f"s{i}", "string_session": s, "api_id": int(api_id), "api_hash": api_hash,
            "enabled": True, "chats": dict(chats),
        }
        for i, s in enumerate(sessions, 1)
    ]

    engine = Engine(store)
    engine.log.connect(lambda lvl, acc, text: say(f"[{acc}] {text}"))
    engine.claim_result.connect(lambda r: say(f"РЕЗУЛЬТАТ: {r['status']} {r['amount']} {r['service']} ({r['account']}, {r['ms']} мс)"))

    # Порт для Render — открываем сразу, чтобы деплой считался успешным
    class Health(BaseHTTPRequestHandler):
        def _ok(self, body=True):
            online = sum(1 for w in engine.workers.values() if w.status == "online")
            data = f"Check Hunter OK. online={online}/{len(store.accounts)}\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if body:
                self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            self._ok()

        def do_HEAD(self):  # noqa: N802
            self._ok(False)

        def log_message(self, *a):
            pass

    server = HTTPServer(("0.0.0.0", int(os.environ.get("PORT") or 10000)), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    say(f"Запуск: аккаунтов {len(store.accounts)}, режим: {'все чаты' if not chats else str(len(chats)) + ' чатов'}")
    fut = engine.run(engine.start_all())
    try:
        fut.result(timeout=180)
    except Exception as e:  # noqa: BLE001
        say(f"Запуск занял слишком долго или упал: {e!r}")

    # Переподключаем аккаунты, которые не смогли подключиться (сбой сети и т.п.)
    while True:
        time.sleep(60)
        for acc in store.accounts:
            w = engine.workers.get(acc["id"])
            if w and w.status == "error":
                say(f"Переподключаю {w.label}…")
                engine.run(engine.start_account(acc))


if __name__ == "__main__":
    main()
