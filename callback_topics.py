"""Единая классификация callback-действий для ожидания и трекинга."""

# Порядок важен: более конкретные главные экраны перечислены до общих префиксов.
STATUS_TOPIC_PREFIXES = (
    ("m_myday", "myday"), ("a_plany", "myday"), ("weather_myday", "myday"),
    ("m_wardrobe", "wardrobe"), ("w_", "wardrobe"),
    ("m_food", "food"), ("as_food", "food"), ("as_fridge", "food"),
    ("as_recipe", "food"), ("a_recipe_", "food"), ("food_", "food"),
    ("notify_learning", "learning"),
    ("a_dict", "learning"), ("a_train", "learning"), ("a_tr_", "learning"),
    ("ex_", "learning"), ("again_tr_", "learning"), ("game", "learning"),
    ("a_game", "learning"), ("gamediff_", "learning"),
    ("m_leisure", "leisure"), ("lz_", "leisure"), ("vg_", "leisure"),
    ("movie_", "leisure"), ("book_", "leisure"), ("music_", "leisure"),
    ("listen", "leisure"), ("a_watch", "leisure"), ("a_read", "leisure"),
    ("a_listen", "leisure"), ("a_concerts", "leisure"),
    ("ans_", "assistant"), ("chat_retry", "assistant"),
)

# Только эти главные экраны могут запустить долгую работу и безопасно блокируются
# на несколько секунд при повторном нажатии той же inline-кнопки.
LONG_HOME_CALLBACKS = frozenset({
    "m_myday", "m_wardrobe", "m_food",
})


def status_topic(data: str) -> str | None:
    for prefix, topic in STATUS_TOPIC_PREFIXES:
        if data.startswith(prefix):
            return topic
    return None
