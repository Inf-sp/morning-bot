"""Telegram callback routing extracted from the wardrobe controller."""


async def ingest(bot, cid, text):
    store.add_wardrobe_mode.pop(str(cid), None)
    await add_item(bot, cid, text)


async def handle_callback(bot, cid, q, data, status=None):
    if data == "w_look":
        previous = _get_cached_look(cid) or {}
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
            await send_looks(
                bot, cid, status=status,
                previous_item_ids=previous.get("item_ids") or [],
                previous_style_tip=previous_style_tip,
                previous_style=previous_style,
            )
        except Exception as error:
            await verify.safe_error(bot, cid, error, back="m_wardrobe")
        finally:
            if owns_status:
                await status.stop(delete=True)
        return
    if data in ("w_closet", "w_del_g"):
        await send_wardrobe_zones(bot, cid, q=q)
        return
    if data == "w_add":
        store.pending_input[str(cid)] = "wardrobe_add"
        await bot.send_message(
            chat_id=cid,
            text=(
                "Опиши её одним сообщением или отправь вещи списком через запятую.\n\n"
                "Пример: Голубая свободная рубашка Uniqlo."
            ),
            reply_markup=_back_kb(),
        )
        return
    if data == "w_fill":
        store.pending_input[str(cid)] = "wardrobe_fill"
        await bot.send_message(
            chat_id=cid,
            text="Пришли список всей своей одежды одним сообщением — я сам разложу всё по шкафу.",
            reply_markup=_back_kb(),
        )
        return
    if data in ("w_add_ok", "w_add_all", "w_add_edit"):
        await send_wardrobe_zones(bot, cid, q=q)
        return
    if data == "w_search":
        await send_wardrobe_zones(bot, cid, q=q)
        return
    if data.startswith("w_searchdel_"):
        await send_delete_confirmation(bot, cid, data[len("w_searchdel_"):], q=q)
        return
    if data.startswith("w_cat_"):
        category_data = data[len("w_cat_"):]
        zone_slug, separator, page_value = category_data.rpartition("_")
        if separator and zone_slug in ZONE_BY_SLUG and page_value.isdigit():
            await send_category(bot, cid, zone_slug, int(page_value), q=q)
        else:
            await send_category(bot, cid, category_data, q=q)
        return
    if data.startswith("w_item_"):
        await send_item_card(bot, cid, data[len("w_item_"):], q=q)
        return
    if data.startswith("w_edit_"):
        await send_item_card(bot, cid, data[len("w_edit_"):], q=q)
        return
    if data.startswith("w_deleteok_"):
        item_id = data[len("w_deleteok_"):]
        store.remove_wardrobe_items(cid, [item_id])
        await send_wardrobe_zones(bot, cid, q=q)
        return
    if data.startswith("w_delete_"):
        await send_delete_confirmation(bot, cid, data[len("w_delete_"):], q=q)
        return
    if data == "w_del" or data.startswith(("w_del_", "w_delz_", "w_delsc_")):
        await send_wardrobe_zones(bot, cid, q=q)
        return
    if data == "w_improve":
        await send_home(bot, cid, q=q)
        return
    if data == "w_buy":
        await recommend_missing_purchase(bot, cid)
        return
    if data.startswith("w_buy_page:"):
        page = data.partition(":")[2]
        await show_purchase_page(bot, cid, int(page) if page.isdigit() else 0, q=q)
        return
    if data == "w_buy_new" or data.startswith("w_buy_new:"):
        page = data.partition(":")[2]
        await recommend_another_purchase(
            bot, cid, q=q, page=int(page) if page.isdigit() else None,
        )
        return
    if data == "w_buy_pick":
        store.pending_input[str(cid)] = "wardrobe_buy"
        await bot.send_message(
            chat_id=cid,
            text="Что ищем? Например: «худи», «зелёная худи» или «ботинки на осень».",
            reply_markup=_kb([[("⬅️ Назад", "w_buy"), ("#️⃣ Главная", "m_menu")]]),
        )
        return
    if data == "w_buy_gap":
        await recommend_missing_purchase(bot, cid)
        return
    if data == "w_check":
        await send_purchase_hub(bot, cid)
