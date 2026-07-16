from datetime import datetime
import hashlib
import json
import logging
import re

import ai
import api_usage
import config
import secure
import store
from recipe_state import _leftover_recent
from fridge_model import _fridge_cat, _fridge_clean_name, _fridge_migrate
from ui.constants import CUISINE_EMOJI

TZ = config.TZ
_log = logging.getLogger(__name__)


_HOME_MEAL_LABELS = {
    "breakfast": "завтрак",
    "lunch": "обед",
    "dinner": "ужин",
}

_HOME_NATURAL_INGREDIENTS = {
    "соус сои": "соевый соус",
    "соус из сои": "соевый соус",
    "соус соевый": "соевый соус",
    "масло оливковое": "оливковое масло",
    "масло сливочное": "сливочное масло",
    "масло подсолнечное": "подсолнечное масло",
    "молоко кокосовое": "кокосовое молоко",
    "молоко овсяное": "овсяное молоко",
    "сыр твердый": "твёрдый сыр",
    "сыр твёрдый": "твёрдый сыр",
    "твердый сыр": "твёрдый сыр",
    "сливки открытые": "открытые сливки",
    "паста томатная": "томатная паста",
    "перец болгарский": "болгарский перец",
    "лук репчатый": "репчатый лук",
    "сыр пармезан": "пармезан",
}

_HOME_SYNONYM_GROUPS = (
    ("соевый соус", "соус сои", "соус из сои"),
    ("цукини", "кабачок", "кабачки"),
    ("помидор", "помидоры", "томат", "томаты"),
    ("нут", "турецкий горох"),
    ("батат", "сладкий картофель"),
    ("баклажан", "синенький", "синенькие"),
    ("кинза", "кориандр"),
    ("рукола", "руккола"),
    ("пармезан", "сыр пармезан"),
)

_HOME_CATEGORY_PAIRS = {
    frozenset(("мясо и рыба", "крупы и макароны")),
    frozenset(("молочное и яйца", "крупы и макароны")),
    frozenset(("молочное и яйца", "специи и соусы")),
    frozenset(("крупы и макароны", "хлеб и выпечка")),
    frozenset(("овощи", "специи и соусы")),
    frozenset(("фрукты", "специи и соусы")),
}

_HOME_TECHNICAL_REASON_RE = re.compile(
    r"(?:поскольку|так как).{0,35}холодильник|"
    r"(?:на основе|исходя из).{0,35}(?:содержим|продукт)|"
    r"содержим.{0,20}холодильник|данн.{0,20}холодильник|"
    r"в холодильнике (?:есть|имеется|находится)|"
    r"(?:рецепт|блюдо) (?:выбран|подобран).{0,25}(?:продукт|ингредиент)|"
    r"доступн.{0,15}(?:ингредиент|продукт)|"
    r"(?:учитывая|с уч[её]том).{0,25}(?:холодильник|продукт|содержим)|"
    r"оптимальн.{0,15}выбор",
    re.IGNORECASE,
)

_HOME_GENERIC_TIP_RE = re.compile(
    r"используй(?:те)? свежие продукты|"
    r"не бой(?:ся|тесь) экспериментировать|"
    r"добав(?:ь|ьте).{0,25}(?:чеснок.{0,10}лук|лук.{0,10}чеснок).{0,25}(?:аромат|вкус)|"
    r"добав(?:ь|ьте) .* по вкусу|"
    r"готовь(?:те)? с любовью",
    re.IGNORECASE,
)

_HOME_FORMAL_TO_INFORMAL = {
    "приготовьте": "приготовь",
    "обжарьте": "обжарь",
    "добавьте": "добавь",
    "используйте": "используй",
    "оставьте": "оставь",
    "снимите": "сними",
    "перемешайте": "перемешай",
    "нарежьте": "нарежь",
    "разогрейте": "разогрей",
    "подавайте": "подавай",
    "варите": "вари",
    "готовьте": "готовь",
    "влейте": "влей",
    "дайте": "дай",
    "смешайте": "смешай",
    "посолите": "посоли",
    "измельчите": "измельчи",
    "запекайте": "запекай",
    "жарьте": "жарь",
    "накройте": "накрой",
    "держите": "держи",
    "выложите": "выложи",
    "переверните": "переверни",
    "доведите": "доведи",
    "убавьте": "убавь",
    "промойте": "промой",
    "замочите": "замочи",
    "взбейте": "взбей",
    "натрите": "натри",
}

_HOME_FORMAL_IMPERATIVE_RE = re.compile(r"\b[а-яё]+(?:йте|ите)\b", re.IGNORECASE)


def _home_meal_for_hour(hour: int) -> str:
    """Тип блюда для главного экрана по локальному времени пользователя/бота."""
    if 5 <= hour < 12:
        return "breakfast"
    if 12 <= hour < 18:
        return "lunch"
    return "dinner"


def _home_string_list(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = re.split(r"[,;\n]+", value)
    else:
        return []
    result = []
    seen = set()
    for item in raw:
        text = " ".join(str(item or "").split()).strip(" -•")
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _home_natural_ingredient(value) -> str:
    text = " ".join(str(value or "").lower().replace("ё", "е").split()).strip(" -•.,")
    natural = _HOME_NATURAL_INGREDIENTS.get(text, text)
    # Возвращаем принятую в модели холодильника форму, затем ещё раз исправляем
    # порядок слов: clean_name знает больше пользовательских вариантов.
    natural = _fridge_clean_name(natural) or natural
    return _HOME_NATURAL_INGREDIENTS.get(natural.replace("ё", "е"), natural)


def _home_semantic_ingredient_key(value) -> str:
    natural = _home_natural_ingredient(value).replace("ё", "е")
    for index, group in enumerate(_HOME_SYNONYM_GROUPS):
        normalized_group = {_home_natural_ingredient(item).replace("ё", "е") for item in group}
        if natural in normalized_group:
            return f"synonym:{index}"
    return natural


def _home_normalize_voice(value) -> str:
    text = str(value or "")
    for formal, informal in _HOME_FORMAL_TO_INFORMAL.items():
        text = re.sub(
            rf"\b{formal}\b",
            lambda match: informal.capitalize() if match.group(0)[:1].isupper() else informal,
            text,
            flags=re.IGNORECASE,
        )
    return text


def _home_human_reason(value, context: dict) -> str:
    reason = _home_one_sentence(_home_normalize_voice(value))
    if (reason and not _HOME_TECHNICAL_REASON_RE.search(reason)
            and not _HOME_FORMAL_IMPERATIVE_RE.search(reason)):
        return reason
    meal = _HOME_MEAL_LABELS.get(context.get("meal"), "приём пищи")
    if context.get("available"):
        return f"Быстрый {meal} из того, что уже есть дома"
    return f"Простой {meal} без лишних сложностей"


def _home_useful_tip(value) -> str:
    tip = _home_one_sentence(_home_normalize_voice(value))
    return "" if (_HOME_GENERIC_TIP_RE.search(tip)
                  or _HOME_FORMAL_IMPERATIVE_RE.search(tip)) else tip


def _home_clean_substitution(value) -> str:
    text = re.sub(
        r"^(?:подойд[её]т|можно использовать|используй|возьми|замени(?:ть)? на)\s+",
        "",
        str(value or "").strip(),
        flags=re.IGNORECASE,
    )
    return _home_natural_ingredient(text)


def _home_valid_substitution(source, replacement) -> bool:
    """Отсекает тот же продукт под другим именем и явно несовместимые категории."""
    source = _home_natural_ingredient(source)
    replacement = _home_clean_substitution(replacement)
    if not source or not replacement:
        return False
    if _home_semantic_ingredient_key(source) == _home_semantic_ingredient_key(replacement):
        return False
    source_cat = _fridge_cat(source)
    replacement_cat = _fridge_cat(replacement)
    if source_cat == replacement_cat and source_cat != "прочее":
        return True
    if "прочее" in (source_cat, replacement_cat):
        return False
    return frozenset((source_cat, replacement_cat)) in _HOME_CATEGORY_PAIRS


def _home_exact_fridge_names(values, available) -> list[str]:
    """Не даёт модели приписать холодильнику продукт, которого там нет."""
    by_name = {
        _home_semantic_ingredient_key(name): _home_natural_ingredient(name)
        for name in available
    }
    result = []
    for value in _home_string_list(values):
        actual = by_name.get(_home_semantic_ingredient_key(value))
        if actual and actual not in result:
            result.append(actual)
    return result


def _home_minutes(value) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return None
    minutes = int(match.group())
    return minutes if 1 <= minutes <= 360 else None


def _home_one_sentence(value) -> str:
    text = " ".join(str(value or "").split()).strip()
    return re.split(r"(?<=[.!?…])\s+", text, maxsplit=1)[0] if text else ""


def _home_steps(value) -> list[dict]:
    """Компактные шаги полного рецепта в едином обращении на «ты»."""
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:6]:
        structured = isinstance(item, dict)
        if structured:
            text = item.get("text")
            minutes = _home_minutes(item.get("minutes"))
        else:
            text = item
            minutes = None
        text = " ".join(_home_normalize_voice(text).split()).strip(" -•")
        has_time = bool(re.search(r"\d+(?:\s*[–-]\s*\d+)?\s*(?:мин|минут)", text, re.I))
        text = re.sub(
            r"\s*(?:—|-|,)?\s*\d+(?:\s*[–-]\s*\d+)?\s*мин(?:ут(?:а|ы|у)?|\.)?",
            "",
            text,
            flags=re.I,
        ).strip(" -—,.;")
        sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", text) if part.strip()]
        text = " ".join(sentences[:2]) if sentences else text
        if not structured and not has_time:
            minutes = 2
        if not text or _HOME_FORMAL_IMPERATIVE_RE.search(text) or not (minutes or has_time):
            continue
        result.append({"text": text, "minutes": minutes})
    while len(result) > 4:
        pair_index = min(
            range(len(result) - 1),
            key=lambda index: len((result[index]["text"] + " " + result[index + 1]["text"]).split()),
        )
        first, second = result[pair_index:pair_index + 2]
        result[pair_index:pair_index + 2] = [{
            "text": f"{first['text'].rstrip('.!?…')}. {second['text']}",
            "minutes": (first.get("minutes") or 0) + (second.get("minutes") or 0) or None,
        }]
    if len(result) == 4:
        pairs = [
            (len((result[index]["text"] + " " + result[index + 1]["text"]).split()), index)
            for index in range(3)
        ]
        word_count, pair_index = min(pairs)
        if word_count <= 24:
            first, second = result[pair_index:pair_index + 2]
            result[pair_index:pair_index + 2] = [{
                "text": f"{first['text'].rstrip('.!?…')}. {second['text']}",
                "minutes": (first.get("minutes") or 0) + (second.get("minutes") or 0) or None,
            }]
    return result if 2 <= len(result) <= 4 else []


def _normalize_home_idea(data, context: dict) -> dict:
    """Нормализует AI-ответ и структурно скрывает недостоверные блоки."""
    data = data if isinstance(data, dict) else {}
    available = context.get("available") or []
    has_fridge = bool(context.get("has_fridge"))

    name = " ".join(str(data.get("name") or "").split()).strip()
    reason = _home_human_reason(data.get("reason"), context)
    tip = _home_useful_tip(data.get("tip"))
    ingredients = [_home_natural_ingredient(item) for item in _home_string_list(data.get("ingredients"))]
    steps = _home_steps(data.get("steps"))
    ingredient_keys = {_home_semantic_ingredient_key(item) for item in ingredients}
    use_first = _home_exact_fridge_names(data.get("use_first"), available) if has_fridge else []
    use_first = [item for item in use_first if _home_semantic_ingredient_key(item) in ingredient_keys]

    missing = ([_home_natural_ingredient(item) for item in _home_string_list(data.get("missing"))]
               if has_fridge else [])
    available_keys = {_home_semantic_ingredient_key(name) for name in available}
    missing = [
        item for item in missing
        if (_home_semantic_ingredient_key(item) in ingredient_keys
            and _home_semantic_ingredient_key(item) not in available_keys)
    ][:3]

    substitution = data.get("substitution") if isinstance(data.get("substitution"), dict) else {}
    substitution_for = _home_natural_ingredient(substitution.get("for"))
    substitution_product = _home_clean_substitution(substitution.get("product"))
    substitution_from_fridge = bool(substitution.get("from_fridge"))
    missing_keys = {_home_semantic_ingredient_key(item) for item in missing}
    if (not missing
            or _home_semantic_ingredient_key(substitution_for) not in missing_keys
            or not _home_valid_substitution(substitution_for, substitution_product)):
        substitution = None
    elif substitution_from_fridge:
        exact = _home_exact_fridge_names([substitution_product], available)
        substitution = {
            "for": substitution_for,
            "product": exact[0],
        } if exact else None
    elif substitution_product:
        substitution = {"for": substitution_for, "product": substitution_product}
    else:
        substitution = None

    return {
        "reason": reason,
        "name": name,
        "minutes": _home_minutes(data.get("minutes")),
        "ingredients": ingredients,
        "steps": steps,
        "use_first": use_first,
        "missing": missing,
        "substitution": substitution,
        "tip": tip,
    }


def _home_idea_context(cid, now=None) -> dict:
    now = now or datetime.now(TZ)
    raw_fridge = store.get_list(config.FRIDGE_KEY, str(cid))
    fridge = _fridge_migrate(raw_fridge)
    available = [item["name"] for item in fridge if item.get("on", True)]
    unavailable = [item["name"] for item in fridge if not item.get("on", True)]
    profile = store.get_profile(cid)
    diet_prefs = " ".join(str(profile.get("diet_prefs") or "").split())
    raw_memory_prefs = profile.get("prefs") or []
    if not isinstance(raw_memory_prefs, list):
        raw_memory_prefs = [raw_memory_prefs]
    memory_prefs = [str(item).strip() for item in raw_memory_prefs if str(item).strip()][:20]
    meal = _home_meal_for_hour(now.hour)
    signature_data = {
        "date": now.date().isoformat(),
        "home_copy_version": 4,
        "meal": meal,
        "available": available,
        "unavailable": unavailable,
        "diet_prefs": diet_prefs,
        "memory_prefs": memory_prefs,
        "cuisines": _cuisine_context(cid),
    }
    signature = hashlib.sha256(
        json.dumps(signature_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        **signature_data,
        "has_fridge": bool(fridge),
        "signature": signature,
    }


def _home_idea_prompt(context: dict) -> str:
    meal = _HOME_MEAL_LABELS[context["meal"]]
    available = context.get("available") or []
    unavailable = context.get("unavailable") or []
    if context.get("has_fridge"):
        fridge_context = (
            "Данные холодильника — единственный источник истины о наличии продуктов.\n"
            f"В наличии: {secure.wrap_untrusted(', '.join(available) or 'ничего', 'продукты в наличии')}.\n"
            f"Закончились: {secure.wrap_untrusted(', '.join(unavailable) or 'нет отметок', 'закончившиеся продукты')}.\n"
        )
    else:
        fridge_context = (
            "Данных о холодильнике нет. Не утверждай, что продукты уже есть дома, "
            "не заполняй use_first, missing и substitution.\n"
        )
    restrictions = context.get("diet_prefs") or "не указаны"
    memory_prefs = "; ".join(context.get("memory_prefs") or []) or "не указаны"
    cuisines = context.get("cuisines") or "не указаны"
    return (
        f"Сейчас нужен {meal}. Составь одну короткую идею полноценного блюда на сегодня.\n"
        f"{fridge_context}"
        f"Пищевые предпочтения, аллергии и ограничения: {secure.wrap_untrusted(restrictions, 'ограничения')}.\n"
        f"Другие сохранённые факты пользователя: {secure.wrap_untrusted(memory_prefs, 'предпочтения')}.\n"
        f"Предпочтительные кухни: {secure.wrap_untrusted(cuisines, 'кухни')}.\n"
        "Правила:\n"
        "• Предложи ровно одно понятное блюдо, без рекламного названия.\n"
        "• Все названия ингредиентов должны звучать естественно по-русски: «соевый соус», а не «соус сои».\n"
        "• Никогда не используй и не предлагай заменой исключённые продукты или аллергены.\n"
        "• Если список «В наличии» не пуст, используй только эти продукты, воду и базовые масло, соль и перец. "
        "Не добавляй другие покупки.\n"
        "• ingredients — полный список всего, что используется в блюде, включая масло, соль и перец, если они нужны. "
        "Для продуктов из холодильника сохраняй точное написание из входного списка.\n"
        "• steps — обычно ровно 3 коротких шага; для очень простого блюда можно 2, для сложного максимум 4. "
        "Объединяй логически связанные действия. Каждый шаг начинается с глагола на «ты», содержит не больше "
        "1–2 коротких предложений и не повторяет ингредиенты без необходимости. В text не пиши время шага; "
        "minutes оставь только отдельным техническим полем, сумма равна общему minutes.\n"
        "• use_first — только точные названия из списка «В наличии», особенно открытые, скоропортящиеся "
        "или явно требующие скорого использования, и только если они входят в ingredients. "
        "Не выдумывай срочность и не добавляй отсутствующие продукты.\n"
        "• use_first, missing и substitution больше не показываются: верни пустые значения.\n"
        "• reason — одна короткая человеческая рекомендация вроде «Быстрый ужин из того, что уже есть дома». "
        "Не пиши «поскольку в холодильнике есть», «на основе содержимого холодильника» и другие технические объяснения. "
        "При пустом холодильнике формулировка нейтральная.\n"
        "• tip — один конкретный приём именно для этого блюда с понятной техникой или результатом. "
        "Запрещены общие советы вроде «добавь чеснок и лук для аромата».\n"
        "• Во всём тексте обращайся только на «ты»: «приготовь», «обжарь», «добавь». "
        "Не используй формы «приготовьте», «обжарьте», «добавьте».\n"
        "• Никаких эмодзи, общих вступлений, текста о настройках и нескольких советов.\n"
        'JSON без markdown: {"reason":"одно предложение","name":"Название блюда","minutes":20,'
        '"ingredients":["все продукты блюда"],'
        '"steps":[{"text":"Нарежь начинку","minutes":3},{"text":"Взбей яйца и вылей на сковороду","minutes":4},{"text":"Добавь начинку и сложи омлет","minutes":5}],'
        '"use_first":[],"missing":[],"substitution":null,'
        '"tip":"один совет"}. Если блока нет, используй пустой массив или null.'
    )


def _home_local_idea(context: dict) -> dict:
    local = _fallback_leftovers_recipe(", ".join(context.get("available") or []))
    if not local:
        return {}
    return _normalize_home_idea({
        "name": local.get("name"),
        "minutes": local.get("time"),
        "ingredients": local.get("ingredients"),
        "steps": local.get("steps"),
        "tip": local.get("chef_tip"),
    }, context)


def get_cached_cooking_home_idea(cid, now=None) -> dict | None:
    """Возвращает готовый рецепт без AI и без побочных эффектов.

    Кэш раздельный для завтрака, обеда и ужина. Подпись включает дату,
    холодильник и предпочтения, поэтому устаревший рецепт не показывается.
    """
    context = _home_idea_context(cid, now=now)
    profile = store.get_profile(cid)
    entries = profile.get("cooking_home_ideas") or {}
    cached = entries.get(context["meal"]) if isinstance(entries, dict) else None
    # Совместимость с кэшем до разделения по приёмам пищи.
    if not isinstance(cached, dict):
        legacy = profile.get("cooking_home_idea")
        if isinstance(legacy, dict) and legacy.get("signature") == context["signature"]:
            cached = legacy
    if not isinstance(cached, dict) or cached.get("signature") != context["signature"]:
        return None
    idea = cached.get("idea")
    if not isinstance(idea, dict):
        return None
    normalized = _normalize_home_idea(idea, context)
    required = ("name", "reason", "minutes", "ingredients", "steps", "tip")
    return normalized if all(normalized.get(field) for field in required) else None


def get_cooking_home_idea(cid, now=None, refresh=False) -> dict:
    """Одна стабильная идея для текущего приёма пищи и актуального холодильника."""
    context = _home_idea_context(cid, now=now)
    profile = store.get_profile(cid)
    entries = profile.get("cooking_home_ideas") or {}
    cached = entries.get(context["meal"]) if isinstance(entries, dict) else None
    if not isinstance(cached, dict):
        legacy = profile.get("cooking_home_idea")
        if isinstance(legacy, dict) and legacy.get("signature") == context["signature"]:
            cached = legacy
    previous_name = ""
    if isinstance(cached, dict):
        previous_name = str((cached.get("idea") or {}).get("name") or "")
    if not refresh:
        ready = get_cached_cooking_home_idea(cid, now=now)
        if ready is not None:
            return ready

    if api_usage.gemini_state(1).get("cooldown_active"):
        local = _home_local_idea(context)
        if all(local.get(field) for field in ("name", "reason", "minutes", "ingredients", "steps", "tip")):
            return local

    prompt = _home_idea_prompt(context)
    if refresh and previous_name:
        prompt += f"\nНе повторяй блюдо: {secure.wrap_untrusted(previous_name, 'предыдущий рецепт')}."
    idea = {}
    for attempt in range(2):
        try:
            result = ai.llm_json(
                prompt, 1100, tier="cheap", module="food",
                fallback_allowed=True, privacy_level="personal", allow_personal_openrouter=True,
            )
        except Exception:
            result = {}
        idea = _normalize_home_idea(result, context)
        complete = all(idea.get(field) for field in ("name", "reason", "minutes", "ingredients", "steps", "tip"))
        repeated = bool(
            refresh and previous_name and idea.get("name", "").casefold() == previous_name.casefold()
        )
        if complete and not repeated:
            break
        if api_usage.gemini_state(1).get("cooldown_active"):
            local = _home_local_idea(context)
            if all(local.get(field) for field in ("name", "reason", "minutes", "ingredients", "steps", "tip")):
                idea = local
                break
        if attempt == 0:
            prompt += (
                "\nПредыдущий вариант не прошёл проверку. Верни новый вариант: обязательны естественные "
                "русские названия, 2–3 коротких шага без времени в тексте и один конкретный совет на «ты»."
            )
    if refresh and previous_name and idea.get("name", "").casefold() == previous_name.casefold():
        idea = {}
    if not all(idea.get(field) for field in ("name", "reason", "minutes", "ingredients", "steps", "tip")):
        idea = _home_local_idea(context)
    if not all(idea.get(field) for field in ("name", "reason", "minutes", "ingredients", "steps", "tip")):
        raise ValueError("Неполный рецепт для главного экрана Готовки")
    # За время AI-запроса профиль мог измениться в другом сценарии. Перечитываем его,
    # чтобы запись кэша не затёрла новые предпочтения или другие пользовательские данные.
    profile = store.get_profile(cid)
    entries = profile.get("cooking_home_ideas")
    if not isinstance(entries, dict):
        entries = {}
    entries[context["meal"]] = {"signature": context["signature"], "idea": idea}
    profile["cooking_home_ideas"] = entries
    # Старое поле оставляем как последний использованный рецепт для совместимости.
    profile["cooking_home_idea"] = {"signature": context["signature"], "idea": idea}
    store.set_profile(cid, profile)
    return idea


def warm_cooking_home_ideas(cid, now=None) -> dict:
    """Готовит три главных рецепта дня для фонового прогрева в 08:00."""
    base = now or datetime.now(TZ)
    results = {}
    for meal, hour in (("breakfast", 8), ("lunch", 13), ("dinner", 19)):
        meal_now = base.replace(hour=hour, minute=0, second=0, microsecond=0)
        try:
            idea = get_cooking_home_idea(cid, now=meal_now, refresh=False)
            results[meal] = bool(idea)
        except Exception as error:
            _log.warning("cooking home warm failed cid=%s meal=%s: %r", cid, meal, error)
            results[meal] = False
    return results


def _cuisine_context(cid):
    # settings импортирует cooking для обратной совместимости старых callback-ов;
    # ленивый импорт не создаёт цикл при загрузке генератора рецептов.
    import settings
    return settings.cuisine_context(cid)

def _my_recipe_pref(cid):
    """Контекст из базы рецептов для промпта (первые 5 названий)."""
    if not cid:
        return ""
    saved = store.get_list(config.MY_RECIPES_KEY, str(cid))[:5]
    names = ", ".join(r.get("name", "") for r in saved if r.get("name"))
    return f"Пользователь любит готовить: {names}. Похожий стиль приветствуется.\n" if names else ""


def _gen_recipe(constraint, cid=None):
    pref = _my_recipe_pref(cid)
    context = _cuisine_context(cid) if cid else ""
    cz = (context + "\n") if context else ""
    avoid = _leftover_recent(cid) if cid else []
    avoid_line = f"Не предлагай эти блюда (уже были из холодильника): {', '.join(avoid)}.\n" if avoid else ""
    return ai.llm_json(
        f"{cz}{avoid_line}{pref}Ты — шеф-повар с идеальной логикой. "
        f"Создай 1 рецепт ({constraint}), 1 человек, электрическая плита, духовка SAGE.\n"
        "Правила:\n"
        "• Все ингредиенты должны быть использованы, но не перечисляй их повторно в каждом шаге.\n"
        "• Не меняй технику без веской причины: начал на сковороде — не гони в духовку. Минимум посуды.\n"
        "• time — общее время блюда; время каждого шага отдельно не показывай.\n"
        "• Каждый шаг начинай с глагола в повелительном наклонении и оставляй только нужное действие.\n"
        "• Обычно 3 шага; для простого блюда можно 2, для сложного максимум 4. Объединяй связанные действия. "
        "Каждый шаг — не больше 1–2 коротких предложений, только действия без вводных слов и описаний вкуса.\n"
        "• В ингредиентах всегда добавляй базу (масло, соль, перец), если нужна для готовки.\n"
        'JSON (без markdown): {"name":"Название блюда","time":"X мин","servings":"1 порц.",'
        '"ingredients":"список через запятую",'
        '"steps":["Глагол + действие + конкретика","шаг 2","шаг 3"],'
        '"full":"тот же рецепт в том же стиле: сначала заголовок, затем <b>Ингредиенты</b>, затем <b>Приготовление</b>, затем <b>😋 Приятного аппетита!</b>. '
        'Без времени и порции, без лишнего текста."}',
        900, tier="cheap", module="food",
        fallback_allowed=True, privacy_level="personal", allow_personal_openrouter=True)

def _fallback_recipe():
    return {
        "name": "Быстрый омлет с овощами",
        "time": "12 мин",
        "servings": "1 порц.",
        "ingredients": "2 яйца, горсть овощей, масло, соль, перец",
        "steps": [
            "Разогрей сковороду с маслом 1 минуту",
            "Обжарь овощи 3-4 минуты",
            "Влей взбитые яйца и готовь под крышкой 5-6 минут",
        ],
        "full": (
            "Быстрый омлет с овощами\n\n"
            "<b>Ингредиенты</b>\n"
            "2 яйца, горсть овощей, масло, соль, перец\n\n"
            "<b>Приготовление</b>\n"
            "Разогрей сковороду, обжарь овощи, влей яйца и доведи под крышкой.\n\n"
            "<b>😋 Приятного аппетита!</b>"
        ),
    }

def _gen_leftovers_recipe(ingredients, cid=None):
    avoid = _leftover_recent(cid) if cid else []
    avoid_line = f"Не предлагай снова: {', '.join(avoid)}.\n" if avoid else ""
    context = _cuisine_context(cid) if cid else ""
    cz = (context + " Учитывай как пожелание к стилю блюда, но используй только доступные продукты.\n") if context else ""
    return ai.llm_json(
        f"{avoid_line}{cz}Есть продукты: {secure.wrap_untrusted(ingredients, 'продукты')}. "
        "Предложи 1 простой рецепт только из них (+ базовые специи, максимум 1 доп продукт). 1 человек.\n"
        'JSON: {"name":"название","time":"X мин","servings":"1 порц.",'
        '"ingredients":"список использованных продуктов через запятую",'
        '"steps":["шаг 1 (до 15 слов)","шаг 2","шаг 3"]}',
        500, tier="cheap", module="food",
        fallback_allowed=True, privacy_level="personal", allow_personal_openrouter=True)


def _fallback_leftovers_recipe(ingredients):
    """Простой рецепт без AI на случай лимита обоих провайдеров.

    Использует только названия из холодильника; вода и сухая антипригарная
    сковорода позволяют не приписывать пользователю масло или специи.
    """
    names = []
    seen = set()
    for raw in re.split(r"[,;\n]+", str(ingredients or "")):
        name = " ".join(raw.split()).strip(" -•")
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    if not names:
        return None

    by_cat = {}
    for name in names:
        by_cat.setdefault(_fridge_cat(name), []).append(name)
    vegetables = by_cat.get("овощи", [])
    fruit = by_cat.get("фрукты", [])
    grains = by_cat.get("крупы и макароны", [])
    bread = by_cat.get("хлеб и выпечка", [])
    proteins = by_cat.get("мясо и рыба", [])
    dairy = by_cat.get("молочное и яйца", [])
    eggs = [name for name in dairy if re.search(r"яйц|eieren", name, re.I)]
    cheese = [name for name in dairy if re.search(r"сыр|пармез|моцарел|фет|kaas", name, re.I)]
    cultured = [name for name in dairy if re.search(r"йогурт|творог|yoghurt", name, re.I)]
    cookable_vegetables = [
        name for name in vegetables
        if re.search(r"морков|лук|чеснок|перец|броккол|цукини|кабач|баклаж|шпинат|капуст|тыкв|гриб|шампиньон|горош|кукуруз|помидор|томат", name, re.I)
    ]
    sandwich_vegetables = [
        name for name in vegetables
        if re.search(r"помидор|томат|перец|гриб|шампиньон|лук", name, re.I)
    ]
    sandwich_proteins = [
        name for name in proteins
        if re.search(r"ветчин|колбас|салями|мортаделл|копч[ёе]н|тунец|шпрот|сардин", name, re.I)
    ]

    def build(name, minutes, used, steps, tip):
        return {
            "name": name,
            "time": f"{minutes} мин",
            "servings": "1 порц.",
            "ingredients": ", ".join(used),
            "steps": steps,
            "chef_tip": tip,
        }

    if eggs:
        used = eggs[:1] + cookable_vegetables[:2] + cheese[:1]
        if cookable_vegetables and cheese:
            title = "Омлет с овощами и сыром"
        elif cookable_vegetables:
            title = "Омлет с овощами"
        elif cheese:
            title = "Омлет с сыром"
        else:
            title = "Омлет"
        return build(title, 12, used, [
            "Нарежь добавки небольшими кусочками",
            "Прогрей их на сухой антипригарной сковороде 3–4 минуты",
            "Взбей яйца, влей и готовь под крышкой 6–7 минут",
        ], "Сними сковороду с огня, когда центр ещё слегка влажный: омлет дойдёт под крышкой")

    pasta = next((name for name in grains if re.search(r"макар|спагет|паст|лапш|noedel", name, re.I)), None)
    if pasta and (cookable_vegetables or cheese):
        used = [pasta] + cookable_vegetables[:2] + cheese[:1]
        return build("Паста с овощами" if cookable_vegetables else "Паста с сыром", 20, used, [
            f"Отвари {pasta} по инструкции на упаковке и сохрани половника воды",
            "Нарежь добавки и прогрей их на сковороде с двумя ложками воды 5–7 минут",
            "Добавь пасту, влей немного воды от варки и перемешай 1 минуту",
        ], "Вода от варки свяжет добавки с пастой и сделает соус гладким")

    grain_base = next((name for name in grains if re.search(r"рис|греч|булгур|кускус|киноа|перлов|пшен|овсян", name, re.I)), None)
    if grain_base and cookable_vegetables:
        base = grain_base
        used = [base] + cookable_vegetables[:3]
        base_title = next((label for pattern, label in (
            (r"рис", "Рис"), (r"греч", "Гречка"),
            (r"булгур", "Булгур"), (r"кускус", "Кускус"),
        ) if re.search(pattern, base, re.I)), "Крупа")
        return build(f"{base_title} с овощами", 25, used, [
            f"Приготовь {base} по инструкции на упаковке",
            "Нарежь овощи одинаковыми кусочками и туши под крышкой с третью стакана воды 8–10 минут",
            "Смешай крупу с овощами и прогрей 2 минуты",
        ], "Дай крупе постоять под крышкой 3 минуты, чтобы она впитала овощной сок")

    if bread and (cheese or sandwich_proteins or sandwich_vegetables):
        used = bread[:1] + cheese[:1] + sandwich_proteins[:1] + sandwich_vegetables[:1]
        return build("Горячие бутерброды", 12, used, [
            "Нарежь начинку тонкими ломтиками",
            "Разложи начинку на хлебе, сыр положи сверху",
            "Прогрей под крышкой на сухой сковороде 6–8 минут",
        ], "Капля воды под крышкой создаст пар: сыр расплавится, а хлеб не пересохнет")

    if cultured and fruit:
        used = cultured[:1] + fruit[:3]
        title = "Творог с фруктами" if re.search(r"творог", cultured[0], re.I) else "Йогурт с фруктами"
        return build(title, 5, used, [
            "Нарежь фрукты небольшими кусочками",
            "Выложи основу в миску и добавь фрукты",
            "Перемешай часть фруктов с основой, остальные оставь сверху",
        ], "Часть фруктов разомни ложкой: сок сделает основу мягче без отдельного соуса")

    if proteins and cookable_vegetables:
        used = proteins[:1] + cookable_vegetables[:3]
        is_fish = bool(re.search(r"рыб|лосос|с[её]мг|тунец|треск|форел|кревет", proteins[0], re.I))
        return build("Рыба с овощами" if is_fish else "Мясо с овощами", 25, used, [
            "Подготовь основной продукт и нарежь овощи одинаковыми кусочками",
            "Готовь основной продукт на антипригарной сковороде до полной готовности",
            "Добавь овощи и треть стакана воды, накрой и туши 8–10 минут",
        ], "Нарежь овощи одинаково: тогда они дойдут до готовности одновременно")

    if len(cookable_vegetables) >= 2:
        used = cookable_vegetables[:4]
        return build("Тушёные овощи", 20, used, [
            "Нарежь овощи одинаковыми кусочками",
            "Выложи плотные овощи в сковороду, добавь треть стакана воды и туши 8 минут",
            "Добавь мягкие овощи и готовь под крышкой ещё 6–8 минут",
        ], "Клади плотные овощи первыми, а сочные — в конце, чтобы они не развалились")

    if len(fruit) >= 2:
        used = fruit[:4]
        return build("Фруктовый салат", 7, used, [
            "Нарежь фрукты кусочками одного размера",
            "Сложи их в миску и аккуратно перемешай",
            "Оставь на 3 минуты, чтобы фрукты дали сок",
        ], "Самые мягкие фрукты добавь последними, чтобы они сохранили форму")
    return None


# ---------- Батч-генерация очереди рецептов (§5 спеки) ----------
# Набор машиночитаемых кодов кухонь совпадает с settings.CUISINE_OPTIONS
# (кросс-региональные группы вроде "asian" совпадают с настройками пользователя,
# чтобы cuisine_weights/приоритеты считались по тем же ключам) + расширение
# конкретными странами для более точного флага в карточке (§7: "всегда показывать
# происхождение блюда"). Модель может вернуть код вне списка (в т.ч. новую страну) —
# это нормально, UI-агент обязан иметь fallback на 🍽, если cuisine_emoji пустой/
# нераспознанный, поэтому список ниже не является жёстким enum для валидации,
# а служит только подсказкой модели в промпте.
RECIPE_CUISINE_CODES = (
    "asian", "russian", "italian", "mediterranean", "mexican", "french",
    "japanese", "korean", "chinese", "thai", "vietnamese", "indian",
    "turkish", "greek", "spanish", "german", "american", "georgian",
)

# Фолбэк-эмодзи флага по коду кухни — на случай пустого/нераспознанного
# cuisine_emoji от модели. Кросс-региональные коды (asian/mediterranean) не имеют
# одного флага — используем нейтральную эмблему блюда.
RECIPE_CUISINE_EMOJI_FALLBACK = CUISINE_EMOJI

RECIPE_BATCH_SIZE = 10
RECIPE_BATCH_MAX_TOKENS = 5000  # ~10 рецептов * (поля + шаги с длительностью) с запасом на JSON-обвязку


def _season_hint() -> str:
    """Сезонная подсказка по текущему месяцу сервера (§5.2). Без геолокации пользователя."""
    month = datetime.now(TZ).month if TZ else datetime.now().month
    if month in (6, 7, 8):
        return "Сейчас лето: предпочитай лёгкие блюда — салаты, гриль, свежие овощи, холодные супы."
    if month in (12, 1, 2):
        return "Сейчас зима: предпочитай сытные блюда — супы, запеканки, тушёное, горячее."
    return ""


def _cuisine_weights_line(cuisine_weights: dict) -> str:
    """Ранжированный список предпочтений кухонь по весу для промпта (§5.3).

    cuisine_weights — {cuisine: weight}, положительный вес важнее. Это подсказка
    модели, не жёсткий фильтр (§9: без пост-валидации по списку кандидатов).
    """
    if not cuisine_weights:
        return ""
    ranked = sorted(cuisine_weights.items(), key=lambda kv: kv[1], reverse=True)
    ranked = [(c, w) for c, w in ranked if w != 0]
    if not ranked:
        return ""
    parts = ", ".join(f"{c} (вес {w:+d})" for c, w in ranked)
    return (
        "Предпочтения пользователя по кухням, от наиболее к наименее желанной "
        f"(учитывай как приоритет при выборе кухонь, не как жёсткий фильтр): {parts}.\n"
    )


def _recipe_batch_prompt(constraint, cid, cuisine_weights, recent_history, season_hint, n, meal_guard="") -> str:
    """Собирает промпт батч-генерации очереди рецептов. Вынесено отдельно от
    _gen_recipe_batch, чтобы промпт можно было проверить без вызова LLM."""
    pref = _my_recipe_pref(cid)
    context = _cuisine_context(cid) if cid else ""
    cz = (context + "\n") if context else ""
    weights_line = _cuisine_weights_line(cuisine_weights)
    season_line = f"{season_hint}\n" if season_hint else ""
    avoid = recent_history or []
    avoid_line = f"Не предлагай эти блюда (уже показывались недавно): {', '.join(avoid)}.\n" if avoid else ""
    cuisine_codes_line = "Коды кухонь (машиночитаемые, используй один из них или ближайший по стране): " + ", ".join(RECIPE_CUISINE_CODES) + ".\n"
    guard_line = f"{meal_guard}\n" if meal_guard else ""
    return (
        f"{cz}{weights_line}{season_line}{avoid_line}{pref}"
        f"Ты — шеф-повар с идеальной логикой. Составь список из {n} РАЗНЫХ рецептов "
        f"({constraint}), 1 человек, электрическая плита, духовка SAGE.\n"
        f"{guard_line}"
        "Правила для каждого рецепта:\n"
        "• Каждый продукт из ингредиентов обязан появиться в шагах приготовления.\n"
        "• Не меняй технику без веской причины: начал на сковороде — не гони в духовку. Минимум посуды.\n"
        "• Сумма minutes по шагам должна строго равняться полю time.\n"
        "• В каждом шаге text: глагол в повелительном наклонении + конкретика (уровень огня, крышка). "
        "НЕ пиши время внутри text (ни минуты, ни «X мин») — время идёт только в отдельное поле minutes.\n"
        "• Обычно 3 шага; для простого блюда можно 2, для сложного максимум 4. Объединяй связанные действия. "
        "Каждый шаг — не больше 1–2 коротких предложений, только действия без вводных слов и описаний вкуса.\n"
        "• В ингредиентах всегда добавляй базу (масло, соль, перец), если нужна для готовки.\n"
        "• chef_tip — НЕ банальный совет (запрещены клише вроде «используйте свежие продукты», "
        "«не пересаливайте», «дайте настояться») — только неочевидный приём именно для этого блюда.\n"
        "• name — НЕ включай национальное прилагательное или название кухни (не «Итальянские тосты», "
        "не «Японский омлет», не «Турецкий завтрак») — кухня уже отдельным полем cuisine и так будет "
        "показана в заголовке карточки. Пиши только сам предмет блюда (например «Тосты с авокадо», "
        "«Омлет с луком», «Шакшука»).\n"
        f"{cuisine_codes_line}"
        "• cuisine_emoji — эмодзи флага страны происхождения блюда (например 🇯🇵, 🇮🇹, 🇰🇷, 🇹🇷).\n"
        "• Фото в готовке не используются. Не добавляй name_en, photo_query_en и photo_fallback_queries.\n"
        "• Разнообразие внутри списка: не более 2 рецептов одной кухни подряд, но общий перекос в сторону "
        "любимых кухонь пользователя (см. предпочтения выше) сохраняй.\n"
        f"• Верни ровно {n} рецептов в массиве, без повторов названий внутри самого списка.\n"
        'JSON (без markdown, объект с одним ключом "recipes"): {"recipes":[{'
        '"name":"Название блюда","cuisine":"код кухни","cuisine_emoji":"🇯🇵",'
        '"time":"X мин","servings":"1 порц.",'
        '"ingredients":"список через запятую",'
        '"steps":[{"text":"Глагол + действие + конкретика","minutes":2},{"text":"шаг 2","minutes":4}],'
        '"chef_tip":"неочевидный совет именно для этого блюда",'
        '"full":"тот же рецепт в том же стиле: сначала заголовок, затем <b>Ингредиенты</b>, затем '
        '<b>Приготовление</b>, затем <b>😋 Приятного аппетита!</b>. Без времени и порции, без лишнего текста."'
        "}, ... ещё " + str(n - 1) + " таких объектов]}"
    )


def _gen_recipe_batch(constraint, cid=None, cuisine_weights=None, recent_history=None,
                       season_hint=None, n=RECIPE_BATCH_SIZE, meal_guard=""):
    """Генерирует за один вызов LLM список из ~n рецептов (§5.1 спеки).

    constraint — тип приёма пищи ("завтрак"/"обед"/"ужин") или список продуктов
    холодильника (см. _gen_leftovers_recipe_batch ниже — тонкая обёртка над этой
    функцией с constraint, описывающим доступные продукты).
    cuisine_weights — {cuisine: weight}, обычно из get_cuisine_weights(cid).
    recent_history — список названий "не повторять", обычно из get_recipe_history(cid).
    season_hint — строка из _season_hint() (можно передать заранее посчитанной).
    meal_guard — явный жёсткий запрет на блюда других приёмов пищи (см. _MEAL_GUARD),
    чтобы сезонная подсказка не перетягивала завтрак в сторону обеда/ужина.

    ai.llm_json умеет возвращать только dict верхнего уровня (JSON-массив он бы
    схлопнул до первого элемента) — поэтому просим модель обернуть массив в
    {"recipes": [...]} и распаковываем сами.

    Возвращает list[dict] — не более n элементов, но может быть и меньше, если
    модель вернула меньше (вызывающий код должен быть к этому готов).
    """
    if season_hint is None:
        season_hint = _season_hint()
    prompt = _recipe_batch_prompt(constraint, cid, cuisine_weights or {}, recent_history or [], season_hint, n, meal_guard)
    result = ai.llm_json(
        prompt, RECIPE_BATCH_MAX_TOKENS, tier="cheap", module="food",
        fallback_allowed=True, privacy_level="personal", allow_personal_openrouter=True,
    )
    items = result.get("recipes") if isinstance(result, dict) else None
    if not isinstance(items, list):
        # модель могла вернуть один рецепт плоским объектом вместо {"recipes":[...]}"
        # (например, при очень коротком max_tokens/шумном ответе) — не роняем вызывающий
        # код, просто отдаём то, что похоже на единственный рецепт.
        items = [result] if isinstance(result, dict) and result.get("name") else []
    return [it for it in items if isinstance(it, dict) and it.get("name")][:n]


def _gen_leftovers_recipe_batch(ingredients, cid=None, cuisine_weights=None, recent_history=None,
                                 season_hint=None, n=RECIPE_BATCH_SIZE):
    """Батч-версия для холодильника (§3 п.5 спеки — fridge включён в общую систему).

    Та же _gen_recipe_batch, только constraint формулирует ограничение по доступным
    продуктам вместо типа приёма пищи. ingredients оборачивается через
    secure.wrap_untrusted, как и в одиночной _gen_leftovers_recipe, — список продуктов
    вводится пользователем и не должен трактоваться моделью как инструкции.
    """
    if api_usage.gemini_state(1).get("cooldown_active"):
        local = _fallback_leftovers_recipe(ingredients)
        if local:
            _log.info("gemini cooldown active, using local fridge recipe")
            return [local]

    constraint = (
        f"только из доступных продуктов: {secure.wrap_untrusted(ingredients, 'продукты')} "
        "(+ базовые специи, максимум 1 доп. продукт на рецепт)"
    )
    try:
        items = _gen_recipe_batch(
            constraint, cid=cid, cuisine_weights=cuisine_weights,
            recent_history=recent_history, season_hint=season_hint, n=n,
        )
    except Exception as error:
        _log.warning("fridge recipe batch failed, retrying one recipe: %s", error)
        items = []

    # Большой JSON с очередью иногда приходит пустым или обрезанным. Не оставляем
    # пользователя без результата: компактный запрос на один рецепт заметно надёжнее.
    complete = [
        item for item in items
        if (isinstance(item, dict) and item.get("name")
            and item.get("ingredients") and item.get("steps"))
    ]
    if complete:
        return complete

    local = _fallback_leftovers_recipe(ingredients)
    return [local] if local else []
