import config
import store
import ai
from util import send_long

def build_outfit_focus(weather_text, day_label):
    w = store.load_wardrobe()
    prompt = f"""Ты персональный стилист Дмитрия, не генератор случайных комплектов.

{config.STYLE_PROFILE}

Погода ({day_label}):
{weather_text}

Параметры: 179 см, ~65 кг, обувь 42.5, джинсы W31 L31.
Гардероб (используй ТОЛЬКО эти вещи):
{store.wardrobe_to_text(w)}

Температурные зоны:{config.TEMP_ZONES}

Учитывай погоду, ветер (для Нидерландов критично), велосипед/прогулки, цветовые сочетания, тренды 2026.
JSON:
{{
 "outfit": ["вещь 1","вещь 2","вещь 3","вещь 4"],
 "why": "1-2 предложения почему работает",
 "focus": "один короткий конкретный совет на день с учётом СДВГ"
}}"""
    return ai.llm_json(prompt, 800)

def generate_look():
    w = store.load_wardrobe()
    prompt = f"""Ты стилист Дмитрия.
{config.STYLE_PROFILE}
Гардероб:
{store.wardrobe_to_text(w)}

Собери 2-3 самые интересные комбинации из этих вещей. Каждая - вещи через запятую, и в конце короткая шутка-вердикт с эмодзи, куда зайдёт образ.
Без markdown и звёздочек. Заголовок: ✨ Луки дня."""
    return ai.llm(prompt, 900, 0.9)

def wardrobe_analysis():
    w = store.load_wardrobe()
    prompt = f"""Ты стилист с прямым, дерзким тоном.
{config.STYLE_PROFILE}
Гардероб:
{store.wardrobe_to_text(w)}

Разбери по назначению (что для чего): повседневная, домашняя, спортивная, деловая, праздничная.
Отметь, что устарело или дублируется (с дерзким юмором) и что стоит докупить (отправь в «Советы к покупке»).
Коротко, без markdown и звёздочек. Заголовок: 🧠 Анализ гардероба."""
    return ai.llm(prompt, 1100, 0.8)

def generate_shopping_advice():
    w = store.load_wardrobe()
    prompt = f"""Ты стилист.
{config.STYLE_PROFILE}
Гардероб:
{store.wardrobe_to_text(w)}

Что докупить, чтобы открыть больше сочетаний. Тренды 2026, без брендов. Раздели строго:

🛍️ Что докупить

ВЕРХ
- вещь - почему

НИЗ
- вещь - почему

ОБУВЬ
- вещь - почему

Максимум 2 пункта на раздел. Коротко, без markdown."""
    return ai.llm(prompt, 800, 0.7)

def parse_wardrobe_list(text):
    w = store.load_wardrobe()
    cats = ", ".join(w.keys()) or "футболки, рубашки, свитшоты, верхняя одежда, брюки, обувь, носки, кепки, аксессуары"
    prompt = f"""Разбери список одежды по категориям.
Категории: {cats}. Можно создать новую.
Список:
{text}
Верни JSON: {{"категория": ["вещь"], ...}}. Названия короткие, нижний регистр."""
    return ai.llm_json(prompt, 800)

# --- действия ---
async def send_look(bot, cid):
    await bot.send_message(chat_id=cid, text="Собираю комбинации...")
    await send_long(bot, cid, generate_look())

async def send_list(bot, cid):
    w = store.load_wardrobe()
    await send_long(bot, cid, "📊 Гардероб\n\n" + (store.wardrobe_to_text(w) or "Пусто."))

async def send_analysis(bot, cid):
    await bot.send_message(chat_id=cid, text="Анализирую...")
    await send_long(bot, cid, wardrobe_analysis())

async def send_shop(bot, cid):
    await bot.send_message(chat_id=cid, text="Подбираю...")
    await send_long(bot, cid, generate_shopping_advice())

async def start_add(bot, cid):
    store.add_wardrobe_mode[str(cid)] = True
    await bot.send_message(chat_id=cid,
        text="📤 Отправь список одежды текстом или файлом (.txt). Можно несколько подряд.")

async def ingest(bot, cid, text):
    await bot.send_message(chat_id=cid, text="Разбираю список...")
    try:
        parsed = parse_wardrobe_list(text)
        added = store.merge_wardrobe(parsed)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка разбора: {e}")
        return
    await bot.send_message(chat_id=cid, text=f"Добавил вещей: {added}. Можешь отправить ещё.")
