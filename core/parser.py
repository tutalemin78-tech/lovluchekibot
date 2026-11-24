"""Поиск ссылок на чеки (xRocket / CryptoBot (@send) / @wallet) и разбор ответов ботов."""
from __future__ import annotations

import re
from dataclasses import dataclass

# username бота (в нижнем регистре) -> сервис
BOT_SERVICES = {
    "send": "cryptobot",
    "cryptobot": "cryptobot",
    "xrocket": "xrocket",
    "tonrocketbot": "xrocket",
    "wallet": "wallet",
}

# Префиксы start-параметров
ACCEPT_PREFIX = {
    "cryptobot": ("CQ",),
    "xrocket": ("mc_", "c_", "ch_", "mch_"),
    "wallet": ("C-", "c-"),
}
REJECT_PREFIX = {
    "cryptobot": ("IV", "iv", "ref", "BU"),
    "xrocket": ("inv_", "t_", "ref", "r_"),
    "wallet": ("WV", "wv"),
}

LINK_RE = re.compile(
    r"(?:https?://)?(?:t|telegram)\.me/(?P<bot>[A-Za-z0-9_]+)\?start=(?P<param>[A-Za-z0-9_\-]+)",
    re.I,
)
TG_RE = re.compile(
    r"tg://resolve\?domain=(?P<bot>[A-Za-z0-9_]+)&start=(?P<param>[A-Za-z0-9_\-]+)", re.I
)

TICKERS = [
    "USDT", "TONCOIN", "TON", "BTC", "ETH", "USDC", "BNB", "TRX", "LTC", "NOT",
    "DOGE", "SOL", "XRP", "JET", "GRAM", "BUSD", "DAI", "MATIC", "DOT", "HMSTR",
]
AMOUNT_RE = re.compile(
    r"(\d[\d\s\u00a0]*(?:[.,]\d+)?)\s*(" + "|".join(TICKERS) + r")\b", re.I
)

HINT_WORDS = (
    "получить", "получи", "забрать", "активир", "claim", "activate", "receive",
    "чек", "cheque", "check",
)
CLAIM_BTN_WORDS = (
    "получить", "получи", "забрать", "активир", "claim", "activate", "receive", "redeem",
)
CHECK_BTN_WORDS = (
    "провер", "check", "подписал", "готово", "done", "verify", "continue", "продолж", "я подп",
)

SUCCESS_WORDS = (
    "вы получили", "вы активировали", "вы забрали", "you received", "you got", "you have received",
    "you've received", "чек активирован", "check activated", "successfully", "успешно",
    "зачислен", "получено", "зачислено", "credited", "вам начислен",
)
FAIL_WORDS = (
    "уже активирован", "уже использован", "уже получен", "already", "не найден", "not found",
    "expired", "истек", "истёк", "закончил", "исчерпан", "недействител", "invalid", "не может быть",
    "cannot", "can't", "недостаточно", "insufficient", "лимит", "limit", "только для", "only for",
    "отключ", "disabled", "удален", "удалён", "deleted", "was cancelled", "отменен", "отменён",
    "не доступ", "not available", "someone else", "кто-то другой",
)
SUBSCRIBE_WORDS = (
    "подпиш", "subscribe", "подписк", "join the", "вступи", "required subscription",
    "необходимо подписаться",
)
CAPTCHA_WORDS = ("captcha", "капч", "я не робот", "not a robot")


@dataclass
class CheckLink:
    service: str
    bot: str          # username бота из ссылки
    param: str        # start-параметр
    label: str = ""   # текст кнопки
    amount: tuple | None = None   # (значение, тикер)

    @property
    def amount_text(self) -> str:
        if not self.amount:
            return ""
        v, t = self.amount
        return f"{v:g} {t}"


def parse_amount(text: str) -> tuple[float, str] | None:
    if not text:
        return None
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    raw = re.sub(r"[\s\u00a0]", "", m.group(1)).replace(",", ".")
    try:
        return float(raw), m.group(2).upper()
    except ValueError:
        return None


def _is_hinted(*texts: str) -> bool:
    joined = " ".join(t for t in texts if t).lower()
    return any(w in joined for w in HINT_WORDS)


def _accept(service: str, param: str, hinted: bool) -> bool:
    if param.startswith(REJECT_PREFIX.get(service, ())):
        return False
    if param.startswith(ACCEPT_PREFIX.get(service, ())):
        return True
    return hinted  # неизвестный формат — берём только если рядом слова «получить/чек»


def _candidates(message) -> list[tuple[str, str]]:
    """Возвращает список (url, подпись) из кнопок, сущностей и текста сообщения."""
    out: list[tuple[str, str]] = []

    try:
        rows = message.buttons or []
    except Exception:
        rows = []
    for row in rows:
        for b in row:
            url = getattr(b, "url", None)
            if url:
                out.append((url, getattr(b, "text", "") or ""))

    text = message.raw_text or ""
    try:
        for ent, ent_text in message.get_entities_text():
            url = getattr(ent, "url", None)
            if url:
                out.append((url, ent_text))
            elif ent.__class__.__name__ == "MessageEntityUrl":
                out.append((ent_text, ""))
    except Exception:
        pass

    for m in LINK_RE.finditer(text):
        out.append((m.group(0), ""))
    return out


def extract_links(message) -> list[CheckLink]:
    text = message.raw_text or ""
    result: list[CheckLink] = []
    seen: set[tuple[str, str]] = set()
    for url, label in _candidates(message):
        m = LINK_RE.search(url) or TG_RE.search(url)
        if not m:
            continue
        bot = m.group("bot")
        service = BOT_SERVICES.get(bot.lower())
        if not service:
            continue
        param = m.group("param")
        if not _accept(service, param, _is_hinted(label, text)):
            continue
        key = (service, param)
        if key in seen:
            continue
        seen.add(key)
        amount = parse_amount(label) or parse_amount(text)
        result.append(CheckLink(service, bot, param, label, amount))
    return result


def _classify_base(text: str) -> str:
    """success | fail | subscribe | captcha | unknown"""
    t = (text or "").lower()
    if not t:
        return "unknown"
    if any(w in t for w in FAIL_WORDS):
        return "fail"
    if any(w in t for w in CAPTCHA_WORDS):
        return "captcha"
    if any(w in t for w in SUBSCRIBE_WORDS):
        return "subscribe"
    if any(w in t for w in SUCCESS_WORDS):
        return "success"
    return "unknown"


# --- усиленное распознавание успеха (чтобы не пропустить и не принять чужое за успех) ---
# Явные фразы о получении денег; "you already received" сюда не попадает.
_RECEIVED_RE = re.compile(
    r"вы\s+(?:успешно\s+)?(?:получили|активировали|забрали)"
    r"|you(?:\s+have|'ve)?\s+(?:successfully\s+)?(?:received|got|claimed)",
    re.I,
)
# Подтверждение успеха для уведомления (вызывается, только если классификатор уже сказал "success").
_CONFIRM_RE = re.compile(
    r"вы\s+(?:успешно\s+)?(?:получили|активировали|забрали)"
    r"|you(?:\s+have|'ve)?\s+(?:successfully\s+)?(?:received|got|claimed|activated)"
    r"|(?:чек|check|cheque)[^.\n]{0,40}(?:активирован|activated|получен|received|claimed)"
    r"|успешно\s+(?:получен|активирован|зачисл)|successfully\s+(?:received|claimed|activated|credited)"
    r"|зачислен|credited|вам\s+начислен",
    re.I,
)
_ALREADY_RE = re.compile(r"уже|already", re.I)


def classify(text: str) -> str:
    """success | fail | subscribe | captcha | unknown

    Как и раньше, но фраза «Вы получили 5 USDT» с суммой считается успехом, даже если
    в ответе бота рядом встретилось служебное слово из списка ошибок (limit, cannot...).
    """
    t = text or ""
    if _RECEIVED_RE.search(t) and parse_amount(t) and not _ALREADY_RE.search(t):
        return "success"
    return _classify_base(t)


def is_confirmed_success(text: str) -> bool:
    """True, если ответ бота действительно говорит о получении/активации (для уведомления)."""
    t = text or ""
    return bool(_CONFIRM_RE.search(t) or parse_amount(t))
