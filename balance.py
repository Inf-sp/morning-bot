from datetime import date, datetime
import hashlib
import logging
import config
import store

_log = logging.getLogger(__name__)
import ai
import verify
import secure
import menu
import thoughts
from response_delivery import (
    answer_keyboard as _ans_kb,
    send_response as _send,
)

TZ = config.TZ

# ---------- Фокус на сегодня ----------
_FOCUS_PHRASES = (
    *(('emotion', text) for text in (
        "Это раздражение, не угроза.", "Пауза - победа.", "Чужие эмоции - не моя ответственность.",
        "Остановись. Выдохни. Потом действуй.", "Это состояние пройдёт.",
        "Представь, что все чудаки.", "Нежелательная мысль → «Отмена» три раза.",
    )),
    *(('action', text) for text in (
        "Сейчас не вся жизнь. Сейчас один шаг.", "Мне не нужно идеально. Мне нужно начать.",
        "Я не ленивый. Мой мозг так работает.", "Я делаю лучшее из возможного сегодня.",
        "От чего наполняешься - то и монетизируй.", "Риск важнее идеала.",
        "Скука - мой криптонит. Создаю интерес сам.", "Скромности мало. Мир продвигает видимых.",
        "Требовать своих прав - здоровое, не наглость.", "Сделано лучше идеального. Закрой и выложи.",
        "Застрял - уменьши шаг, не бросай задачу.", "Действие гасит тревогу быстрее, чем анализ.",
        "Дискомфорт нового = вход в индустрию, а не стоп.",
    )),
    *(('values', text) for text in (
        "Не пропускай зло дальше себя.", "Фокус на хорошем, благодарность за мелочи.",
        "Уважай границы, говори открыто.",
        "Любовь важна, но не единственное. Цени поддержку, создавай воспоминания.",
        "Родители - взрослые. Ты не отвечаешь за их чувства.", "Не все споры стоят нервов.",
        "Перемены открывают возможности.", "Окружение влияет - ищи своё, не терпи.",
        "Книги - радость и рост.", "Путешествия важнее материального.",
        "Избавляйся от лишнего, освобождай место новому.",
        "Баланс работа / отдых / движение - необходим.", "Переключайся, но не убегай.",
    )),
)

_FOCUS_GUIDANCE = {
    "emotion": (
        ("Назови чувство одним словом и сделай три спокойных выдоха.",
         "Отложи ответ на пять минут, если эмоция ещё сильная.",
         "Выбери одно спокойное действие вместо немедленной реакции."),
        "Короткая пауза отделяет чувство от действия и помогает ответить спокойнее.",
    ),
    "action": (
        ("Выбери не больше трёх задач на сегодня.",
         "Начни с одной задачи, которую можно сделать за 5–10 минут.",
         "Отметь готовый шаг и только потом переходи к следующему."),
        "Короткий список снижает перегрузку и помогает быстрее перейти от мыслей к действию.",
    ),
    "values": (
        ("Выбери одно решение, которое сегодня поддержит эту мысль.",
         "Сделай небольшой конкретный шаг до конца дня.",
         "Вечером отметь, что из этого получилось."),
        "Связь принципа с одним действием помогает не оставлять важное только намерением.",
    ),
}

_HEALTH_SCHEDULE = (
    "08:00 · Почисти зубы и выпей стакан воды.",
    "13:00 · Собери обед: овощи, белок и цельные злаки.",
    "18:30 · Сделай пять минут лёгкой гимнастики.",
    "22:30 · Почисти зубы и дай себе время на сон.",
)

_HEALTH_TOPICS = (
    ("Уход за кожей", (
        "Утром нанеси увлажняющий крем и SPF, если выходишь на улицу.",
        "Умывайся мягким средством без скраба каждый день.",
        "Вводи только одно новое средство за раз.",
    )),
    ("Осанка", (
        "Поставь верх экрана на уровень глаз.",
        "Поставь стопы на пол и опирайся спиной на спинку стула.",
        "Раз в час встань, пройдись и разомни плечи две минуты.",
    )),
    ("Питание", (
        "Добавь овощи или фрукты хотя бы к одному приёму пищи.",
        "Выбери бобовые: чечевицу, фасоль или нут — как источник белка и клетчатки.",
        "Сделай гарнир из цельных злаков: овсянки, гречки или цельнозернового хлеба.",
    )),
    ("Движение", (
        "Выбери десять минут ходьбы в удобном темпе.",
        "Сделай по несколько мягких кругов плечами и тазом.",
        "После долгого сидения разомни голеностоп и икры.",
    )),
    ("Сон", (
        "За час до сна убери яркий экран или включи тёплый свет.",
        "Оставь на вечер короткий повторяющийся ритуал: душ, книга или музыка.",
        "Не переноси на кровать рабочие задачи и ленту новостей.",
    )),
)


def _focus_index(cid, day):
    digest = hashlib.sha256(str(cid).encode()).digest()
    start = int.from_bytes(digest[:4], "big") % len(_FOCUS_PHRASES)
    return (start + date.fromisoformat(day).toordinal()) % len(_FOCUS_PHRASES)


def _health_topic_index(cid, day):
    digest = hashlib.sha256(f"health-topic:{cid}".encode()).digest()
    start = int.from_bytes(digest[:4], "big") % len(_HEALTH_TOPICS)
    return (start + date.fromisoformat(day).toordinal()) % len(_HEALTH_TOPICS)


def health_focus(cid):
    now = datetime.now(TZ)
    day = now.strftime("%Y-%m-%d")
    kind, phrase = _FOCUS_PHRASES[_focus_index(cid, day)]
    steps, tip = _FOCUS_GUIDANCE[kind]
    theme, tips = _HEALTH_TOPICS[_health_topic_index(cid, day)]
    return {
        "phrase": phrase,
        "steps": steps,
        "tip": tip,
        "schedule": _HEALTH_SCHEDULE,
        "theme": theme,
        "tips": tips,
    }


async def send_health_focus(bot, cid):
    text, entities, kb = menu.menu_screen("m_balance", cid)
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=kb, transient=True)


async def send_motiv_push(bot, cid):
    """Совместимость старого действия ассистента: открывает новый экран Здоровья."""
    await send_health_focus(bot, cid)


# ---------- роли ----------
def _role_system(role):
    if role == "state":
        return ("Ты спокойный помощник по состоянию, фокусу и мотивации ( психотерапевт). "
                "Выслушай, разложи ситуацию на 1-3 конкретных шага, поддержи коротко. Без воды, с эмодзи. "
        )
    return "Ты полезный ассистент."

async def handle_role(bot, cid, role, text):
    if role == "medicine":
        import medicine
        await medicine.answer(bot, cid, text); return
    if role == "doctor":
        import doctor
        await doctor.answer(bot, cid, text); return
    if secure.is_dangerous_med(text):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health", back="m_balance"); return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        route = "gemini"
        out = await ai.allm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7, route=route)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_balance"); return
    store.last_action[str(cid)] = ("role", role, text)
    cont = ("✨ Ещё совет", "chat_retry") if role == "state" else ("Продолжить", "chat_retry")
    await _send(bot, cid, out, kb=_ans_kb(*cont), surface="chat" if role == "state" else "card")


# ---------- 😮‍💨 Мысли ----------
async def send_daycheck(bot, cid, status=None):
    await thoughts.send_home(bot, cid, status=status)

async def worry_clear_all(bot, cid):
    # Совместимость со старыми Telegram-сообщениями: историческая кнопка
    # «Очистить всё» больше не выполняет массовое удаление.
    await thoughts.send_inbox(bot, cid)

async def save_worries(bot, cid, text):
    await thoughts.capture(bot, cid, text)


# ---------- роутер кнопок Баланса ----------
async def handle_callback(bot, cid, q, data, status=None):
    # мысли
    if data == "as_daycheck":
        await send_daycheck(bot, cid, status=status); return
    # Совместимость со старыми сообщениями с кнопкой «Мотивация».
    if data == "as_motiv":
        await send_health_focus(bot, cid)
        return
    # врач
    if data == "as_medicine":
        # Старые сообщения с отдельной кнопкой остаются рабочими, но теперь
        # ведут в единый сценарий врача.
        import doctor
        await doctor.send_prompt(bot, cid); return
    if data == "as_doctor":
        import doctor
        await doctor.send_prompt(bot, cid); return



# ---------- «Продолжить» / «Ещё раз» ----------
async def retry(bot, cid, status=None):
    la = store.last_action.get(str(cid))
    if la and la[0] == "role":
        await handle_role(bot, cid, la[1], la[2]); return
    hist = list(store.chat_history.get(str(cid), []))
    if not hist:
        await bot.send_message(chat_id=cid, text="Нет предыдущего запроса."); return
    if hist[-1]["role"] == "assistant":
        hist = hist[:-1]
    await bot.send_chat_action(chat_id=cid, action="typing")
    nudge = hist + [{"role": "user", "content": "Продолжи мысль или дай более полезный вариант."}]
    try:
        answer = await ai.achat_chain(nudge, cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_balance"); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer, surface="chat")


# ---------- «Короче / Глубже» (переписать последний ответ) ----------
async def reword(bot, cid, mode):
    prev = (store.last_answer.get(str(cid)) or "").strip()
    if not prev:
        await bot.send_message(chat_id=cid, text="Нет ответа, который можно переписать."); return
    surface = store.last_surface.get(str(cid), "card")
    if mode == "short":
        how, tier = "короче и без воды, оставь только суть", "cheap"
    else:
        how, tier = "подробнее и глубже, добавь полезные детали и нюансы", "smart"
    await bot.send_chat_action(chat_id=cid, action="typing")
    prompt = (f"Перепиши этот ответ {how}. Сохрани смысл и тот же язык. "
              "Формат - Telegram HTML: подзаголовки <b>...</b>, пункты с «• », без markdown (без *, #, `).\n\n"
              f"Текст:\n{secure.wrap_untrusted(prev, 'предыдущий ответ')}")
    try:
        out = await ai.allm(prompt, 1200, 0.6, tier=tier)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_balance"); return
    await _send(bot, cid, out, surface=surface)
