"""AI-редактура текста образа до детерминированной проверки фактов."""

import logging

import ai
from wardrobe_model import public_item_name

_log = logging.getLogger(__name__)


async def ai_reframe_look(items, reasons, tip):
    """Переформулирует локальный текст; финальную достоверность проверяет
    ``wardrobe_outfit.validate_outfit_copy`` после этого вызова."""
    item_facts = "\n".join(
        f"- название={public_item_name(item)}; категория={item.get('zone', '')}; "
        f"цвет={item.get('color') or ', '.join(item.get('colors') or []) or 'не указан'}; "
        f"тепло={item.get('warmth') or 'обычные'}; материал={item.get('material') or 'не указан'}; "
        f"длина={item.get('length') or 'не указана'}; посадка={item.get('fit') or 'не указана'}; "
        f"дождь={'да' if item.get('rain_ok') else 'нет'}; ветер={'да' if item.get('wind_ok') else 'нет'}; "
        f"детали=только явно присутствующие в названии"
        for item in items
    )
    prompt = f"""Ты современный персональный стилист. Комплект уже выбран — не меняй и не добавляй вещи.
Вещи:
{item_facts}
Причины (локальные заметки, переформулируй естественно): {"; ".join(reasons) if reasons else "нет"}
Совет по носке: {tip or "нет"}
Дай одно точное объяснение, почему комплект работает, используя ТОЛЬКО заполненные факты выше.
Не упоминай длину, объём, материал, посадку, рукава, карманы, воротник, принт и другие детали,
если конкретное свойство дословно не подтверждено названием или отдельным заполненным полем.
Категория «рубашка» сама по себе НЕ означает длинные или объёмные рукава.
Не выводи служебные season/style/occasion-теги и не добавляй их в скобках.
Совет по носке — одна строка, максимум два действия. Не повторяй полное название вещи.
Обращайся на «ты», без имени и приветствий. Не выдумывай факты, которых нет выше.
Никогда не пиши, что вещь "давно не носили"/"ещё не пробовали"/"пора попробовать".
Верни строго валидный JSON (без markdown): {{"reasons": ["ровно 1 содержательная строка"], "tip": "1 строка или пусто"}}"""
    try:
        data = await ai.allm_json(prompt, 400, tier="cheap", module="wardrobe")
        new_reasons = [str(reason).strip() for reason in (data.get("reasons") or []) if str(reason).strip()]
        new_tip = str(data.get("tip") or "").strip()
        return (new_reasons or reasons)[:1], new_tip or tip
    except Exception as error:
        _log.warning("wardrobe AI reframe failed, using local text: %r", error, exc_info=True)
        return reasons, tip
