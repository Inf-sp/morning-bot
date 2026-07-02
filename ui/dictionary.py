from telegram import MessageEntity

from .builder import MessageBuilder


def _lang_adj(code):
    return "нидерландских" if code == "nl" else "английских"


def _lang_acc(code):
    return "нидерландские" if code == "nl" else "английские"


def _lang_dat(code):
    return "нидерландскому" if code == "nl" else "английскому"


def _kind_word(kind):
    return "фраза" if kind == "phrase" else "слово"


def _kind_bucket(kind):
    return "фраз" if kind == "phrase" else "слов"


def _kind_bucket_acc(kind):
    return "фразы" if kind == "phrase" else "слова"


def _kind_loc(kind):
    return "фразах" if kind == "phrase" else "словах"


def _line(item):
    line = item["word"]
    if item.get("ru"):
        line += f" - {item['ru']}"
    return line


def _added_explanation(item):
    kind = _kind_word(item["kind"])
    pronoun = "эта" if item["kind"] == "phrase" else "это"
    lang = _lang_dat(item["lang"])
    if item.get("ru"):
        return f"Теперь {pronoun} {kind} будет попадаться в тренировках по {lang} с переводом на русский."
    return f"Теперь {pronoun} {kind} будет храниться в словаре и попадаться в тренировках по {lang}."


def dict_add_confirmation(added_items):
    b = MessageBuilder()
    first = added_items[0]
    single = len(added_items) == 1

    b.bold("Словарь").blank()

    if single:
        kind = _kind_word(first["kind"])
        added_form = "добавлена" if first["kind"] == "phrase" else "добавлено"
        b.text_line(
            f"✅ {kind.capitalize()} {added_form} в {_lang_acc(first['lang'])} {_kind_bucket_acc(first['kind'])}"
        )
        b.blank().quote(_line(first)).blank().text_line(_added_explanation(first))
        return b.build()

    counts = {}
    for item in added_items:
        key = (item["lang"], item["kind"])
        counts[key] = counts.get(key, 0) + 1
    summary = []
    for code in ("nl", "en"):
        for kind in ("word", "phrase"):
            n = counts.get((code, kind), 0)
            if n:
                summary.append(f"{n} в словарь {_lang_adj(code)} {_kind_bucket(kind)}")
    b.add("✅ Добавлено: " + "; ".join(summary), MessageEntity.BOLD).blank()

    for idx, item in enumerate(added_items[:8]):
        b.quote(_line(item))
        if idx != min(len(added_items), 8) - 1:
            b.newline()
    if len(added_items) > 8:
        b.add(f"\n...и ещё {len(added_items) - 8}")
    b.blank().text_line("Новые записи будут храниться в словаре и попадаться в тренировках по языку.")
    return b.build()


def dict_duplicate_confirmation(duplicate_items):
    b = MessageBuilder()
    first = duplicate_items[0]
    single = len(duplicate_items) == 1

    b.bold("Словарь").blank()

    if single:
        kind = _kind_word(first["kind"])
        b.text_line(f"✅ {kind.capitalize()} уже есть в {_lang_adj(first['lang'])} {_kind_loc(first['kind'])}")
        b.blank().quote(_line(first)).blank()
        b.text_line("Повторно не добавляю, чтобы словарь оставался чистым и тренировки не дублировали одно и то же.")
        return b.build()

    b.text_line("✅ Эти записи уже есть в словаре").blank()
    for idx, item in enumerate(duplicate_items[:8]):
        b.quote(_line(item))
        if idx != min(len(duplicate_items), 8) - 1:
            b.newline()
    if len(duplicate_items) > 8:
        b.add(f"\n...и ещё {len(duplicate_items) - 8}")
    b.blank().text_line("Повторно не добавляю их, чтобы словарь оставался чистым.")
    return b.build()
