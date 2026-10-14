"""Общие helpers для коротких Telegram-карточек."""

import re


def clean_card_text(value):
    value = re.sub(r"<[^>]+>", "", str(value or ""))
    value = re.sub(r"^[\s\U0001F1E6-\U0001FAFF\u2600-\u27BF\u200D\uFE0F]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def ru_plural(n, one, few, many):
    """Русская форма слова для числа: 1 сезон, 2 сезона, 5 сезонов, 11 сезонов."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def finish_dot(value):
    value = clean_card_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


