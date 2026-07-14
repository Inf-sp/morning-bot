"""Совместимая точка входа раздела «Досуг».

Новая бизнес-логика живёт в leisure_movies, leisure_books, leisure_music и
leisure_concerts. Реэкспорт оставлен для старых импортов вне callback-роутера.
"""

from leisure_movies import *  # noqa: F401,F403
