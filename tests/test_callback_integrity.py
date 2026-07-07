"""Callback integrity: реальные keyboard factories → dry-run роутера bot.py.

Два уровня проверки (см. docs/audit-cleanup-plan.md, PR1):
  A) собрать реальные callback_data из публичных keyboard factories проекта;
  B) прогнать каждый через routing.resolve_callback_handler — структурный
     (AST-based) резолвер, который проходит дерево if/elif в bot.py и
     под-роутерах в том же порядке, что и реальный код, НЕ исполняя ни один
     handler (никаких сетевых вызовов, LLM, Postgres).

Статический regex-аудит (verify.audit_callbacks) остаётся вспомогательным
линтером для быстрой локальной проверки — источником истины здесь выступает
routing.resolve_callback_handler.
"""
import asyncio

import pytest
from telegram import InlineKeyboardMarkup

import cleanup
import config
import routing
import store
import travel
import wardrobe
from leisure import _book_kb, _listen_kb, _movie_home_kb, _movie_kb
from balance import _fridge_recipe_kb, _recipe_kb, _recipe_typed_kb
from wardrobe import _look_result_kb, _wardrobe_home_kb


def _extract_callback_data(markup):
    """Рекурсивно достаёт все callback_data из InlineKeyboardMarkup."""
    assert isinstance(markup, InlineKeyboardMarkup)
    out = []
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data is not None:
                out.append(button.callback_data)
    return out


# Публичные keyboard factories без сетевых/LLM зависимостей — вызываются напрямую
# с синтетическими аргументами. Это НЕ исчерпывающий список всех клавиатур
# проекта — это обязательный минимум, зафиксированный в PR1. Расширять по мере
# добавления новых экранов.
_SYNC_KEYBOARD_FACTORIES = [
    ("travel._home_kb", travel._home_kb, ()),
    ("travel._travel_kb", travel._travel_kb, ()),
    ("leisure._movie_kb", _movie_kb, (0,)),
    ("leisure._movie_home_kb", _movie_home_kb, ()),
    ("leisure._book_kb", _book_kb, (0,)),
    ("leisure._listen_kb", _listen_kb, ()),
    ("wardrobe._wardrobe_home_kb", _wardrobe_home_kb, ()),
    ("wardrobe._look_result_kb", _look_result_kb, ()),
    ("wardrobe.closet_kb", wardrobe.closet_kb, ()),
    ("balance._recipe_kb", _recipe_kb, ()),
    ("balance._recipe_typed_kb", _recipe_typed_kb, ()),
    ("balance._fridge_recipe_kb", _fridge_recipe_kb, ()),
]


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})


CID = "callback-integrity-cid"


def _collect_all_callbacks():
    """Уровень A: собирает (label, callback_data) из всех зарегистрированных
    keyboard factories — синхронных вызовом напрямую, cleanup.py через dry-run
    send_cleanup с фейковым ботом (без сети, только чтение store)."""
    collected = []
    for label, factory, args in _SYNC_KEYBOARD_FACTORIES:
        markup = factory(*args)
        for cb in _extract_callback_data(markup):
            collected.append((label, cb))

    # cleanup.py: ни send_cleanup, ни open_view не делают сетевых вызовов,
    # только store.get_list — прогоняем по паре контекстов с прогнозируемо
    # пустыми/минимальными данными.
    bot = _FakeBot()
    # "nb" (Сохранённое) с PR3a переведён на view-режим (короткий callback_data
    # через open_view) — это и есть реальный путь пользователя, send_cleanup для
    # nb больше не вызывается из open_cleanup.
    start = len(bot.messages)
    asyncio.run(cleanup.open_view(bot, CID, "nb"))
    for msg in bot.messages[start:]:
        markup = msg.get("reply_markup")
        if markup is not None:
            for cb in _extract_callback_data(markup):
                collected.append(("cleanup.open_view:nb", cb))

    for ctx in ("fridge", "recipes", "lagom"):
        start = len(bot.messages)
        asyncio.run(cleanup.send_cleanup(bot, CID, ctx, 0))
        for msg in bot.messages[start:]:
            markup = msg.get("reply_markup")
            if markup is not None:
                for cb in _extract_callback_data(markup):
                    collected.append((f"cleanup.send_cleanup:{ctx}", cb))

    # wardrobe.send_looks/send_improve с ПУСТЫМ гардеробом — ветка, которая не
    # уходит в LLM, а сразу шлёт клавиатуру "добавь вещи" (это и есть сценарий
    # P0-1 из плана — regression-guard именно на эту кнопку).
    store.reset_wardrobe(CID)
    for label, coro_factory in (
        ("wardrobe.send_looks(empty)", lambda: wardrobe.send_looks(bot, CID)),
        ("wardrobe.send_improve(empty)", lambda: wardrobe.send_improve(bot, CID)),
    ):
        start = len(bot.messages)
        asyncio.run(coro_factory())
        for msg in bot.messages[start:]:
            markup = msg.get("reply_markup")
            if markup is not None:
                for cb in _extract_callback_data(markup):
                    collected.append((label, cb))
    return collected


@pytest.mark.integration
def test_all_keyboard_callbacks_resolve_to_handler():
    """Каждый callback_data, который реально показывает кнопка, обязан дойти
    до конкретного handler'а (bot.py или под-роутер). Падает на первом же
    orphan-callback с указанием, откуда он взялся и куда не дошёл."""
    collected = _collect_all_callbacks()
    assert collected, "не собрано ни одного callback_data — фикстура тестов сломана"

    failures = []
    for label, cb in collected:
        result = routing.resolve_callback_handler(cb)
        if not result["handled"]:
            failures.append(f"{label}: callback_data={cb!r} → {result['detail']} (module={result['module']})")

    assert not failures, "Кнопки без обработчика:\n" + "\n".join(failures)


@pytest.mark.integration
def test_callback_data_within_telegram_limit():
    """Telegram Bot API: callback_data — 1..64 байта в UTF-8."""
    collected = _collect_all_callbacks()
    assert collected

    oversized = []
    for label, cb in collected:
        n = len(cb.encode("utf-8"))
        if not (1 <= n <= 64):
            oversized.append(f"{label}: callback_data={cb!r} ({n} bytes)")

    assert not oversized, "callback_data вне лимита Telegram (1-64 байта):\n" + "\n".join(oversized)


@pytest.mark.unit
def test_unknown_callback_is_not_silently_handled():
    """Заведомо не существующий callback не должен считаться обработанным —
    защита от слишком широких prefix-правил в резолвере."""
    result = routing.resolve_callback_handler("this_callback_does_not_exist_anywhere")
    assert result["handled"] is False


# ---------- Allowlist: known orphan handlers (не удаляются в PR1, см. план) ----------
#
# Handler существует и реально обрабатывает callback_data, но ни одна известная
# keyboard factory его не генерирует. Это НЕ баг видимой кнопки (для этого есть
# test_all_keyboard_callbacks_resolve_to_handler выше) — это кандидаты на будущую
# чистку (PR2/PR5), которые не удаляются автоматически по этому тесту.
#
# Каждая запись обязана иметь причину — почему он остаётся (compatibility alias,
# ждёт продуктового решения, часть ещё не завершённого PR).
_ORPHAN_HANDLER_ALLOWLIST = {
    "as_lovedel_0": "заменено as_loveclean_* → cleanup.py (lv_*); удаляется в PR5 (P3-1)",
    "as_notedel_0": "заменено fav_viewg_* → fav_view; удаляется в PR5 (P3-1)",
    "set_love": "заменено as_love_movies/countries/artists/books; удаляется в PR5 (P3-1)",
    "set_notiftest_x": "заменено set_admin_runjob_*; удаляется в PR5 (P3-1)",
    "setdel_lagom_0": "заменено set_lagom_clean → cleanup.py (lagom); удаляется в PR5 (P3-1)",
    "setdel_country_0": "legacy-путь, ждёт единого маршрута PR2 (P1-2) — не удаляется автоматически",
    "setdel_artist_0": "legacy-путь, ждёт единого маршрута PR2 (P1-2) — не удаляется автоматически",
    "setdel_book_0": "legacy-путь, ждёт единого маршрута PR2 (P1-2) — не удаляется автоматически",
    "w_closet": "дублирует w_del (тот же вызов send_del_zones(origin='m')); удаляется в PR5 (P3-1)",
    "w_show": "не подключено ни к одной кнопке; удаляется в PR5 (P3-1)",
    "md_refresh": "весь md_-роутинг в myday.py недостижим; удаляется в PR5 (P3-1)",
    "md_worrycheck": "весь md_-роутинг в myday.py недостижим; удаляется в PR5 (P3-1)",
    "ob_done": "заменено ob_prio_done; удаляется в PR5 (P3-1)",
}


@pytest.mark.unit
def test_known_orphan_handlers_are_allowlisted_with_reason():
    """Каждый известный orphan-handler зафиксирован в allowlist с причиной — не
    для блокировки, а чтобы будущие изменения не добавляли НОВЫХ необнаруженных
    orphan-путей незаметно. Если этот тест падает на записи из allowlist —
    значит handler уже удалили (можно убрать запись) или переименовали (нужно
    актуализировать план чистки)."""
    for cb, reason in _ORPHAN_HANDLER_ALLOWLIST.items():
        assert reason, f"{cb}: allowlist-запись обязана содержать причину"
        result = routing.resolve_callback_handler(cb)
        # Handler должен существовать (иначе запись устарела и её нужно убрать
        # из allowlist руками, с проверкой, что он не всплыл заново как P0-баг).
        assert result["handled"] is True, (
            f"{cb}: ожидался известный orphan-handler ({reason}), "
            f"но резолвер говорит {result} — actualize docs/audit-cleanup-plan.md"
        )


@pytest.mark.unit
def test_as_worryreview_handler_unreachable_by_routing():
    """`as_worryreview` — отдельный, более серьёзный случай, чем обычный
    orphan-handler: код реально существует в balance.py (`if data ==
    "as_worryreview"`), но верхнеуровневый роутер bot.py никогда не доставит
    туда этот callback — префикс "as_worryreview" не входит в список
    ("as_food", "as_fridge", "as_recipe", "as_my_recipe", "as_daycheck",
    "as_motiv", "as_doctor"), поэтому уходит в settings.handle_notes_callback,
    где ветки для него нет. Зафиксировано отдельно от allowlist orphan-handler'ов
    (там handler физически достижим, просто без кнопки — здесь недостижим сам
    маршрут). См. docs/audit-cleanup-plan.md, PR1 orphan-таблица."""
    result = routing.resolve_callback_handler("as_worryreview")
    assert result == {
        "handled": False,
        "module": "settings.py:handle_notes_callback",
        "detail": "reached sub-router but no matching branch inside it",
    }
