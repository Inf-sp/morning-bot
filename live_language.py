"""Живой язык: ежедневная естественная фраза и примеры употребления."""

import random
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

import ai
import secure
import store
from ui import learning as learning_ui


def _code(language):
    if language in ("nl", "en"):
        return language
    return "nl" if language == "нидерландский" else "en"


def _language_for_code(code):
    return "английский" if code == "en" else "нидерландский"


def active_language(cid):
    code = store.get_learning_language(cid)
    return _language_for_code(code if code in ("nl", "en") else "nl")


def _flag(language):
    return "🇳🇱" if _code(language) == "nl" else "🇬🇧"


def _cap(value):
    value = str(value or "").strip()
    return value[:1].upper() + value[1:] if value else value


def _normalize_phrase_for_compare(text):
    return " ".join(re.findall(
        r"[\wÀ-ÖØ-öø-ÿ'-]+", str(text or "").lower(), re.UNICODE))
# ================= ГЛАГОЛ ДНЯ / ПОСЛОВИЦА =================
def _proverb_kb(code):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё вариант", callback_data=f"a_proverb_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_learn"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])

def _proverb_entities_card(flag, original, analogs=None, meaning="", examples=None, example_ru=""):
    msg = learning_ui.proverb_card(flag, original, analogs, meaning, examples, example_ru)
    return msg.text, msg.entities


_PROVERB_FALLBACKS = {
    "nl": [
        {
            "nl": "Dat is de druppel!",
            "en": "",
            "analogs": ["это последняя капля"],
            "type": "идиома",
            "meaning": "Когда мелкие неприятности копятся, и очередная мелочь окончательно добивает.",
            "example": "Eerst was mijn trein te laat, toen morste ik koffie over mijn shirt... En nu dit?! Dat is de druppel!",
            "example_ru": "Сначала поезд опоздал, потом я залил кофе рубашку... А теперь еще и это?! Ну всё, это последняя капля!",
        },
        {
            "nl": "Geen probleem.",
            "en": "",
            "analogs": ["без проблем"],
            "type": "разговорная фраза",
            "meaning": "Когда спокойно соглашаются помочь или показывают, что всё нормально.",
            "example": "Kun je me straks even bellen? Geen probleem.",
            "example_ru": "Можешь потом мне позвонить? Без проблем.",
        },
        {
            "nl": "Komt goed.",
            "en": "",
            "analogs": ["всё будет нормально"],
            "type": "разговорная фраза",
            "meaning": "Когда хотят коротко успокоить человека или показать, что вопрос решится.",
            "example": "Maak je geen zorgen, ik regel het morgen. Komt goed.",
            "example_ru": "Не переживай, завтра я всё улажу. Всё будет нормально.",
        },
        {
            "nl": "Doe maar rustig aan.",
            "en": "",
            "analogs": ["не торопись"],
            "type": "разговорная фраза",
            "meaning": "Когда человеку предлагают не спешить и действовать спокойнее.",
            "example": "Je hoeft niet te rennen. Doe maar rustig aan.",
            "example_ru": "Тебе не нужно бежать. Не торопись.",
        },
        {
            "nl": "Ik zie wel.",
            "en": "",
            "analogs": ["посмотрим"],
            "type": "разговорная фраза",
            "meaning": "Когда пока не принимают решение и оставляют всё открытым.",
            "example": "Misschien ga ik mee, maar ik zie wel.",
            "example_ru": "Может, я пойду с вами, но пока посмотрим.",
        },
        {
            "nl": "Laat maar.",
            "en": "",
            "analogs": ["забей"],
            "type": "разговорная фраза",
            "meaning": "Когда больше не хотят объяснять, спорить или продолжать тему.",
            "example": "Nee, het lukt niet meer. Laat maar.",
            "example_ru": "Нет, уже не получится. Забей.",
        },
        {
            "nl": "Het valt mee.",
            "en": "",
            "analogs": ["всё не так плохо"],
            "type": "разговорная фраза",
            "meaning": "Когда ситуация оказалась легче или приятнее, чем ожидалось.",
            "example": "Ik dacht dat het examen moeilijk zou zijn, maar het valt mee.",
            "example_ru": "Я думал, экзамен будет сложным, но всё оказалось не так плохо.",
        },
        {
            "nl": "Ik ben er klaar mee.",
            "en": "",
            "analogs": ["с меня хватит"],
            "type": "разговорная фраза",
            "meaning": "Когда человек устал от ситуации и больше не хочет с ней мириться.",
            "example": "Elke week hetzelfde gedoe. Ik ben er klaar mee.",
            "example_ru": "Каждую неделю одна и та же возня. С меня хватит.",
        },
        {
            "nl": "Dat komt goed uit.",
            "en": "",
            "analogs": ["это как раз кстати"],
            "type": "разговорная фраза",
            "meaning": "Когда что-то удобно совпало с планами или ситуацией.",
            "example": "Je bent morgen vrij? Dat komt goed uit.",
            "example_ru": "Ты завтра свободен? Это как раз кстати.",
        },
        {
            "nl": "Daar heb ik geen zin in.",
            "en": "",
            "analogs": ["мне совсем не хочется"],
            "type": "разговорная фраза",
            "meaning": "Когда прямо говорят, что нет желания что-то делать.",
            "example": "Nog een vergadering van twee uur? Daar heb ik geen zin in.",
            "example_ru": "Еще одно двухчасовое совещание? Мне совсем не хочется.",
        },
    ],
    "en": [
        {
            "nl": "",
            "en": "No worries.",
            "analogs": ["не переживай"],
            "type": "разговорная фраза",
            "meaning": "Когда хотят показать, что всё нормально и проблемы нет.",
            "example": "Sorry, I forgot to reply yesterday. No worries.",
            "example_ru": "Прости, я вчера забыл ответить. Не переживай.",
        },
        {
            "nl": "",
            "en": "That makes sense.",
            "analogs": ["логично"],
            "type": "разговорная фраза",
            "meaning": "Когда объяснение звучит понятно и разумно.",
            "example": "You took the earlier train to avoid the rain? That makes sense.",
            "example_ru": "Ты сел на поезд пораньше, чтобы не попасть под дождь? Логично.",
        },
        {
            "nl": "",
            "en": "I'm in.",
            "analogs": ["я с вами"],
            "type": "разговорная фраза",
            "meaning": "Когда человек соглашается участвовать в плане.",
            "example": "Pizza after work? I'm in.",
            "example_ru": "Пицца после работы? Я с вами.",
        },
        {
            "nl": "",
            "en": "Fair enough.",
            "analogs": ["справедливо"],
            "type": "разговорная фраза",
            "meaning": "Когда принимают чужой аргумент, даже если не спорят дальше.",
            "example": "I need more time before I decide. Fair enough.",
            "example_ru": "Мне нужно больше времени, прежде чем решить. Справедливо.",
        },
        {
            "nl": "",
            "en": "It slipped my mind.",
            "analogs": ["я совсем забыл"],
            "type": "разговорная фраза",
            "meaning": "Когда человек забыл что-то не специально.",
            "example": "I meant to call you back, but it slipped my mind.",
            "example_ru": "Я собирался тебе перезвонить, но совсем забыл.",
        },
        {
            "nl": "",
            "en": "Give me a sec.",
            "analogs": ["дай секунду"],
            "type": "разговорная фраза",
            "meaning": "Когда просят немного подождать.",
            "example": "Give me a sec, I'm just finding the address.",
            "example_ru": "Дай секунду, я как раз ищу адрес.",
        },
        {
            "nl": "",
            "en": "That was close.",
            "analogs": ["чуть не случилось"],
            "type": "разговорная фраза",
            "meaning": "Когда неприятность почти произошла, но её удалось избежать.",
            "example": "The cup almost fell off the table. That was close.",
            "example_ru": "Чашка почти упала со стола. Чуть не случилось.",
        },
        {
            "nl": "",
            "en": "I'm not feeling it.",
            "analogs": ["мне не заходит"],
            "type": "разговорная фраза",
            "meaning": "Когда что-то не нравится или не подходит по настроению.",
            "example": "Everyone likes this song, but I'm not feeling it.",
            "example_ru": "Всем нравится эта песня, но мне не заходит.",
        },
        {
            "nl": "",
            "en": "Let's call it a day.",
            "analogs": ["давай на сегодня закончим"],
            "type": "разговорная фраза",
            "meaning": "Когда предлагают закончить работу или дело на сегодня.",
            "example": "We've been fixing this for hours. Let's call it a day.",
            "example_ru": "Мы чиним это уже несколько часов. Давай на сегодня закончим.",
        },
        {
            "nl": "",
            "en": "I'm running late.",
            "analogs": ["я опаздываю"],
            "type": "разговорная фраза",
            "meaning": "Когда человек сообщает, что не успевает прийти вовремя.",
            "example": "I'm running late, but I'll be there in ten minutes.",
            "example_ru": "Я опаздываю, но буду через десять минут.",
        },
    ],
}


def _proverb_fallback(language):
    return dict(random.choice(_PROVERB_FALLBACKS[_code(language)]))


def _proverb_prompt(language):
    code = _code(language)
    target = "нидерландский" if code == "nl" else "английский"
    field = "nl" if code == "nl" else "en"
    other = "en" if code == "nl" else "nl"
    language_rule = (
        "Для Dutch выбирай выражения, которые реально звучат в Нидерландах. "
        if code == "nl"
        else "Для English выбирай живой разговорный аналог, а не буквальный перевод. "
    )
    return (
        "Ты эксперт по живой разговорной речи. "
        "Выдай одно естественное выражение для короткой карточки Telegram-бота. "
        "Это может быть идиома, фразовый глагол или частая разговорная фраза. "
        "Выражение должно реально использоваться в живой речи. "
        "Не придумывай кальки. Не используй редкие выражения. "
        "Русский перевод должен передавать смысл, а не буквальный перевод. "
        "Пример должен звучать как обычная жизненная ситуация. "
        "Пиши коротко. Без учебникового стиля. "
        f"Целевой язык карточки: {target}. Заполни поле {field}; поле {other} можно оставить пустым. "
        f"{language_rule}"
        "analogs[0] — главный русский перевод; другие варианты не нужны. "
        "meaning максимум 1 короткое предложение. "
        "example максимум 1-2 предложения. "
        "example_ru должен переводить смысл, а не слово в слово. "
        'JSON: {'
        '"nl":"NL expression or empty string",'
        '"en":"English expression or empty string",'
        '"analogs":["главный русский перевод"],'
        '"type":"идиома / разговорная фраза / фразовый глагол",'
        '"meaning":"когда так говорят, коротко по-русски",'
        '"example":"короткий пример на языке выражения",'
        '"example_ru":"естественный перевод примера на русский"'
        '}'
    )


def _split_proverb_example(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    value = str(value or "").strip()
    if "→" not in value:
        return value, ""
    example, example_ru = value.split("→", 1)
    return example.strip(), example_ru.strip()


def _proverb_normalized(raw, language):
    raw = raw if isinstance(raw, dict) else {}
    code = _code(language)
    original = str(raw.get(code) or raw.get("original") or "").strip()
    analogs = raw.get("analogs") or raw.get("literal") or raw.get("ru") or []
    if isinstance(analogs, str):
        analogs = [analogs]
    analogs = [str(x).strip() for x in analogs if str(x).strip()][:1]
    example, parsed_example_ru = _split_proverb_example(raw.get("example") or raw.get("examples") or "")
    return {
        "original": _cap(original),
        "analogs": analogs,
        "meaning": str(raw.get("meaning") or "").strip(),
        "example": example,
        "example_ru": str(raw.get("example_ru") or parsed_example_ru or "").strip(),
    }


def _plain_for_match(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(text or "").lower(), flags=re.UNICODE)).strip()


def _example_mentions_original(original, example):
    original_plain = _plain_for_match(original)
    example_plain = _plain_for_match(example)
    if not original_plain or not example_plain:
        return False
    if original_plain in example_plain:
        return True
    tokens = [token for token in original_plain.split() if len(token) > 2]
    return bool(tokens) and all(token in example_plain.split() for token in tokens)


def _valid_proverb(data):
    return (
        bool(data.get("original"))
        and bool(data.get("analogs"))
        and bool(data.get("example"))
        and _example_mentions_original(data.get("original"), data.get("example"))
        and len(data.get("meaning") or "") <= 160
        and len(data.get("example") or "") <= 240
        and len(data.get("example_ru") or "") <= 240
    )


async def _generate_proverb(language):
    try:
        raw = await ai.allm_json(_proverb_prompt(language), 500, tier="cheap", route="gemini", module="learning")
    except Exception:
        raw = _proverb_fallback(language)
    data = _proverb_normalized(raw, language)
    if not _valid_proverb(data):
        data = _proverb_normalized(_proverb_fallback(language), language)
    return data


async def send_proverb(bot, cid, language=None, with_kb=True):
    language = language or active_language(cid)
    data = await _generate_proverb(language)
    txt, entities = _proverb_entities_card(
        _flag(language),
        data["original"],
        data["analogs"],
        _cap(data["meaning"]),
        data["example"],
        data["example_ru"],
    )
    reply_markup = _proverb_kb(_code(language)) if with_kb else None
    await bot.send_message(chat_id=cid, text=txt, entities=entities, reply_markup=reply_markup)


async def send_proverb_both(bot, cid, with_kb=True, language=None):
    """Compatibility wrapper: live language uses the single active learning language."""
    await send_proverb(bot, cid, language or active_language(cid), with_kb=with_kb)
