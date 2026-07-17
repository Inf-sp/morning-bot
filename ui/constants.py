UI_EMOJI = {
    "myday": "🌡️",
    "leisure": "🍿",
    "cinema": "🎬",
    "books": "📖",
    "music": "🎧",
    "concerts": "🎫",
    "wardrobe": "👟",
    "food": "🥣",
    "recipes": "🍽",
    "version": "🚀",
    "products": "🧊",
    "cook_from": "🥕",
    "learning": "📚",
    "dictionary": "🗂️",
    "phrases": "🧩",
    "live_language": "💭",
    "word_trainer": "🧠",
    "game": "🕵️",
    "travel": "✈️",
    "countries": "🌍",
    "routes": "🗺",
    "health": "🚑",
    "doctor": "👩🏻‍⚕️",
    "worry_diary": "😮‍💨",
    "recommendation": "✨",
    "save": "💾",
    "favorite": "❤️",
    "settings": "🎚️",
    "breakfast": "🥐",
    "lunch": "🥗",
    "dinner": "🍲",
    "add": "🆕",
    "delete": "❌",
    "find": "🔍",
    "choose": "*️⃣",
    "seen": "✅",
    "warning": "⚠️",
    "hot": "🔥",
    "interesting": "👨🏻‍💻",
    "system": "📊",
    "users": "👨🏻‍💻",
    "logs": "📈",
    "invite": "🔗",
    "refresh": "🔄",
    "status_ok": "🟢",
    "status_warn": "🟡",
    "status_bad": "🔴",
    "status_unknown": "⚪",
    "admin": "🛠️",
    "welcome": "👋",
    "diagnostics": "📊",
    "api": "🔌",
    "llm": "🤖",
    "notifications": "🔔",
    "tests": "🧪",
    "history": "📜",
    "profile": "👤",
    "broadcasts": "🔔",
    "personalization": "🎛️",
    "cuisines": "🍽",
    "clothing_style": "👟",
    "personal_data": "🌀",
    "examples": "💬",
    "sections": "📂",
    "translation": "🤖",
    "usage": "💭",
    "example": "💬",
    "knowledge": "🦉",
    "quote": "💬",
    "worries": "📓",
    "evening": "🌙",
    "summary": "✅",
    "assessment": "🧐",
    "shopping": "🛍️",
    "avoid": "⚠️",
    "potential": "✨",
    "why_today": "☀️",
    "empty_wardrobe": "✂️",
    "when": "🕒",
    "action": "✅",
    "week": "🗓️",
    "reason": "✨",
    "spoken_language": "👩🏻‍🏫",
    "best_time": "🕒",
    "budget": "💶",
    "dont_miss": "❗️",
    "lgbtq": "🌈",
    # Дополнительная семантика разделов и действий. Значения централизованы здесь,
    # даже если конкретный экран пока использует литерал для читаемости разметки.
    "answer": "⌨️", "energy": "⚡", "evening_weather": "🌦️",
    "art": "🎨", "theatre": "🎭", "target": "🎯", "guitar": "🎸",
    "pets": "🐾", "clothes": "👕", "ghost": "👻", "romance": "💕",
    "romance_alt": "💘", "action_movie": "💥", "technology": "💻",
    "clipboard": "📋", "pin": "📌", "signal": "📶", "photo": "📷",
    "knife": "🔪", "speech": "🗣", "route_alt": "🗺️", "comedy": "😂",
    "calm": "😌", "scary": "😱", "surprise": "😲", "tools": "🛠",
    "thinking": "🤔", "disguise": "🥸", "compass": "🧭", "luggage": "🧳",
    "mood": "🫥", "give_up": "🫪", "atmosphere": "🌫️",
}

CUISINE_EMOJI = {
    "european": "🇪🇺",
    "international": "🌍",
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

COUNTRY_EMOJI = {
    "nl": "🇳🇱",
    "be": "🇧🇪",
    "de": "🇩🇪",
    "fr": "🇫🇷",
    "gb": "🇬🇧",
    "es": "🇪🇸",
    "it": "🇮🇹",
    "at": "🇦🇹",
    "ch": "🇨🇭",
    "pl": "🇵🇱",
    "se": "🇸🇪",
    "dk": "🇩🇰",
    "pt": "🇵🇹",
}

WEATHER_EMOJI = {
    "sun": "☀️",
    "cloud": "☁️",
    "rain": "🌧️",
    "storm": "⛈️",
    "thunderstorm": "🌩️",
    "snow": "❄️",
    "fog": "🌫️",
    "wind": "💨",
    "temperature": "🌡️",
    "humidity": "💧",
    "wind_direction": "🌬️",
    "heat": "🥵",
    "cold": "🥶",
    "tornado": "🌪️",
    "waves": "🌊",
}

STATUS_EMOJI = {
    "ok": UI_EMOJI["status_ok"],
    "warn": UI_EMOJI["status_warn"],
    "bad": UI_EMOJI["status_bad"],
    "unknown": UI_EMOJI["status_unknown"],
}


def ui_label(key, text):
    emoji = UI_EMOJI.get(key)
    return f"{emoji} {text}" if emoji else text


def save_toggle_label(saved):
    return "✅ Сохранено" if saved else ui_label("save", "Сохранить")


def delete_label(text):
    """Единая подпись для любой кнопки, удаляющей или убирающей данные."""
    text = str(text or "").strip()
    emoji = UI_EMOJI["delete"]
    return text if text.startswith(emoji) else f"{emoji} {text}"


def choose_label(text):
    """Единая подпись для любого действия выбора: «*️⃣ Выбрать …»."""
    text = str(text or "").strip()
    emoji = UI_EMOJI["choose"]
    return text if text.startswith(emoji) else f"{emoji} {text}"


def cuisine_label(key, text):
    emoji = CUISINE_EMOJI.get(key)
    return f"{emoji} {text}" if emoji else text
