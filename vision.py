"""Vision-проверка кандидата в фото рецепта.

Используется photo_provider.py для валидации top-кандидатов от Pexels/Unsplash
перед тем, как показать фото пользователю: подтверждает, что на снимке
изображено именно готовое блюдо, а не сырые ингредиенты/интерьер/шеф/другое блюдо.

OpenAI API намеренно отключён, чтобы бот не делал платных вызовов. Сейчас модуль
сохраняет интерфейс и возвращает None; photo_provider деградирует на локальный
score без внешнего vision-запроса.
"""
import logging

_log = logging.getLogger(__name__)


def is_enabled() -> bool:
    return False


def validate_dish_photo(image_url: str, recipe: dict, meal_type: str = "") -> dict | None:
    """OpenAI vision отключён. Возвращаем None без сетевых вызовов."""
    return None
