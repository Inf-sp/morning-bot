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
    """Короткий текст недели: «Часто дождь, временами ветрено» — без значка и температур."""
    wet = sum(day["rain_real"] for day in days)
    clear = sum(day["code"] in (0, 1) and not day["rain_real"] for day in days)
    cloudy = sum(day["code"] in (3, 45, 48) for day in days)
    snow = sum(day["code"] in SNOW_CODES for day in days)
    max_wind = max(day["wind"] for day in days)
    avg_wind = sum(day["wind"] for day in days) / len(days)

    if snow:
        description = "Временами снег"
    elif wet >= 4:
        description = "Часто дождь"
    elif wet >= 2:
        description = "Переменная облачность, временами дождь"
    elif clear >= 5:
        description = "В основном ясно"
    elif clear >= 3:
        description = "В основном малооблачно"
    elif cloudy >= 4:
        description = "В основном облачно"
    else:
        description = "Переменная облачность"

    if max_wind >= 11:
        description += ", сильный ветер"
    elif max_wind >= 8:
        description += ", временами ветрено"
    elif avg_wind >= 5:
        description += ", умеренный ветер"
    return description


