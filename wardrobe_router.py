"""Telegram callback routing extracted from the wardrobe controller."""

import store
import util
import verify
import wardrobe


async def ingest(bot, cid, text):
    store.add_wardrobe_mode.pop(str(cid), None)
    await wardrobe.add_item(bot, cid, text)


def _settings_style(choice):
    """w_lookst_<casual|classic> → стили группы; w_lookst_<индекс> (старые кнопки) → один стиль; иначе None."""
    import settings
    if choice in ("casual", "classic"):
        return settings.style_group_members(choice)
    return settings.STYLES[int(choice)] if choice.isdigit() and int(choice) < len(settings.STYLES) else None


async def handle_callback(bot, cid, q, data, status=None):
    if data == "w_look" or data.startswith(("w_lookst_", "w_with_")):
        # w_with_<id> — «Собрать образ» из карточки вещи: образ дня с этой вещью.
        anchor_id = data[len("w_with_"):] if data.startswith("w_with_") else None
        choice = data[len("w_lookst_"):] if data.startswith("w_lookst_") else ""
        style = (_settings_style(choice))
        previous = wardrobe._get_cached_look(cid) or {}
        previous_style_tip = (previous.get("look_data") or {}).get("style_tip") or None
        previous_style = (previous.get("look_data") or {}).get("primary_style") or None
        store.clear_wardrobe_daylook(cid)
        owns_status = status is None
        if owns_status:
            status = await util.StatusManager.start_inline(
                q, bot=bot, cid=cid,
                stages=util.StatusManager.TOPIC_STAGES["wardrobe"],
                preserve_message=True,
            )
        try:
            await wardrobe.send_looks(
                bot, cid, status=status,
                previous_item_ids=[] if anchor_id else previous.get("item_ids") or [],
                previous_style_tip=previous_style_tip,
                previous_style=previous_style,
                style=style,
                anchor_id=anchor_id,
            )
        except Exception as error:
            await verify.safe_error(bot, cid, error, back="m_wardrobe")
        finally:
            if owns_status:
                await status.stop(delete=True)
        return
    if data in ("w_closet", "w_del_g"):
        await wardrobe.send_wardrobe_zones(bot, cid, q=q)
        return
    if data == "w_add":
        store.pending_input[str(cid)] = "wardrobe_add"
        await bot.send_message(
            chat_id=cid,
            text=(
                "Опиши её одним сообщением или отправь вещи списком через запятую.\n\n"
                "Пример: Голубая свободная рубашка Uniqlo."
            ),
            reply_markup=wardrobe._back_kb(),
        )
        return
    if data == "w_fill":
        store.pending_input[str(cid)] = "wardrobe_fill"
        await bot.send_message(
            chat_id=cid,
            text="Пришли список всей своей одежды одним сообщением — я сам разложу всё по шкафу.",
            reply_markup=wardrobe._back_kb(),
        )
        return
    if data in ("w_add_ok", "w_add_all", "w_add_edit"):
        await wardrobe.send_wardrobe_zones(bot, cid, q=q)
        return
    if data == "w_search":
        await wardrobe.send_wardrobe_zones(bot, cid, q=q)
        return
    if data.startswith("w_cat_"):
        category_data = data[len("w_cat_"):]
        zone_slug, separator, page_value = category_data.rpartition("_")
        if separator and zone_slug in wardrobe.ZONE_BY_SLUG and page_value.isdigit():
            await wardrobe.send_category(bot, cid, zone_slug, int(page_value), q=q)
        else:
            await wardrobe.send_category(bot, cid, category_data, q=q)
        return
    if data.startswith("w_item_"):
        await wardrobe.send_item_card(bot, cid, data[len("w_item_"):], q=q)
        return
    if data.startswith("w_edit_"):
        await wardrobe.send_item_card(bot, cid, data[len("w_edit_"):], q=q)
        return
    # «Удалить» удаляет вещь сразу, без подтверждения; w_deleteok_ — старые кнопки.
    if data.startswith(("w_delete_", "w_deleteok_", "w_searchdel_")):
        store.remove_wardrobe_items(cid, [data.split("_", 2)[2]])
        await wardrobe.send_wardrobe_zones(bot, cid, q=q)
        return
    if data == "w_del" or data.startswith(("w_del_", "w_delz_", "w_delsc_")):
        await wardrobe.send_wardrobe_zones(bot, cid, q=q)
        return
    if data == "w_improve":
        await wardrobe.send_home(bot, cid, q=q)
        return
    if data.startswith("w_buy") or data == "w_check":
        # «Что докупить» и «Проверить покупку» убраны: старые кнопки из истории чата — на главный экран Гардероба.
        await wardrobe.send_home(bot, cid, q=q)
