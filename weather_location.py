"""City search and Telegram location handlers extracted from weather controller."""


async def set_city_text(bot, cid, name, show_brief=True):
    import re as _re
    raw = (name or "").strip()
    q = _re.sub(r"\s*-\s*", "-", raw)
    q = _re.sub(r"\s+", " ", q)
    variants = []
    for value in (q, q.replace("-", " "), raw):
        value = value.strip()
        if value and value not in variants:
            variants.append(value)
    try:
        official = await ai.allm(
            f"Какое официальное название у города «{raw}»? "
            "Если это прозвище или сокращение (Питер, Нью-Йорк, Первопрестольная…), "
            "верни официальное название. Если уже официальное — верни как есть. "
            "Только название, без пояснений.",
            40, 0.1, tier="cheap", route="gemini",
        )
        official = official.strip().strip("«»\"'.").split("\n")[0].strip()
        if official and official.lower() not in {value.lower() for value in variants}:
            variants.insert(0, official)
    except Exception:
        pass
    try:
        result = None
        for value in variants:
            if not config.WEATHER_API_KEY:
                break
            try:
                response = await asyncio.to_thread(
                    requests.get,
                    "https://api.openweathermap.org/geo/1.0/direct",
                    params={"q": value, "limit": 5, "appid": config.WEATHER_API_KEY},
                    timeout=20,
                )
                rows = response.json()
            except Exception:
                rows = []
            if rows:
                item = rows[0]
                country_code = (item.get("country") or "").upper()
                result = [{
                    "latitude": float(item["lat"]),
                    "longitude": float(item["lon"]),
                    "name": item.get("local_names", {}).get("ru") or item.get("name") or value,
                    "country": item.get("country") or "",
                    "country_code": country_code.lower(),
                }]
                break
        if not result:
            for value in variants:
                try:
                    response = await asyncio.to_thread(
                        requests.get,
                        "https://nominatim.openstreetmap.org/search",
                        params={
                            "q": value, "format": "json", "limit": 1,
                            "accept-language": "ru",
                        },
                        headers={"User-Agent": "DM-bot"}, timeout=20,
                    )
                    rows = response.json()
                except Exception:
                    rows = []
                if rows:
                    item = rows[0]
                    display = item.get("display_name", value).split(",")
                    result = [{
                        "latitude": float(item["lat"]),
                        "longitude": float(item["lon"]),
                        "name": display[0].strip(),
                        "country": display[-1].strip(),
                        "country_code": "",
                    }]
                    break
        if not result:
            store.pending_input[str(cid)] = "setcity"
            msg = weather_ui.city_not_found(raw)
            await bot.send_message(chat_id=cid, text=msg.text)
            return
        city = result[0]
        country = city.get("country", "")
        country_code = city.get("country_code", "")
        city_name = city["name"]
        try:
            hint = f" ({country})" if country else ""
            translated = await ai.allm(
                f"Как правильно пишется название города «{city_name}»{hint} на русском языке, "
                "как в Википедии? Ответь ТОЛЬКО названием города, без пояснений.",
                40, 0.1, tier="cheap", route="gemini",
            )
            translated = translated.strip().strip("«»\"'.").split("\n")[0].strip()
            if translated and len(translated) <= 80 and not any(char.isdigit() for char in translated):
                city_name = translated
        except Exception:
            pass
        store.set_settings(
            cid, city["latitude"], city["longitude"], city_name, country, country_code,
        )
        try:
            import myday
            myday.reset_day_cache(cid)
        except Exception:
            pass
        msg = weather_ui.city_changed(city_name, country, country_code)
        await bot.send_message(chat_id=cid, text=msg.text)
        if show_brief:
            try:
                import myday
                await myday.send_plany(bot, cid)
            except Exception:
                pass
    except Exception as error:
        await verify.safe_error(bot, cid, error, back="m_myday")


async def location_handler(update, context):
    cid = update.effective_chat.id
    location = update.message.location
    city, country = "твой город", ""
    try:
        response = await asyncio.to_thread(
            requests.get,
            "https://api.bigdatacloud.net/data/reverse-geocode-client",
            params={
                "latitude": location.latitude, "longitude": location.longitude,
                "localityLanguage": "ru",
            },
            timeout=15,
        )
        payload = response.json()
        city = (
            payload.get("city") or payload.get("locality")
            or payload.get("principalSubdivision") or "твой город"
        )
        country = payload.get("countryName", "")
        country_code = payload.get("countryCode", "")
    except Exception as error:
        _log.warning("location_handler: reverse geocode failed: %s", error)
        country_code = ""
    store.set_settings(
        cid, location.latitude, location.longitude, city, country, country_code,
    )
    try:
        import myday
        myday.reset_day_cache(cid)
    except Exception:
        pass
    msg = weather_ui.location_changed(city, country, country_code)
    await update.message.reply_text(msg.text)
    try:
        await send_weather(context.bot, cid, "today")
    except Exception:
        pass
