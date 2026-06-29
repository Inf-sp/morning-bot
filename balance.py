import asyncio
from datetime import datetime
import logging
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store

_log = logging.getLogger(__name__)
import ai
import rerank
import util
from util import esc, cap_sentence
import verify
import secure
import memory

TZ = config.TZ

_food_tip_cache: dict = {}  # cid -> {"date": ..., "text": ...}

# ===== Холодильник: категории =====
# Порядок dict определяет приоритет матчинга. Сначала более специфичные группы,
# потом широкие, чтобы "масло сливочное" и "сок апельсиновый" не улетали не туда.
_FRIDGE_KEYWORDS: dict = {
    "заморозка": [
        "заморож", "замороз", "frozen", "diepvries",
        "картофель фри", "картошка фри", "фри",
    ],
    "мясо и рыба": [
        # мясо и птица
        "курич", "курен", "говядин", "свинин", "фарш", "индейк", "баранин",
        "сосис", "колбас", "ветчин", "бекон", "утк", "кролик", "стейк", "котлет",
        "шашлык", "карбонад", "окорок", "грудин", "вырезк",
        # рыба и морепродукты
        "рыб", "лосос", "сёмг", "семг", "тунец", "треска", "сельд", "сёлд", "селед",
        "скумбри", "форел", "икр", "креветк", "мидии", "кальмар", "осьминог", "краб",
        "шпрот", "сардин", "анчоус", "палтус", "минтай", "хек", "судак", "карп",
        "тиляпи", "дорад", "сибас",
    ],
    "овощи": [
        "помидор", "томат", "огурец", "огурц", "морков", "репчат",
        "лук", "чеснок", "перец болгар", "перец свеж", "картофел", "картошк",
        "брокколи", "цукини", "кабачок", "баклажан", "шпинат", "салат",
        "капуст", "свёкл", "свекл", "сельдерей", "петрушк", "укроп",
        "кинза", "базилик", "рукол", "горошек", "кукуруз",
        "редис", "тыкв", "артишок", "спаржа", "порей", "фенхел",
        "авокадо", "имбир", "пастернак", "топинамбур", "дайкон",
        "коул слоу", "coleslaw", "cole slaw",
    ],
    "фрукты": [
        "яблок", "банан", "апельсин", "лимон", "лайм", "мандарин", "груш",
        "слива", "сливы", "сливу", "персик", "нектарин", "абрикос",
        "ягод", "малин", "клубник", "черник", "виноград", "киви",
        "манго", "ананас", "смородин", "вишн", "черешн",
        "папайя", "гранат", "инжир", "хурм",
        "дын", "арбуз", "клюкв", "голубик", "брусник", "ежевик",
    ],
    "молочное и яйца": [
        # проверяем до фруктов — иначе 'сливочное' матчит 'слив' из фруктов
        "масло слив", "сливочн", "молок", "кефир", "йогурт", "творог",
        "сметан", "сливк", "сыр", "пармезан", "моцарелл", "рикотт",
        "бри", "камамбер", "фет", "гауд", "эдам", "чеддер", "халум",
        "ряженк", "варенец", "айран", "кумыс", "яйц",
    ],
    "крупы и макароны": [
        "рис", "гречк", "овсянк", "овёс", "макарон", "спагетт", "паст", "лапш",
        "хлопь", "киноа", "булгур", "кускус", "перловк", "пшен", "чечевиц",
        "нут", "фасол", "горох", "боб", "ячмен", "полба", "амарант",
        "вермишел", "пенне", "фетучин", "тальятелл",
    ],
    "хлеб и выпечка": [
        "хлеб", "батон", "булочк", "тост", "лаваш", "пита",
        "лепёшк", "лепешк", "багет", "чиабатт", "круасс",
        "бублик", "сушк", "хлебц", "хрустящ", "afbakbrood", "broodjes", "brood",
    ],
    "напитки": [
        # проверяем до фруктов — иначе 'сок апельсиновый' матчит 'апельсин' из фруктов
        "чай", "кофе", "сок", "морс", "компот", "квас", "лимонад",
        "минерал", "газировк", "энергетик", "пиво", "вино", "сидр",
        "какао", "смузи", "напиток",
    ],
    "снеки и сладости": [
        "печень", "шоколад", "конфет", "батончик", "чипс", "снэк", "сухар",
        "печен", "пирог", "торт", "кекс", "десерт", "морожен",
    ],
    "специи и соусы": [
        "соль", "специ", "приправ", "соус", "уксус", "горчиц", "кетчуп",
        "майонез", "соев", "песто", "тахин", "хумус",
        "масло оливк", "масло растит", "масло подсолн", "масло кунжут",
        "перец чёрн", "перец черн", "перец молот", "паприк", "карри", "куркум",
        "корица", "ваниль", "лавров", "орегано", "тимьян", "розмарин",
        "мускат", "чили", "острый соус",
        "мёд", "мед", "варень", "джем", "конфитюр",
    ],
}
_CAT_EMOJI: dict = {
    "мясо и рыба": "🥩", "овощи": "🥦", "фрукты": "🍎", "молочное и яйца": "🥛",
    "крупы и макароны": "🍝", "хлеб и выпечка": "🍞", "специи и соусы": "🧂",
    "напитки": "🥤", "снеки и сладости": "🍪", "заморозка": "❄️",
    "прочее": "📦",
}
# Короткие названия для кнопок (чтобы помещались в 2 столбца)
_CAT_BTN_LABEL: dict = {
    "мясо и рыба": "Мясо/рыба", "молочное и яйца": "Молочное",
    "крупы и макароны": "Крупы/паста", "хлеб и выпечка": "Хлеб/выпечка",
    "специи и соусы": "Специи", "снеки и сладости": "Снеки",
    "заморозка": "Заморозка",
}
_CAT_ORDER = [
    "мясо и рыба", "овощи", "фрукты", "молочное и яйца",
    "крупы и макароны", "хлеб и выпечка", "напитки", "специи и соусы",
    "снеки и сладости", "заморозка", "прочее",
]

# Устаревшие категории → новые (для миграции существующих записей)
_CAT_REMAP = {
    "мясо":     "мясо и рыба",
    "рыба":     "мясо и рыба",
    "молочное": "молочное и яйца",
    "крупы":    "крупы и макароны",
    "хлеб":     "хлеб и выпечка",
    "специи":   "специи и соусы",
    "напитки":  "напитки",
    "сладости": "снеки и сладости",
    "снеки":    "снеки и сладости",
    "заморозка": "заморозка",
    "замороженное": "заморозка",
}
_CAT_VALID = frozenset(_CAT_ORDER)
_FRIDGE_FALLBACK_TARGET = {
    "овощи": "овощи",
    "фрукты": "фрукты",
    "молочное и яйца": "молочное и яйца",
    "крупы и макароны": "крупы и макароны",
    "хлеб и выпечка": "хлеб и выпечка",
    "напитки": "напитки",
    "специи и соусы": "специи и соусы",
    "снеки и сладости": "снеки и сладости",
    "мясо и рыба": "мясо и рыба",
    "заморозка": "заморозка",
    "прочее": "прочее",
}

FRIDGE_MIN_CAT = 3  # минимум продуктов для отдельной кнопки категории


def _fridge_cat(name: str) -> str:
    """Определить категорию продукта по ключевым словам."""
    n = name.lower()
    for cat, keywords in _FRIDGE_KEYWORDS.items():
        if any(k in n for k in keywords):
            return cat
    return "прочее"


def _fridge_migrate(items: list) -> list:
    """Конвертировать старые строки в {name, cat, on}. Мигрирует устаревшие категории."""
    result = []
    for it in items:
        if isinstance(it, dict):
            cat = it.get("cat", "прочее")
            if cat in _CAT_REMAP:
                it = {**it, "cat": _CAT_REMAP[cat]}
            elif cat not in _CAT_VALID:
                # Неизвестная/устаревшая категория — перекласифицируем по имени
                it = {**it, "cat": _fridge_cat(it.get("name", ""))}
            elif cat == "прочее":
                detected = _fridge_cat(it.get("name", ""))
                if detected != "прочее":
                    it = {**it, "cat": detected}
            result.append(it)
        else:
            s = str(it)
            result.append({"name": s, "cat": _fridge_cat(s), "on": True})
    return result


def _fridge_by_cat_display(items: list) -> dict:
    """Словарь cat → [(global_idx, item)] для отображения.
    Категории с менее чем FRIDGE_MIN_CAT продуктами сливаются в 'прочее'."""
    by_cat = _fridge_by_cat(items)
    result: dict = {cat: [] for cat in _CAT_ORDER}
    for cat in _CAT_ORDER:
        for gi, it in by_cat.get(cat, []):
            target = cat if len(by_cat.get(cat, [])) >= FRIDGE_MIN_CAT or cat == "прочее" else _FRIDGE_FALLBACK_TARGET.get(cat, "прочее")
            result[target].append((gi, it))
    return {cat: items for cat, items in result.items() if items}


def _fridge_available(items: list) -> list:
    """Имена продуктов с on=True (для рецепта)."""
    return [it["name"] for it in _fridge_migrate(items) if it.get("on", True)]

def _food_tip_context(cid) -> str:
    import memory
    lagom = memory.get_lagom(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid)
    parts = []
    if lagom:
        parts.append("Ценности и стиль жизни: " + "; ".join(str(x) for x in lagom[:8]))
    if recipes:
        parts.append("Любимые рецепты уже есть: " + ", ".join(str(r) for r in recipes[:6]))
    hints = memory.profile_hints(cid)
    if hints:
        parts.append(hints)
    return "\n".join(parts)

def _food_card(d, label="Рецепт дня") -> str:
    """Единый формат карточки рецепта для радара и нового рецепта."""
    name = esc(str(d.get("name", "")).strip())
    ingredients = esc(str(d.get("ingredients", "")).strip())
    steps = d.get("steps") or []
    if isinstance(steps, str):
        steps = [steps]
    lines = [f"🥣 <b>{label} • {name}</b>"]
    if ingredients:
        lines += ["", "<b>Ингредиенты:</b>", ingredients]
    if steps:
        lines += ["", "<b>Приготовление:</b>"]
        for step in steps:
            lines.append(f"• {esc(str(step).strip())}")
    lines += ["", "<b>😋 Приятного аппетита!</b>"]
    return "\n".join(lines)

def fetch_food_tip(cid) -> str:
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cache = _food_tip_cache.get(str(cid))
    if cache and cache.get("date") == today:
        return cache["text"]
    ctx = _food_tip_context(cid)
    prompt = (
        "Ты кулинарный советник. Предложи один интересный рецепт, который понравится этому человеку.\n"
        + (ctx + "\n" if ctx else "")
        + "Учти стиль жизни — не повторяй уже знакомые блюда из списка любимых.\n"
        "Верни JSON (без markdown): "
        '{"name":"Название блюда","time":"X мин","servings":"1 порц.",'
        '"ingredients":"короткий список через запятую",'
        '"steps":["шаг 1 (до 15 слов)","шаг 2","шаг 3"]}'
    )
    try:
        d = ai.llm_json(prompt, 400, tier="cheap")
        text = _food_card(d) if d.get("name") else ""
    except Exception:
        text = ""
    _food_tip_cache[str(cid)] = {"date": today, "text": text}
    return text

DOCTOR_INTRO = (
    "👩🏻‍⚕️ Врач\n\n"
    "Дам общую справочную информацию о здоровье и лекарствах. Это не диагноз и не назначение - "
    "при тревожных симптомах обратись к специалисту.\n\n"
    "Опиши, что беспокоит, или спроси про лекарство 👇"
)

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

# универсальная клавиатура под ответом: [Продолжить][Короче|Глубже][⭐][В меню]
def _ans_kb(cont_label="🔄 Продолжить", cont_cb="chat_retry", depth=True):
    rows = []
    if cont_label and cont_cb:
        rows.append([(cont_label, cont_cb)])
    if depth:
        rows.append([("✂️ Короче", "ans_short"), ("🔬 Глубже", "ans_deep")])
    rows.append([("⏳ Позже", "as_fav"), ("◀️ Назад", "m_close")])
    return _kb(rows)

def _recipe_kb():
    return _kb([
        [("✨ Ещё рецепт", "as_food")],
        [("◀️ Назад", "m_close")],
    ])

def _recipe_typed_kb():
    """Клавиатура после «Новый рецепт» — только выбор типа приёма пищи."""
    return _kb([
        [("🍳 Завтрак", "a_recipe_breakfast"), ("🥗 Обед", "a_recipe_lunch"), ("🍽️ Ужин", "a_recipe_dinner")],
        [("◀️ Назад", "m_food")],
    ])

def _fridge_recipe_kb():
    return _recipe_typed_kb()

def _back_kb():
    return _kb([[("◀️ Назад", "m_close")]])


async def _send(bot, cid, text, kb=None, surface="card"):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    text, _w = verify.grade_text(text, surface)   # health->дисклеймер, chat->≤1 эмодзи
    for w in _w:
        print(f"[verify] {surface}: {w}")
    store.last_answer[str(cid)] = text
    store.last_source.setdefault(str(cid), "Ассистент")
    store.last_surface[str(cid)] = surface       # для «Короче/Глубже»
    html = util.tg_html(text)
    chunks = [html[i:i+4000] for i in range(0, len(html), 4000)]
    for i, c in enumerate(chunks):
        markup = (kb if kb is not None else _ans_kb()) if i == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=c, parse_mode="HTML", reply_markup=markup)
        except Exception:
            # если HTML невалиден - отправляем как обычный текст, без падения
            await bot.send_message(chat_id=cid, text=c, reply_markup=markup)


# ---------- Кулинарный радар ----------
def _my_recipe_pref(cid):
    """Контекст из базы рецептов для промпта (первые 5 названий)."""
    if not cid:
        return ""
    saved = store.get_list(config.MY_RECIPES_KEY, str(cid))[:5]
    names = ", ".join(r.get("name", "") for r in saved if r.get("name"))
    return f"Пользователь любит готовить: {names}. Похожий стиль приветствуется.\n" if names else ""


def _gen_recipe(constraint, cid=None):
    pref = _my_recipe_pref(cid)
    return ai.llm_json(
        f"{pref}Ты — шеф-повар с идеальной логикой. "
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
        900, tier="cheap")

def _recipe_card(d):
    return _food_card(d, label="Рецепт дня")

async def send_recipe(bot, cid, constraint="обычное блюдо"):
    await bot.send_message(chat_id=cid, text="Подбираю...")
    try:
        d = await asyncio.to_thread(_gen_recipe, constraint, cid=cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", constraint)
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card
    await util.send_html(bot, cid, card, reply_markup=_recipe_kb())

async def send_recipe_featured(bot, cid):
    """Новый рецепт из меню — под результатом кнопки завтрак/обед/ужин."""
    await bot.send_message(chat_id=cid, text="Подбираю рецепт...")
    try:
        d = await asyncio.to_thread(_gen_recipe, "любое блюдо под вкус пользователя", cid=cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", "featured")
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card
    await util.send_html(bot, cid, card, reply_markup=_recipe_typed_kb())

async def send_recipe_push(bot, cid):
    """Уведомление 12:30 — без кнопок."""
    try:
        d = await asyncio.to_thread(_gen_recipe, "любое блюдо под вкус пользователя", cid=cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card
    await util.send_html(bot, cid, card)


def _gen_leftovers_recipe(ingredients):
    return ai.llm_json(
        f"Есть продукты: {secure.wrap_untrusted(ingredients, 'продукты')}. "
        "Предложи 1 простой рецепт только из них (+ базовые специи, максимум 1 доп продукт). 1 человек.\n"
        'JSON: {"name":"название","time":"X мин","servings":"1 порц.",'
        '"ingredients":"список использованных продуктов через запятую",'
        '"steps":["шаг 1 (до 15 слов)","шаг 2","шаг 3"]}',
        500, tier="cheap")

async def send_leftovers(bot, cid, ingredients):
    await bot.send_message(chat_id=cid, text="Смотрю, что можно приготовить...")
    try:
        d = await asyncio.to_thread(_gen_leftovers_recipe, ingredients)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("leftovers", ingredients)
    card = _food_card(d, label="Рецепт из холодильника")
    store.last_source[str(cid)] = "Питание · Остатки"
    store.last_answer[str(cid)] = card
    await util.send_html(bot, cid, card, reply_markup=_fridge_recipe_kb())


_FRIDGE_PAGE = 8  # продуктов на страницу в категории


def _fridge_by_cat(items: list) -> dict:
    """Словарь cat → [(global_idx, item)] для отображения."""
    by_cat: dict = {}
    for i, it in enumerate(items):
        cat = it.get("cat", "прочее")
        by_cat.setdefault(cat, []).append((i, it))
    return by_cat


# ---------- Мой холодильник: главный экран (категории) ----------
async def send_fridge(bot, cid, q=None, back="m_food"):
    cid_s = str(cid)
    raw = store.get_list(config.FRIDGE_KEY, cid_s)
    items = _fridge_migrate(raw)
    if items != raw:
        store.set_list(config.FRIDGE_KEY, cid_s, items)

    if not items:
        txt = "🧊 <b>Мой холодильник</b>\n\nПусто — добавь продукты, которые обычно есть дома."
        rows = [
            [InlineKeyboardButton("✏️ Добавить продукты", callback_data="as_fridge_add")],
            [InlineKeyboardButton("◀️ Назад", callback_data=back)],
        ]
    else:
        available = sum(1 for it in items if it.get("on", True))
        by_cat = _fridge_by_cat_display(items)
        txt = f"🧊 <b>Мой холодильник</b> · {len(items)} продуктов · {available} в наличии\n\nВыбери категорию:"
        present_cats = [c for c in _CAT_ORDER if c in by_cat]
        cat_btns = []
        for ci, cat in enumerate(present_cats):
            cat_items = by_cat[cat]
            on_cnt = sum(1 for _, it in cat_items if it.get("on", True))
            emoji = _CAT_EMOJI.get(cat, "📦")
            label = _CAT_BTN_LABEL.get(cat, cat.capitalize())
            cat_btns.append(InlineKeyboardButton(
                f"{emoji} {label} {on_cnt}/{len(cat_items)}",
                callback_data=f"as_fridge_cat_{ci}_0"
            ))
        rows = [[btn] for btn in cat_btns]
        rows.append([
            InlineKeyboardButton("✏️ Добавить", callback_data="as_fridge_add"),
            InlineKeyboardButton("❌ Убрать", callback_data="as_fridge_clean"),
        ])
        rows.append([InlineKeyboardButton("◀️ Назад", callback_data=back)])

    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(txt, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)


# ---------- Экран категории (пагинация + toggle + отдельная чистка) ----------
async def send_fridge_cat(bot, cid, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    by_cat = _fridge_by_cat_display(items)

    # Определяем имя категории по индексу в present_cats (с учётом мержа малых)
    present_cats = [c for c in _CAT_ORDER if c in by_cat]
    if cat_idx >= len(present_cats):
        await send_fridge(bot, cid, q); return
    cat = present_cats[cat_idx]
    cat_items = by_cat[cat]  # [(global_idx, item)]

    total = len(cat_items)
    pages = max(1, (total + _FRIDGE_PAGE - 1) // _FRIDGE_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = cat_items[page * _FRIDGE_PAGE:(page + 1) * _FRIDGE_PAGE]

    emoji = _CAT_EMOJI.get(cat, "📦")
    on_cnt = sum(1 for _, it in cat_items if it.get("on", True))
    txt = (f"{emoji} <b>{cat.capitalize()}</b> · {total} продуктов · {on_cnt} в наличии\n\n"
           "🟢 — есть в наличии  ⚪ — закончилось\n"
           "Нажми продукт, чтобы изменить статус.")

    # Два продукта в строку. Удаление вынесено в отдельный режим чистки.
    rows = []
    product_buttons = []
    for gi, it in chunk:
        mark = "🟢" if it.get("on", True) else "⚪"
        name_short = it["name"][:18]
        product_buttons.append(
            InlineKeyboardButton(f"{mark} {name_short}", callback_data=f"as_fridge_tgl_{gi}_{cat_idx}_{page}")
        )
    for i in range(0, len(product_buttons), 2):
        rows.append(product_buttons[i:i + 2])

    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"as_fridge_cat_{cat_idx}_{(page-1) % pages}"),
            InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"as_fridge_cat_{cat_idx}_{(page+1) % pages}"),
        ])
    rows.append([
        InlineKeyboardButton("✏️ Добавить", callback_data=f"as_fridge_add_{cat_idx}"),
        InlineKeyboardButton("❌ Убрать", callback_data="as_fridge_clean"),
    ])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="as_fridge_home")])

    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(txt, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)


async def fridge_add_done(bot, cid, text, cat_idx: int = -1):
    cid_s = str(cid)
    parts = re.split(r"[,\n;]+", text)
    items_new = [p.strip().lower() for p in parts if p.strip()]
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    existing = {it["name"].lower() for it in items}
    added = []
    for name in items_new:
        if name and name not in existing:
            items.append({"name": name, "cat": _fridge_cat(name), "on": True})
            existing.add(name)
            added.append(name)
    store.set_list(config.FRIDGE_KEY, cid_s, items)
    if added:
        await bot.send_message(chat_id=cid, text=f"✏️ Добавлено: {', '.join(added)}")
    else:
        await bot.send_message(chat_id=cid, text="Все эти продукты уже есть в списке.")
    if cat_idx >= 0:
        await send_fridge_cat(bot, cid, cat_idx, 0)
    else:
        await send_fridge(bot, cid)


async def fridge_toggle(bot, cid, idx: int, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    if 0 <= idx < len(items):
        items[idx]["on"] = not items[idx].get("on", True)
        store.set_list(config.FRIDGE_KEY, cid_s, items)
    await send_fridge_cat(bot, cid, cat_idx, page, q)


async def fridge_del(bot, cid, idx: int, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    if 0 <= idx < len(items):
        items.pop(idx)
        store.set_list(config.FRIDGE_KEY, cid_s, items)
    await send_fridge_cat(bot, cid, cat_idx, page, q)


async def send_fridge_recipe(bot, cid):
    raw = store.get_list(config.FRIDGE_KEY, str(cid))
    available = _fridge_available(raw)
    if not available:
        await bot.send_message(chat_id=cid,
            text="🧊 Холодильник пуст или все продукты отмечены как отсутствующие.\n\n"
                 "Отметь 🟢, что есть сейчас, и попробуй снова.")
        return
    await send_leftovers(bot, cid, ", ".join(available))


# ---------- База рецептов ----------
async def save_my_recipe(bot, cid):
    cid_s = str(cid)
    d = store.last_recipe.get(cid_s)
    if not d or not d.get("name"):
        await bot.send_message(chat_id=cid, text="Нет рецепта для сохранения."); return
    saved = store.get_list(config.MY_RECIPES_KEY, cid_s)
    names_lower = [r.get("name", "").lower() for r in saved]
    if d["name"].lower() in names_lower:
        await bot.send_message(chat_id=cid, text=f"«{util.esc(d['name'])}» уже есть в твоих рецептах."); return
    store.add_to_list(config.MY_RECIPES_KEY, cid_s, d)
    await bot.send_message(chat_id=cid, text=f"❤️ «{util.esc(d['name'])}» сохранён в базе рецептов.")


async def send_my_recipes(bot, cid):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if not recipes:
        txt = ("🍳 <b>Мои рецепты</b>\n\nПусто. Сохраняй рецепты кнопкой "
               "«❤️ Сохранить рецепт» под любым рецептом.")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="as_bucket_love")]])
    else:
        txt = "🍳 <b>Мои рецепты</b> — {}\n\n".format(len(recipes))
        txt += "\n".join(f"• {util.esc(r.get('name', '?'))}" for r in recipes)
        rows = []
        for i, r in enumerate(recipes):
            name = r.get("name", f"Рецепт {i+1}")[:30]
            rows.append([InlineKeyboardButton(f"📖 {name}", callback_data=f"as_my_recipe_{i}")])
        rows.append([InlineKeyboardButton("❌ Убрать", callback_data="as_recipe_clean")])
        rows.append([InlineKeyboardButton("◀️ Назад", callback_data="as_bucket_love")])
        kb = InlineKeyboardMarkup(rows)
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)


async def send_my_recipe_full(bot, cid, idx):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if idx >= len(recipes):
        await bot.send_message(chat_id=cid, text="Рецепт не найден."); return
    d = recipes[idx]
    store.last_recipe[cid_s] = d
    txt = _food_card(d, label="Рецепт")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить из базы", callback_data=f"as_my_recipe_del_{idx}")],
        [InlineKeyboardButton("◀️  к списку", callback_data="as_my_recipes")],
    ])
    await util.send_html(bot, cid, txt, reply_markup=kb)


async def my_recipe_del(bot, cid, idx):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if idx < len(recipes):
        name = recipes[idx].get("name", "рецепт")
        recipes.pop(idx)
        store.set_list(config.MY_RECIPES_KEY, cid_s, recipes)
        await bot.send_message(chat_id=cid, text=f"❌ «{util.esc(name)}» удалён из базы рецептов.")
    await send_my_recipes(bot, cid)


# ---------- СДВГ / Следующий шаг ----------
def _pick_lagom(cid) -> str:
    """Берёт один неиспользованный Лагом-принцип, при исчерпании — сбрасывает счётчик."""
    import memory
    items = memory.get_lagom(cid)
    if not items:
        return ""
    seen = store.get_list(config.MOTIV_LAGOM_SEEN_KEY, cid)
    unused = [i for i in range(len(items)) if i not in seen]
    if not unused:
        seen = []
        unused = list(range(len(items)))
        store.set_list(config.MOTIV_LAGOM_SEEN_KEY, cid, [])
    import random
    idx = random.choice(unused)
    seen.append(idx)
    store.set_list(config.MOTIV_LAGOM_SEEN_KEY, cid, seen)
    return items[idx]

def _gen_motiv(cid):
    import random
    lagom = _pick_lagom(cid)
    angles = ["физическое действие", "ограничение", "мини-ритуал", "перезагрузку", "один микрошаг"]
    angle = random.choice(angles)
    lagom_ctx = f"Принцип лагома пользователя: «{lagom}»\n" if lagom else ""
    prompt = (
        f"{lagom_ctx}"
        f"Предложи {angle} на основе этого принципа. "
        "Без философии и клише. Конкретно, коротко, на русском. "
        "Верни JSON (без markdown):\n"
        '{"steps":["конкретное действие или ограничение","ещё одно если нужно"],'
        '"why":"1-2 предложения: зачем это работает прямо сейчас"}'
    )
    try:
        d = ai.llm_json(prompt, 300, tier="smart")
        steps = [esc(str(s).strip()) for s in (d.get("steps") or []) if str(s).strip()]
        why = esc(str(d.get("why", "")).strip())
    except Exception:
        return "Одно действие прямо сейчас — встань и пройди круг по комнате."
    lagom_full = esc(lagom) if lagom else "Один шаг."
    lines = [
        "☕️ <b>Мотивация</b>", "",
        f"<b><i>{lagom_full}</i></b>", "",
        "<b>Действие</b>",
    ]
    lines += [f"• {s}" for s in steps]
    if why:
        lines += ["", f"<b>Зачем:</b> {why}"]
    return "\n".join(lines)


async def send_motiv_push(bot, cid):
    """09:00 — плановая мотивация (без 'Секунду...')."""
    out = _gen_motiv(cid)
    store.last_source[str(cid)] = "Баланс · Мотивация"
    store.last_answer[str(cid)] = out
    await bot.send_message(chat_id=cid, text=out, parse_mode="HTML")


# ---------- роли ----------
def _role_system(role):
    if role == "state":
        return ("Ты спокойный помощник по состоянию, фокусу и мотивации ( психотерапевт). "
                "Выслушай, разложи ситуацию на 1-3 конкретных шага, поддержи коротко. Без воды, с эмодзи. "
        )
    if role == "doctor":
        return ("Ты помощник по здоровью. Дай разбор СТРОГО в формате, кратко, с эмодзи:\n"
                "👩🏻‍⚕️ Разбор симптомов\n\n📍 Основная жалоба:\n{коротко}\n\n🔎 На что похоже:\n{1-2 предложения}\n\n"
                "✅ Рекомендации:\n• пункт\n• пункт\n\n🚨 Срочно к врачу:\n{когда}\n\nИтог: {одно короткое предложение}\n\n"
                )
    return "Ты полезный ассистент."

_MED_RE = ("лекарств", "таблет", "препарат", "доз", "мг ", " мг", "метилфенидат", "ибупрофен",
           "парацетамол", "антибиотик", "капл", "сироп", "мазь", "витамин", "пилюл", "concerta",
           "ritalin", "риталин", "медикамент", "побочк", "побочн", "как принимать")

def _is_med_question(text):
    t = (text or "").lower()
    return any(k in t for k in _MED_RE)

def _med_system():
    return ("Ты помощник по лекарствам. Дай СПРАВОЧНУЮ информацию о препарате СТРОГО в формате, кратко, с эмодзи:\n"
            "💊 {название и доза если есть}\n\n"
            "📍 Зачем:\n{коротко}\n\n"
            "⏱️ Когда работает:\n{через сколько и сколько держится}\n\n"
            "⚠️ Часто бывает:\n• побочка\n• побочка\n\n"
            "💡 Важно:\n• пункт\n• пункт\n\n"
            "🚨 К врачу если:\n• симптом\n• симптом\n\n"
            "Итог: {одно короткое предложение}\n\n"
            "Это общая справочная информация, не назначение. Дозы и схему определяет врач.")

def _doctor_candidates(symptoms):
    data = ai.llm_json(
        f"Пользователь описал: {symptoms}\nДай 6 коротких справочных тезисов (общая информация о возможных "
        "причинах/состояниях при таких симптомах; НЕ диагноз). JSON: {\"items\": [\"тезис\", ...]}", 900, tier="cheap")
    return [x for x in data.get("items", []) if isinstance(x, str) and x.strip()]

async def doctor_answer(bot, cid, symptoms):
    if secure.is_dangerous_med(symptoms):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health")
        return
    await bot.send_chat_action(chat_id=cid, action="typing")
    safe_symptoms = secure.wrap_untrusted(symptoms, "симптомы пользователя")
    if _is_med_question(symptoms):
        prompt = f"{_med_system()}\n\nВопрос про лекарство: {safe_symptoms}"
        try:
            out = await ai.allm(prompt, 900, 0.4, route="claude")
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        store.last_source[str(cid)] = "Здоровье · Лекарство"
        store.last_action[str(cid)] = ("role", "doctor", symptoms)
        await _send(bot, cid, out, kb=_ans_kb(None, None, depth=False), surface="health")
        return
    passages = []
    try:
        cands = await asyncio.to_thread(_doctor_candidates, symptoms)
        ranked = rerank.rerank(symptoms, cands, top_n=3)
        passages = [t for t, _ in ranked]
    except Exception:
        passages = []
    base = _role_system("doctor")
    if passages:
        ctx = "\n".join(f"- {p}" for p in passages)
        prompt = f"{base}\n\nНаиболее релевантные тезисы (по симптомам):\n{ctx}\n\nСимптомы: {safe_symptoms}"
    else:
        prompt = f"{base}\n\nСимптомы: {safe_symptoms}"
    try:
        out = await ai.allm(prompt, 900, 0.5, route="claude")
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_source[str(cid)] = "Здоровье · Врач"
    store.last_action[str(cid)] = ("role", "doctor", symptoms)
    await _send(bot, cid, out, kb=_ans_kb(None, None, depth=False), surface="health")

async def handle_role(bot, cid, role, text):
    if role == "doctor":
        await doctor_answer(bot, cid, text); return
    if secure.is_dangerous_med(text):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health"); return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        route = "claude" if role == "state" else "openrouter"
        out = await ai.allm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7, route=route)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_action[str(cid)] = ("role", role, text)
    cont = ("✨ Ещё совет", "chat_retry") if role == "state" else ("🔄 Продолжить", "chat_retry")
    await _send(bot, cid, out, kb=_ans_kb(*cont), surface="chat" if role == "state" else "card")


# ---------- Дневник тревоги ----------
async def send_daycheck(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)   # фикс: ответ не уйдёт в Обратный перевод
    store.game_state.pop(cid, None)
    worries = store.get_list(config.WORRIES_KEY, cid)
    lines = ["📓 <b>Дневник тревоги</b>", "",
             "Сюда выгружай всё, что крутится в голове. Не анализируй - просто запиши.",
             "Каждую тревогу с новой строки. Вечером проверим, что было фактами, а что шумом.", ""]
    if worries:
        lines.append("<b>Тревоги за сегодня:</b>")
        for w in worries:
            lines.append(f"• {esc(w['text'])}")
        lines.append("")
        lines.append("Напиши новые мысли сообщением или очисти список 👇")
    else:
        lines.append("Пока пусто. Напиши тревоги одним сообщением.")
    store.pending_input[cid] = "worry"
    rows = []
    if worries:
        rows.append([InlineKeyboardButton("❌ Очистить все тревоги", callback_data="worry_clearall")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="m_close")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

async def send_evening_review(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)
    store.game_state.pop(cid, None)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    all_worries = store.get_list(config.WORRIES_KEY, cid)
    worries = [w for w in all_worries if w.get("date", today) == today]
    if not worries:
        await bot.send_message(chat_id=cid, parse_mode="HTML",
            text="🥸 <b>Вечерний разбор</b>\n\nСегодня тревог не записано. Если что-то крутится - выгрузи сейчас, каждую с новой строки.")
        store.pending_input[cid] = "worry"
        return
    wlist = "\n".join(f"- {w['text']}" for w in worries)
    try:
        d = await ai.allm_json(
            "Ты спокойный психолог. Разбери тревоги человека с СДВГ по-доброму, на русском.\n"
            "Нужно коротко, без медицинских назначений и без длинной поддержки.\n"
            "Для каждой тревоги дай одну короткую интерпретацию: что может быть фактом, а что предположением.\n"
            "Итог дня - 1 короткое предложение.\n"
            'Верни JSON: {"items":[{"worry":"тревога как есть","note":"коротко, до 20 слов"}],'
            '"summary":"короткий итог, до 22 слов"}\n\n'
            f"Тревоги:\n{wlist}", 700, 0.5)
    except Exception as e:
        _log.warning("send_evening_review: LLM failed, analysis empty: %s", e)
        d = {}
    L = ["🥸 <b>Вечерний разбор</b>", "", "<b>Сегодня тебя беспокоили:</b>"]
    items = d.get("items") or []
    for idx, w in enumerate(worries):
        L.append(f"• {esc(w['text'])}")
        note = ""
        if idx < len(items) and isinstance(items[idx], dict):
            note = (items[idx].get("note") or "").strip()
        if note:
            L.append(f"<i>{esc(note)}</i>")
    summary = (d.get("summary") or "").strip()
    if summary:
        L += ["", "<b>Итог дня:</b>", esc(cap_sentence(summary))]
    rows = [
        [InlineKeyboardButton("❌ Очистить все тревоги", callback_data="worry_clearall")],
    ]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def worry_clear_all(bot, cid):
    cid = str(cid)
    worries = store.get_list(config.WORRIES_KEY, cid)
    if worries:
        summary = f"Разобрано тревог: {len(worries)}"
        store.add_to_list(config.DIARY_KEY, cid, {"date": datetime.now(TZ).strftime("%d.%m"), "text": summary})
    store.set_list(config.WORRIES_KEY, cid, [])
    await bot.send_message(chat_id=cid, text="❌ Дневник тревог очищен. Лёгкой ночи.")

async def save_worries(bot, cid, text):
    cid = str(cid)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    new = [{"text": w.strip(), "status": "pending", "date": today} for w in text.split("\n") if w.strip()]
    existing = store.get_list(config.WORRIES_KEY, cid)
    store.set_list(config.WORRIES_KEY, cid, existing + new)
    await bot.send_message(chat_id=cid, text=f"📝 Записал в дневник тревоги: +{len(new)}. Вечером проверим, что реально случилось.")


_MOTIV_KB = _kb([[("✨ Ещё мотивации", "as_motiv")], [("◀️ Назад", "m_balance")]])

_ONESHOT = {}


# ---------- роутер кнопок Баланса ----------
async def handle_callback(bot, cid, q, data):
    # Кулинарный радар
    if data == "as_food":
        await send_recipe(bot, cid, "обычное блюдо"); return

# дневник тревоги
    if data == "as_daycheck":
        await send_daycheck(bot, cid); return
    if data == "as_worryreview":
        await send_evening_review(bot, cid); return
    # мотивация
    if data == "as_motiv":
        await util.ack_loading(q)
        try:
            out = _gen_motiv(cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        store.last_source[str(cid)] = "Баланс · Мотивация"
        store.last_answer[str(cid)] = out
        await _send(bot, cid, out, kb=_MOTIV_KB, surface="card")
        return
    # одноразовая генерация (прочее)
    if data in _ONESHOT:
        gen, lbl, cb = _ONESHOT[data]
        await util.ack_loading(q)
        try:
            out = gen(cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        store.last_action[str(cid)] = ("oneshot", data)
        store.last_source[str(cid)] = {"as_motiv": "Здоровье · Мотивация"}.get(data, "Ассистент")
        await _send(bot, cid, out, kb=_ans_kb(lbl, cb))
        return
    # врач
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=DOCTOR_INTRO, reply_markup=_back_kb()); return
    # холодильник
    if data in ("as_fridge", "as_fridge_home"):
        await send_fridge(bot, cid, q); return
    if data.startswith("as_fridge_cat_"):
        parts = data.split("_")  # as_fridge_cat_{ci}_{page}
        try:
            await send_fridge_cat(bot, cid, int(parts[3]), int(parts[4]), q)
        except (ValueError, IndexError):
            await send_fridge(bot, cid, q)
        return
    if data.startswith("as_fridge_add_"):
        # добавление из категории: as_fridge_add_{ci}
        try:
            ci = int(data.split("_")[-1])
        except (ValueError, IndexError):
            ci = -1
        store.pending_input[str(cid)] = f"fridge_add_{ci}"
        await bot.send_message(chat_id=cid,
            text="✏️ Напиши продукты через запятую или с новой строки — добавлю в список.",
            reply_markup=_back_kb()); return
    if data == "as_fridge_add":
        store.pending_input[str(cid)] = "fridge_add_-1"
        await bot.send_message(chat_id=cid,
            text="✏️ Напиши продукты через запятую или с новой строки — добавлю в список.",
            reply_markup=_back_kb()); return
    if data == "as_fridge_cook":
        await util.ack_loading(q); await send_fridge_recipe(bot, cid); return
    if data == "as_fridge_clean":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "fridge"); return
    if data.startswith("as_fridge_tgl_"):
        # as_fridge_tgl_{idx}_{ci}_{page}
        parts = data.split("_")
        try:
            await fridge_toggle(bot, cid, int(parts[3]), int(parts[4]), int(parts[5]), q)
        except (ValueError, IndexError):
            await send_fridge(bot, cid, q)
        return
    if data.startswith("as_fridge_del_"):
        # as_fridge_del_{idx}_{ci}_{page}
        parts = data.split("_")
        try:
            await fridge_del(bot, cid, int(parts[3]), int(parts[4]), int(parts[5]), q)
        except (ValueError, IndexError):
            await send_fridge(bot, cid, q)
        return
    # база рецептов
    if data == "as_recipe_save":
        await save_my_recipe(bot, cid); return
    if data == "as_recipe_clean":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "recipes"); return
    if data == "as_my_recipes":
        await send_my_recipes(bot, cid); return
    if data.startswith("as_my_recipe_del_"):
        try:
            await my_recipe_del(bot, cid, int(data.split("_")[-1]))
        except (ValueError, IndexError):
            pass
        return
    if data.startswith("as_my_recipe_"):
        try:
            await send_my_recipe_full(bot, cid, int(data.split("_")[-1]))
        except (ValueError, IndexError):
            pass
        return


# ---------- «Продолжить» / «Ещё раз» ----------
async def retry(bot, cid):
    la = store.last_action.get(str(cid))
    if la and la[0] == "oneshot":
        gen, lbl, cb = _ONESHOT[la[1]]
        await bot.send_message(chat_id=cid, text="Ещё вариант...")
        try:
            out = gen(cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        await _send(bot, cid, out, kb=_ans_kb(lbl, cb)); return
    if la and la[0] == "recipe":
        await send_recipe(bot, cid, la[1]); return
    if la and la[0] == "leftovers":
        await send_leftovers(bot, cid, la[1]); return
    if la and la[0] == "role":
        await handle_role(bot, cid, la[1], la[2]); return
    hist = list(store.chat_history.get(str(cid), []))
    if not hist:
        await bot.send_message(chat_id=cid, text="Нет предыдущего запроса."); return
    if hist[-1]["role"] == "assistant":
        hist = hist[:-1]
    await bot.send_chat_action(chat_id=cid, action="typing")
    nudge = hist + [{"role": "user", "content": "Продолжи мысль или дай более полезный вариант."}]
    try:
        answer = await ai.achat_chain(nudge, cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer, surface="chat")


# ---------- «Короче / Глубже» (переписать последний ответ) ----------
async def reword(bot, cid, mode):
    prev = (store.last_answer.get(str(cid)) or "").strip()
    if not prev:
        await bot.send_message(chat_id=cid, text="Нет ответа, который можно переписать."); return
    surface = store.last_surface.get(str(cid), "card")
    if mode == "short":
        how, tier = "короче и без воды, оставь только суть", "cheap"
    else:
        how, tier = "подробнее и глубже, добавь полезные детали и нюансы", "smart"
    await bot.send_chat_action(chat_id=cid, action="typing")
    prompt = (f"Перепиши этот ответ {how}. Сохрани смысл и тот же язык. "
              "Формат - Telegram HTML: подзаголовки <b>...</b>, пункты с «• », без markdown (без *, #, `).\n\n"
              f"Текст:\n{secure.wrap_untrusted(prev, 'предыдущий ответ')}")
    try:
        out = await ai.allm(prompt, 1200, 0.6, tier=tier)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    await _send(bot, cid, out, surface=surface)
