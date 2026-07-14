from datetime import datetime

import ai
import config
import secure
import store
from recipe_state import _leftover_recent
from ui.constants import CUISINE_EMOJI

TZ = config.TZ


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
        "• Каждый продукт из ингредиентов обязан появиться в шагах приготовления.\n"
        "• Не меняй технику без веской причины: начал на сковороде — не гони в духовку. Минимум посуды.\n"
        "• Сумма минут по шагам должна строго равняться полю time.\n"
        "• В каждом шаге: глагол в повелительном наклонении + конкретика (минуты, уровень огня, крышка).\n"
        "• 3–5 шагов. Один шаг — одно-два действия. Без вводных слов и описаний вкуса.\n"
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
        "• 3–5 шагов. Один шаг — одно-два действия. Без вводных слов и описаний вкуса.\n"
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
    constraint = (
        f"только из доступных продуктов: {secure.wrap_untrusted(ingredients, 'продукты')} "
        "(+ базовые специи, максимум 1 доп. продукт на рецепт)"
    )
    return _gen_recipe_batch(constraint, cid=cid, cuisine_weights=cuisine_weights,
                              recent_history=recent_history, season_hint=season_hint, n=n)


