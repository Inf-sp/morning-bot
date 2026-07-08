UI_EMOJI = {
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
}


def ui_label(key, text):
    emoji = UI_EMOJI.get(key)
    return f"{emoji} {text}" if emoji else text
