"""Создание строк сессий для запуска на Render. Запускать на СВОЁМ компьютере (нужен ввод кода из Telegram).

    pip install telethon
    python make_session.py

Для каждого аккаунта: номер → код из Telegram → (если есть) облачный пароль.
В конце выведет готовый блок переменных окружения и сохранит его в render_env.txt.
ВАЖНО: строка сессии = полный доступ к аккаунту. Никому не показывайте и не выкладывайте в GitHub.
"""
import asyncio
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession


async def login(api_id: int, api_hash: str) -> str:
    client = TelegramClient(
        StringSession(), api_id, api_hash,
        device_model="Desktop", system_version="Windows 10", app_version="5.0",
        lang_code="ru", system_lang_code="ru-RU",
    )
    await client.start()  # спросит номер, код и (если нужно) облачный пароль
    me = await client.get_me()
    print(f"✓ Вошли как {me.first_name or ''} {('@' + me.username) if me.username else ''} (ID {me.id})")
    s = client.session.save()
    await client.disconnect()
    return s


async def main() -> None:
    print("api_id и api_hash берутся на https://my.telegram.org → API development tools\n")
    api_id = int(input("api_id: ").strip())
    api_hash = input("api_hash: ").strip()
    sessions: list[str] = []
    while len(sessions) < 10:
        sessions.append(await login(api_id, api_hash))
        if input("Добавить ещё аккаунт? (y/n): ").strip().lower() != "y":
            break
    lines = [f"API_ID={api_id}", f"API_HASH={api_hash}"] + [f"SESSION_{i}={s}" for i, s in enumerate(sessions, 1)]
    text = "\n".join(lines)
    Path("render_env.txt").write_text(text + "\n", encoding="utf-8")
    print("\n===== ВСТАВЬТЕ ЭТО В Render → Environment =====\n" + text + "\n===============================================")
    print("Сохранено также в render_env.txt (не выкладывайте файл в GitHub).")


if __name__ == "__main__":
    asyncio.run(main())
