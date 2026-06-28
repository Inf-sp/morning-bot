# morning-bot · CLAUDE.md

Личный Telegram-бот DM: погода, гардероб, языки (NL/EN), досуг, рецепты, самозабота.
Python 3.11+, `python-telegram-bot 21.6`, Railway + PostgreSQL.

---

## Быстрый старт

```bash
pip install -r requirements.txt
TELEGRAM_TOKEN=... GEMINI_API_KEY=... python bot.py
```

Обязательные ENV: `TELEGRAM_TOKEN`, `GEMINI_API_KEY`.  
Остальные (`ANTHROPIC_API_KEY`, `DATABASE_URL`, `GROQ_API_KEY`, …) — опциональны, дефолты в `config.py`.

---

## Архитектура модулей

| Файл | Роль |
|---|---|
| `bot.py` | Точка входа. Регистрирует хендлеры, джобы расписания, `post_init`. Два роутера: `answer_callback` (inline-кнопки) и `text_router` (текст). |
| `config.py` | ENV-переменные + константы + ключи хранилища. Всё берётся только отсюда. |
| `store.py` | Слой данных: Postgres (psycopg2) с автоматическим откатом в in-memory dict. Таблица `kv (key TEXT, value JSONB)`. |
| `menu.py` | Reply-клавиатура (`MAIN_KB`), инлайн-подменю (`menu_screen`), маппинг `LABEL_TO_KEY`. |
| `settings.py` | Экран `/setup`: город, уведомления, уровень языков, гардероб, словарь, лагом. `handle_callback` для всех `set_*` / `setadd_*` / `setdel_*`. |
| `learning.py` | Языковые фичи: грамматика, тренажёр, слово дня, перевод, игра-детектив, de/het, словарь, уровни. |
| `balance.py` | Самозабота: вопрос врачу, мотивация, дневник тревоги, рецепты, холодильник. |
| `wardrobe.py` | Гардероб: образ дня, разбор шкафа, проверка покупки, управление вещами. |
| `leisure.py` | Досуг: фильмы, книги, музыка, путешествия, концерты. |
| `myday.py` | «Мой день»: сводка погода + образ + слово + цитата + факт. |
| `weather.py` | Open-Meteo: прогноз сегодня/завтра/неделю, штормовые предупреждения. |
| `assistant.py` | Свободный чат: роутит к нужному навыку или LLM-ответу. |
| `ai.py` | Унифицированный клиент LLM (Claude / Gemini / Groq / OpenRouter). Лог расходов. |
| `memory.py` | Профиль пользователя: предпочтения, лагом-принципы, фидбек по гардеробу. |
| `access.py` | Белый список `allowed_cids.json`. Инвайт-коды. `is_owner` / `is_allowed`. |
| `secure.py` | `clamp` (лимит длины + чистка), `injection_flags`, `scan_secrets`, `MAX_DOC_BYTES`. |
| `verify.py` | `safe_error` — центральный обработчик ошибок: логирует traceback, шлёт юзеру общее сообщение. `audit_callbacks` — проверяет что все literal-callback_data обработаны. |
| `onboard.py` | Онбординг нового пользователя (имя → город). |
| `firstvisit.py` | Первичный опрос при входе в раздел (wardrobe / learn / leisure / balance). |
| `cleanup.py` | Интерактивное удаление элементов из списков (вотч-лист, темы, словарь). |
| `skills.py` | Маппинг текстовых команд к навыкам (одношотовые LLM-сценарии). |
| `research.py` | Веб-поиск (Tavily / ZeroEntropy) для обогащения ответов. |
| `rerank.py` | Ранжирование результатов поиска. |
| `util.py` | Мелкие хелперы: `ack_loading`, `esc` (HTML-экранирование). |

---

## Роутинг callback_data

Всё через один хендлер `answer_callback` в `bot.py`. Диспатч по **префиксу**:

| Префикс | Обработчик |
|---|---|
| `ob_` | `onboard.handle_callback` |
| `fav_`, `as_notes*` | `settings.handle_notes_callback` |
| `gm_`, `dh_` | `learning.handle_callback` |
| `as_food*`, `as_fridge*`, `as_recipe*`, `as_daycheck`, `as_motiv`, `as_doctor` | `balance.handle_callback` |
| `w_` | `wardrobe.handle_callback` |
| `md_` | `myday.handle_callback` |
| `set_`, `setadd_`, `setdel_` | `settings.handle_callback` |
| `m_` | `menu.menu_screen` (edit in place) |
| `a_` | действия (learning / leisure / weather / balance) inline в bot.py |
| `lvl_` | `store.set_level` → `learning.send_levels` |
| `train_` | `learning.train_*` |
| `gamelang_`, `gamediff_`, `game_*` | `learning.game_*` |
| `gram_a`, `gram_b` | `learning.grammar_answer` |
| `again_`, `next_gram_`, `rand_gram_` | `learning.*` |
| `clt_`, `clp_`, `cla_`, `cld_` | `cleanup.handle_cleanup` |
| `worddel_`, `topicdel_` | `learning.del_*` |
| `movie_*`, `book_*`, `listen_*`, `reco_` | `leisure.*` |
| `fv_skip_` | `firstvisit.skip` |
| `ans_short`, `ans_deep` | `balance.reword` |
| `worry_clearall`, `chat_retry` | `balance.*` |
| `noop` | игнорируется |

**Правило добавления кнопки:** задай уникальный `callback_data`, добавь ветку в `answer_callback` (или делегируй в нужный модуль). После — запусти `pytest tests/test_contracts.py` (аудит callbacks).

---

## Паттерн хранилища

```python
# Примитивы (все ключи — строки)
_load(key)            # → dict / list из Postgres; fallback → _mem[key]
_save(key, data)      # upsert в Postgres; fallback → _mem[key]

# Per-user хелперы (chat_id всегда str)
store.get_list(key, cid)           # → list
store.add_to_list(key, cid, item)
store.set_list(key, cid, items)
store.get_settings(cid)            # → {"lat", "lon", "city", ...}
store.get_profile(cid)             # → dict произвольных фактов
store.get_level(cid, language)     # → "A2" | "B1" | …
```

**In-memory состояние** (сбрасывается при рестарте):  
`store.pending_input`, `store.game_state`, `store.challenge_state`, `store.train_state`,  
`store.chat_history`, `store.micro_state`, `store.dehet_state`, и др.

`cid` — **всегда `str`** (`cid = str(q.message.chat_id)`). Не передавай int.

---

## Конвенции кода

### Edit vs Send
- Настроечный экран (settings): передавай `q` → `q.message.edit_text(...)`, fallback `bot.send_message`.  
- Новый контент (рецепт, образ, грамматика): всегда `bot.send_message`.  
- Подменю (`m_*`): `q.message.edit_text`, fallback `bot.send_message`.

### Push-джобы
Оборачивай бота в `_NokbBot(context.bot)` — он убирает `reply_markup` из уведомлений.

### Ошибки
- LLM / API ошибки → `await verify.safe_error(bot, cid, e)` (логирует + показывает «⚠️ Что-то пошло не так…»).
- `⏳ …` или `⚠️ …` в начале `str(e)` → показывается пользователю как есть.

### Безопасность
- Пользовательский ввод: `secure.clamp(text)` перед обработкой.
- Секреты: только через `os.environ` в `config.py`. Никаких hardcode.
- `access.is_allowed(cid)` — первая проверка в каждом хендлере.

---

## Расписание (Europe/Amsterdam)

| Время | Джоб |
|---|---|
| 07:30 | Утренняя сводка (погода + слово дня) |
| 08:15 | Погодное предупреждение |
| 09:00 | Лагом-мотивация |
| 11:00 | Грамматика |
| 12:30 | Рецепт дня |
| 14:00 | Дневная разгрузка (тревоги) |
| 19:00 | Вечерняя погода |
| 19:00 вс | Недельный прогноз |
| 10:00 вс | Афиша недели |
| 21:00 | Повторение слов (vocab review) |
| 22:00 | Вечерний чекин |

---

## Команды бота

| Команда | Что делает |
|---|---|
| `/start [code]` | Приветствие или активация по инвайт-коду |
| `/setup` | Настройки |
| `/notes` | Моя база (закладки) |
| `/health` | Статус: env, DB, Weather API, LLM-расходы |
| `/cost` | Сводка LLM-расходов (только owner) |
| `/invite` | Создать инвайт-ссылку (только owner) |
| `/remember <факт>` | Сохранить факт в профиль памяти |

---

## Тесты

```bash
pytest tests/                          # все тесты
pytest tests/test_contracts.py -v      # аудит callbacks + проверка навыков
pytest tests/test_secure.py -v         # безопасность ввода
```

`test_contracts.py::test_no_unhandled_callbacks` — обязательно после добавления кнопок.

---

## Деплой (Railway)

1. Push в `main` → Railway автодеплоит.
2. Логи: Railway Dashboard → Deployments → последний деплой → **View logs**.
3. `post_init` запускается при старте: миграции, аудит callbacks, скан секретов.
4. При рестарте теряется всё из `store.*` in-memory (game_state, pending_input и т.д.) — это нормально.

---

## TODO / Известные проблемы

- [ ] **`set_levels` баг** (2026-06-28): исправлен — `q=q` теперь передаётся, `edit_text` вместо `send_message`. Задеплоить и проверить в Railway-логах.
- [ ] `send_home` не принимает `q` → при «Назад» из любого экрана настроек отправляется новое сообщение вместо редактирования. Некритично, но захламляет чат.
- [ ] `job_live_lang` зарегистрирован в `bot.py`, но нет соответствующего уведомления в `settings.notif_on` — включить или убрать.
- [ ] Тесты `test_imports` и `test_memory` падают (не связано с текущим кодом — проверить окружение).
