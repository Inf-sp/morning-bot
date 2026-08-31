"""Pure helpers for compact weekly weather summaries."""

SNOW_CODES = (71, 73, 75, 77, 85, 86)


def qualitative_outlook(days, period="На следующей неделе"):
    """Describe expected weather in words, without exposing numeric values."""
    rainy = sum(bool(day.get("rain_real")) for day in days)
    snowy = sum(day.get("code") in SNOW_CODES for day in days)
    windy = sum(float(day.get("wind") or 0) >= 8 for day in days)
    hot = sum(float(day.get("tmax") or 0) >= 30 for day in days)
    cold = sum(float(day.get("tmax") or 0) <= 10 for day in days)

    if snowy:
        weather = "ожидается снег, возможны скользкие дороги"
    elif rainy >= max(1, len(days) // 2):
        weather = "будет часто идти дождь, но возможны короткие сухие окна"
    elif rainy:
        weather = "погода будет переменчивой, местами пройдут дожди"
    elif hot:
        weather = "будет жарко и преимущественно сухо"
    elif cold:
        weather = "будет прохладно, тёплый слой пригодится"
    else:
        weather = "ожидается спокойная погода без продолжительных осадков"

    if windy >= max(1, len(days) // 2):
        weather += ", ветер будет заметным"
    elif windy:
        weather += ", временами усилится ветер"
    return f"{period} {weather}."


def week_overview(days):
    """Build a short summary from daily weather without nightly lows."""
    low = min(day["tmax"] for day in days)
    high = max(day["tmax"] for day in days)
    wet = sum(day["rain_real"] for day in days)
    clear = sum(day["code"] in (0, 1) and not day["rain_real"] for day in days)
    cloudy = sum(day["code"] in (3, 45, 48) for day in days)
    snow = sum(day["code"] in SNOW_CODES for day in days)
    max_wind = max(day["wind"] for day in days)
    avg_wind = sum(day["wind"] for day in days) / len(days)

    if snow:
        icon, description = "❄️", "Временами снег"
    elif wet >= 4:
        icon, description = "🌧️", "Часто дождь"
    elif wet >= 2:
        icon, description = "🌦️", "Переменная облачность, временами дождь"
    elif clear >= 5:
        icon, description = "☀️", "В основном ясно"
    elif clear >= 3:
        icon, description = "🌤️", "В основном малооблачно"
    elif cloudy >= 4:
        icon, description = "☁️", "В основном облачно"
    else:
        icon, description = "🌤️", "Переменная облачность"

    if max_wind >= 11:
        description += ", сильный ветер"
    elif max_wind >= 8:
        description += ", временами ветрено"
    elif avg_wind >= 5:
        description += ", умеренный ветер"
    return f"{icon} {low:+.0f}…{high:.0f}°C · {description}"


def week_advice(days, strong_wind_ms=8):
    """Return one practical suggestion tied to the actual forecast."""
    strong_wind = [day for day in days if day["wind"] >= strong_wind_ms]
    rainy = [day for day in days if day["rain_real"]]
    outdoor = [day for day in days if not day["rain_real"] and day["wind"] < strong_wind_ms]
    hottest = max(days, key=lambda day: day["tmax"])
    hot_label = {
        "понедельник": "в понедельник", "вторник": "во вторник",
        "среда": "в среду", "четверг": "в четверг", "пятница": "в пятницу",
        "суббота": "в субботу", "воскресенье": "в воскресенье",
    }.get(hottest.get("name"), "в самый жаркий день")
    if hottest["tmax"] >= 38:
        return (f"{hot_label.capitalize()} до {hottest['tmax']:+.0f}°C — избегай долгих "
                "прогулок и велосипеда днём, выходи утром или вечером")
    if hottest["tmax"] >= 32:
        return (f"{hot_label.capitalize()} до {hottest['tmax']:+.0f}°C — планируй "
                "активность на улице утром или вечером и возьми воду")
    if strong_wind and all(day in strong_wind for day in days[-2:]):
        return "В конце недели ожидается усиление ветра"
    if len(strong_wind) >= 3:
        return "Для велосипеда выбирай дни без сильного ветра"
    if len(rainy) >= 4:
        return "Для прогулок выбирай сухие окна между дождями"
    if rainy and len(outdoor) >= 2:
        best = sorted(outdoor, key=lambda day: (-day["tmax"], day["wind"]))[:2]
        labels = " и ".join(day["name"] for day in sorted(best, key=lambda day: day["index"]))
        return f"Лучшие дни для отдыха на улице — {labels}"
    if min(day["tmin"] for day in days) <= 12:
        return "Возьми лёгкую куртку — утром и вечером будет прохладно"
    if len(outdoor) >= 5:
        return "Можно спокойно планировать прогулки, велосипед и поездки"
    return "Сверяйся с прогнозом перед выходом: условия в течение недели будут меняться"
