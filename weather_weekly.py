"""Pure helpers for compact weekly weather summaries."""

SNOW_CODES = (71, 73, 75, 77, 85, 86)


def _snowy(day):
    # Суточный код «снег» без реальных осадков — не снег.
    return day.get("code") in SNOW_CODES and bool(day.get("rain_real"))


def _sky(day):
    """Небо дня: из почасовой облачности (`sky`), иначе по коду погоды."""
    if day.get("sky"):
        return day["sky"]
    code = day.get("code")
    return "sunny" if code in (0, 1) else "cloudy" if code in (3, 45, 48) else "partly"


def qualitative_outlook(days, period="На следующей неделе"):
    """Describe expected weather in words, without exposing numeric values."""
    rainy = sum(bool(day.get("rain_real")) for day in days)
    snowy = sum(_snowy(day) for day in days)
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
    clear = sum(_sky(day) == "sunny" and not day["rain_real"] for day in days)
    cloudy = sum(_sky(day) == "cloudy" and not day["rain_real"] for day in days)
    snow = sum(_snowy(day) for day in days)
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
    return description + "…"




# ---------- «Неделя»: короткая история недели без AI ----------
_TO = {"понедельник": "к понедельнику", "вторник": "ко вторнику", "среда": "к среде", "четверг": "к четвергу",
       "пятница": "к пятнице", "суббота": "к субботе", "воскресенье": "к воскресенью"}
WINDY_MS = 10  # м/с: у дня пометка «ветер»
TREND_DEGREES = 3  # °C: разница начала и конца недели — «теплеет / холодает»


def _deg(value) -> str:
    value = round(float(value))
    return "0°" if value == 0 else f"{value:+d}°"


def day_note(day) -> str:
    """Пометка дня — только когда есть что сказать: «дождь 80%», «ветер 12 м/с»."""
    notes = []
    if day.get("rain_real"):
        notes.append(f"{'снег' if _snowy(day) else 'дождь'} {float(day.get('rain') or 0):.0f}%")
    if float(day.get("wind") or 0) >= WINDY_MS:
        notes.append(f"ветер {float(day['wind']):.0f} м/с")
    return " · ".join(notes)


def week_story(days) -> tuple[str, str]:
    """(вступление, итог): «К четвергу теплеет: с +1° до +8°. Дождь — в вс и ср.» и «Лучший день — пятница:
    солнечно и сухо. Выходные: сб сухо, вс дождь.»; без AI, по тем же дням, что строки недели."""
    if not days:
        return "", ""
    temps = [float(day["tmax"]) for day in days]
    start, end = sum(temps[:2]) / len(temps[:2]), sum(temps[-2:]) / len(temps[-2:])
    peak = max(range(len(days)), key=lambda index: temps[index])
    if end - start >= TREND_DEGREES:
        trend = f"Теплеет: с {_deg(temps[0])} до {_deg(temps[peak])} {_TO.get(days[peak]['name'], '')}".strip()
    elif start - end >= TREND_DEGREES:
        low = min(range(len(days)), key=lambda index: temps[index])
        trend = f"Холодает: с {_deg(temps[0])} до {_deg(temps[low])} {_TO.get(days[low]['name'], '')}".strip()
    else:
        low_t, high_t = _deg(min(temps)), _deg(max(temps))
        trend = f"Ровно: около {high_t}" if low_t == high_t else f"Ровно: от {low_t} до {high_t}"
    wet = [day for day in days if day.get("rain_real")]
    # Дни с дождём видны пометками в строках — во вступлении только итог недели, если он есть.
    rain = ("Без дождя всю неделю." if not wet else "Дождь почти каждый день." if len(wet) >= len(days) - 1 else "")
    intro = " ".join(part for part in (f"{trend}.", rain) if part)

    def score(day):  # сухо, солнце, тише ветер, теплее
        sky = {"sunny": 0, "partly": 1}.get(_sky(day), 2)
        return (bool(day.get("rain_real")), sky, float(day.get("wind") or 0) >= WINDY_MS, -float(day["tmax"]))

    best = min(days, key=score)
    sky_word = {"sunny": "солнечно", "partly": "переменная облачность"}.get(_sky(best), "облачно")
    parts = []
    if not best.get("rain_real"):
        parts.append(f"Лучший день — {best['name']}: {sky_word}, без дождя, {_deg(best['tmax'])}.")
    weekend = [day for day in days if str(day["abbrev"]).casefold() in ("сб", "вс")]
    if weekend:
        parts.append("Выходные: " + ", ".join(
            f"{str(day['abbrev']).lower()} {'дождь' if day.get('rain_real') else 'сухо'}" for day in weekend) + ".")
    return intro, " ".join(parts)
