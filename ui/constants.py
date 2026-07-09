UI_EMOJI = {
    "myday": "☀️",
    "leisure": "🍿",
    "cinema": "🎬",
    "books": "📖",
    "music": "🎧",
    "concerts": "🎸",
    "wardrobe": "👟",
    "clothes": "👕",
    "food": "🥣",
    "recipes": "🍽",
    "products": "🧊",
    "cook_from": "🥕",
    "learning": "📚",
    "dictionary": "🗂️",
    "phrases": "🧩",
    "live_language": "💭",
    "travel": "✈️",
    "countries": "🌍",
    "routes": "🗺",
    "health": "🚑",
    "recommendation": "✨",
    "save": "⭐️",
    "favorite": "❤️",
    "settings": "🎚️",
    "breakfast": "🥐",
    "lunch": "🥗",
    "dinner": "🍲",
    "add": "✏️",
    "delete": "❌",
    "find": "🔍",
    "seen": "✅",
    "warning": "⚠️",
    "hot": "🔥",
    "interesting": "👀",
    "news": "📰",
    "system": "📊",
    "users": "👥",
    "logs": "📈",
    "invite": "🔗",
    "refresh": "🔄",
    "status_ok": "🟢",
    "status_warn": "🟡",
    "status_bad": "🔴",
}

CUISINE_EMOJI = {
    "asian": "🥢",
    "russian": "🇷🇺",
    "italian": "🇮🇹",
    "mediterranean": "🫒",
    "mexican": "🇲🇽",
    "french": "🇫🇷",
    "japanese": "🇯🇵",
    "korean": "🇰🇷",
    "chinese": "🇨🇳",
    "thai": "🇹🇭",
    "vietnamese": "🇻🇳",
    "indian": "🇮🇳",
    "turkish": "🇹🇷",
    "greek": "🇬🇷",
    "spanish": "🇪🇸",
    "german": "🇩🇪",
    "american": "🇺🇸",
    "georgian": "🇬🇪",
}

LANGUAGE_EMOJI = {
    "nl": "🇳🇱",
    "en": "🇬🇧",
}

STATUS_EMOJI = {
    "ok": UI_EMOJI["status_ok"],
    "warn": UI_EMOJI["status_warn"],
    "bad": UI_EMOJI["status_bad"],
}


def ui_label(key, text):
    emoji = UI_EMOJI.get(key)
    return f"{emoji} {text}" if emoji else text


def cuisine_label(key, text):
    emoji = CUISINE_EMOJI.get(key)
    return f"{emoji} {text}" if emoji else text


def language_label(code, text):
    emoji = LANGUAGE_EMOJI.get(code)
    return f"{emoji} {text}" if emoji else text
