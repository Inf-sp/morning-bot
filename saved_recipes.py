"""Сохранённые пользовательские рецепты."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
import util
from recipe_state import bump_cuisine_weight
from ui import food as food_ui

_food_card = lambda data, label="Рецепт": food_ui.food_card(data, label=label)

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
    if d.get("cuisine"):
        bump_cuisine_weight(cid, d["cuisine"], 1)  # обучение на действиях пользователя (§12/§4.4 спеки)
    await bot.send_message(chat_id=cid, text=f"❤️ «{util.esc(d['name'])}» сохранён в базе рецептов.")


async def send_my_recipes(bot, cid, back="as_notes"):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if not recipes:
        msg = food_ui.my_recipes_empty()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]])
    else:
        msg = food_ui.my_recipes_list(recipes)
        rows = []
        for i, r in enumerate(recipes):
            name = r.get("name", f"Рецепт {i+1}")[:30]
            rows.append([InlineKeyboardButton(f"📖 {name}", callback_data=f"as_my_recipe_{i}")])
        rows.insert(0, [InlineKeyboardButton("❌ Удалить", callback_data="as_recipe_clean")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
        kb = InlineKeyboardMarkup(rows)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_my_recipe_full(bot, cid, idx):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if idx >= len(recipes):
        await bot.send_message(chat_id=cid, text="Рецепт не найден."); return
    d = recipes[idx]
    store.last_recipe[cid_s] = d
    card = _food_card(d, label="Рецепт")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить из базы", callback_data=f"as_my_recipe_del_{idx}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="as_my_recipes"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    await bot.send_message(chat_id=cid, text=card.text, entities=card.entities, reply_markup=kb)


async def my_recipe_del(bot, cid, idx):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if idx < len(recipes):
        name = recipes[idx].get("name", "рецепт")
        recipes.pop(idx)
        store.set_list(config.MY_RECIPES_KEY, cid_s, recipes)
        await bot.send_message(chat_id=cid, text=f"❌ «{util.esc(name)}» удалён из базы рецептов.")
    await send_my_recipes(bot, cid)

