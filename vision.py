"""Vision-проверка кандидата в фото рецепта через OpenAI (gpt-4o-mini,
image_url по прямой ссылке — без скачивания/base64, см. интервью к задаче).

Используется photo_provider.py для валидации top-кандидатов от Pexels/Unsplash
перед тем, как показать фото пользователю: подтверждает, что на снимке
изображено именно готовое блюдо, а не сырые ингредиенты/интерьер/шеф/другое блюдо.

Синхронный вызов (requests), по аналогии с ai.py/pexels.py/unsplash.py —
вызывающая сторона сама оборачивает в asyncio.to_thread.

Деградация: при отсутствии ключа, сетевой ошибке, таймауте, не-200 ответе или
невалидном JSON — функция возвращает None (кандидат не может быть подтверждён;
photo_provider трактует это как непройденную проверку, не как исключение).
"""
import json
import logging
import re

import requests

import config
import secure

_log = logging.getLogger(__name__)

_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_TIMEOUT = 20  # секунд — vision-модели отвечают дольше текстовых
_MAX_TOKENS = 300

_PROMPT_TEMPLATE = """You are validating an image for a recipe card.

Recipe:
- Name: {name_en}
- Cuisine: {cuisine}
- Meal type: {meal_type}
- Main ingredients: {main_ingredients}
- Required visual traits: {visual_tags}
- Must not show: {negative_visual_tags}

Does the image depict the prepared dish described above, not merely ingredients, a restaurant interior, a chef, or unrelated food?

Return JSON only:
{{
  "match_score": 0,
  "is_prepared_dish": false,
  "contains_main_ingredients": false,
  "has_unrelated_subject": false,
  "reason": ""
}}"""


def _build_prompt(recipe: dict, meal_type: str) -> str:
    return _PROMPT_TEMPLATE.format(
        name_en=recipe.get("name_en") or recipe.get("name") or "",
        cuisine=recipe.get("cuisine") or "",
        meal_type=meal_type or "",
        main_ingredients=recipe.get("main_ingredients_en") or recipe.get("ingredients") or "",
        visual_tags=", ".join(recipe.get("visual_tags") or []),
        negative_visual_tags=", ".join(recipe.get("negative_visual_tags") or []),
    )


def _parse_response(text: str) -> dict | None:
    raw = re.sub(r"```(json)?", "", text or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "match_score": int(data.get("match_score") or 0),
        "is_prepared_dish": bool(data.get("is_prepared_dish")),
        "contains_main_ingredients": bool(data.get("contains_main_ingredients")),
        "has_unrelated_subject": bool(data.get("has_unrelated_subject")),
        "reason": str(data.get("reason") or "")[:300],
    }


def validate_dish_photo(image_url: str, recipe: dict, meal_type: str = "") -> dict | None:
    """Просит OpenAI gpt-4o-mini подтвердить, что image_url — фото готового блюда
    по описанию recipe. Возвращает dict со match_score/is_prepared_dish/... или
    None при любой ошибке/деградации (нет ключа, сеть, невалидный ответ)."""
    if not config.OPENAI_API_KEY or not (image_url or "").strip():
        return None
    prompt = _build_prompt(recipe, meal_type)
    try:
        r = requests.post(
            _CHAT_URL,
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": config.OPENAI_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }],
                "max_tokens": _MAX_TOKENS,
                "temperature": 0,
            },
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            _log.warning("vision validate failed: HTTP %s", r.status_code)
            return None
        content = r.json()["choices"][0]["message"]["content"]
        return _parse_response(content)
    except Exception as e:
        _log.warning("vision request error: %s", secure.redact(str(e)))
        return None
