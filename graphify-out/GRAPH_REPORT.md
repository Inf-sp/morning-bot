# Graph Report - .  (2026-06-25)

## Corpus Check
- 154 files · ~65,858 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 509 nodes · 1025 edges · 28 communities (26 shown, 2 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 23 edges (avg confidence: 0.82)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Leisure Content (moviesbooksmusic)|Leisure Content (movies/books/music)]]
- [[_COMMUNITY_Security & ECC Config|Security & ECC Config]]
- [[_COMMUNITY_My Day Summary & Formatting|My Day Summary & Formatting]]
- [[_COMMUNITY_Notes & Favorites|Notes & Favorites]]
- [[_COMMUNITY_Balance (healthfooddoctor)|Balance (health/food/doctor)]]
- [[_COMMUNITY_Weather & Cache|Weather & Cache]]
- [[_COMMUNITY_LLM Provider Layer|LLM Provider Layer]]
- [[_COMMUNITY_Storage & Config Seeds|Storage & Config Seeds]]
- [[_COMMUNITY_Settings (setup)|Settings (/setup)]]
- [[_COMMUNITY_Bot Dispatcher & Menu|Bot Dispatcher & Menu]]
- [[_COMMUNITY_Learning Translation & Topics|Learning: Translation & Topics]]
- [[_COMMUNITY_Wardrobe|Wardrobe]]
- [[_COMMUNITY_Chat & Verification Surfaces|Chat & Verification Surfaces]]
- [[_COMMUNITY_Learning List Cleanup|Learning: List Cleanup]]
- [[_COMMUNITY_Learning Detective Game|Learning: Detective Game]]
- [[_COMMUNITY_Learning Dictionary Import|Learning: Dictionary Import]]
- [[_COMMUNITY_Travel|Travel]]
- [[_COMMUNITY_Learning Dictionary Management|Learning: Dictionary Management]]
- [[_COMMUNITY_Learning Grammar Trainer|Learning: Grammar Trainer]]
- [[_COMMUNITY_Grader Unit Tests|Grader Unit Tests]]
- [[_COMMUNITY_Verification Graders|Verification Graders]]
- [[_COMMUNITY_Learning Word Trainer|Learning: Word Trainer]]
- [[_COMMUNITY_Railway Deploy Config|Railway Deploy Config]]
- [[_COMMUNITY_Skills Registry|Skills Registry]]
- [[_COMMUNITY_Contract Tests|Contract Tests]]
- [[_COMMUNITY_List Cleanup Engine|List Cleanup Engine]]
- [[_COMMUNITY_Safe Send & HTML Grader|Safe Send & HTML Grader]]
- [[_COMMUNITY_Test Fixtures|Test Fixtures]]

## God Nodes (most connected - your core abstractions)
1. `esc()` - 39 edges
2. `_flag()` - 13 edges
3. `handle_callback()` - 13 edges
4. `send_weather()` - 13 edges
5. `_code()` - 12 edges
6. `_ctx_items()` - 12 edges
7. `ECC common rules layer` - 12 edges
8. `llm()` - 11 edges
9. `send_grammar()` - 11 edges
10. `_study_words()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `Input clamp (length + invisible chars)` --implements--> `clamp()`  [INFERRED]
  CLAUDE.md → secure.py
- `Untrusted-text wrapping for prompts` --implements--> `wrap_untrusted()`  [INFERRED]
  CLAUDE.md → secure.py
- `Secret redaction in logs` --implements--> `redact()`  [INFERRED]
  CLAUDE.md → secure.py
- `send_daycheck()` --calls--> `esc()`  [EXTRACTED]
  balance.py → util.py
- `send_evening_review()` --calls--> `esc()`  [EXTRACTED]
  balance.py → util.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Verification surfaces** — surface_chat, surface_health, surface_card, surface_weather [EXTRACTED 1.00]
- **ECC standard topics** — ecc_coding_style, ecc_security, ecc_testing, ecc_patterns, ecc_hooks [EXTRACTED 1.00]

## Communities (28 total, 2 thin omitted)

### Community 0 - "Leisure Content (movies/books/music)"
Cohesion: 0.07
Nodes (55): add_fav(), add_listen(), add_reco(), _add_unique(), _book_cover(), book_dislike(), _book_kb(), book_love() (+47 more)

### Community 1 - "Security & ECC Config"
Cohesion: 0.06
Nodes (39): AgentShield security layer, ECC layered configuration (.claude/), Agents guidance, Code review checklist, Coding style standards, ECC common rules layer, Git workflow, Tooling hooks (formatters/linters) (+31 more)

### Community 2 - "My Day Summary & Formatting"
Cohesion: 0.07
Nodes (33): Telegram-HTML escaping convention, Shared TTL weather cache, _build_day_text(), _cap(), city_fact(), _clean_wiki(), _day_menu_kb(), ensure_lagom() (+25 more)

### Community 3 - "Notes & Favorites"
Cohesion: 0.15
Nodes (31): Reusable list-cleanup engine, export_notes(), handle_callback(), love_add_done(), love_add_start(), love_delete(), _love_items(), _love_key_of() (+23 more)

### Community 4 - "Balance (health/food/doctor)"
Cohesion: 0.16
Nodes (23): _ans_kb(), _back_kb(), doctor_answer(), _doctor_candidates(), _gen_recipe(), handle_callback(), handle_role(), _is_med_question() (+15 more)

### Community 5 - "Weather & Cache"
Cohesion: 0.12
Nodes (21): _FakeResp, fetch_weather кеширует ответ в пределах TTL - второй вызов не ходит в сеть., test_fetch_weather_expired(), fetch_current_temp(), fetch_weather(), _joke_outfit(), location_handler(), _meteo_fact() (+13 more)

### Community 6 - "LLM Provider Layer"
Cohesion: 0.15
Nodes (20): _as_text(), _chat(), chat_chain(), _friendly(), _gen_cf(), _gen_claude(), _gen_gemini(), _gen_groq() (+12 more)

### Community 7 - "Storage & Config Seeds"
Cohesion: 0.17
Nodes (20): _load_json(), _load_lagom_items(), myday_rules(), place_hint(), Подсказка о локации для генерации фактов - зависит от выбранной страны., JSON seed files for lists, Postgres KV with in-memory fallback, add_to_list() (+12 more)

### Community 8 - "Settings (/setup)"
Cohesion: 0.20
Nodes (21): _all(), get(), home_kb(), list_add_done(), list_delete(), _list_kb(), notif_on(), _preload_books() (+13 more)

### Community 9 - "Bot Dispatcher & Menu"
Cohesion: 0.13
Nodes (14): answer_callback(), handle_settings(), job_checkin_day(), job_checkin_evening(), job_grammar(), main(), post_init(), DEFAULT_TYPE (+6 more)

### Community 10 - "Learning: Translation & Topics"
Cohesion: 0.15
Nodes (18): _add_one_topic(), add_topic(), check_translation(), _code(), grammar_answer(), _proverb_kb(), Дробит сообщение на отдельные темы по строкам/«;», убирая маркеры списка.     Те, Сохраняет одну тему и показывает грамматический разбор. (+10 more)

### Community 11 - "Wardrobe"
Cohesion: 0.27
Nodes (16): add_item(), _back_kb(), check_purchase(), closet_kb(), del_item(), handle_callback(), home_kb(), ingest() (+8 more)

### Community 12 - "Chat & Verification Surfaces"
Cohesion: 0.15
Nodes (11): Card surface grader (HTML only), Chat surface grader (≤1 emoji + HTML), Health surface grader (disclaimer + HTML), Weather surface grader (umbrella warning), audit_callbacks(), Verification-слой: грейдеры качества ответов + безопасная отправка и обработка о, Полную ошибку - в логи, пользователю - нейтральный текст. Никогда не показываем, Best-effort: каждый ЛИТЕРАЛЬНЫЙ callback_data должен где-то обрабатываться. (+3 more)

### Community 13 - "Learning: List Cleanup"
Cohesion: 0.22
Nodes (14): _cleanup_delete(), _ctx_items(), del_topic(), get_topics(), handle_cleanup(), _list_label(), Подпись для элемента простого списка (строка или {name})., (заголовок, items=[(global_id, label)], back_callback) для контекста чистки. (+6 more)

### Community 14 - "Learning: Detective Game"
Cohesion: 0.22
Nodes (10): do_translate(), _dot(), _fuzzy(), game_answer(), game_data(), game_lang_kb(), game_start(), generate_challenge() (+2 more)

### Community 15 - "Learning: Dictionary Import"
Cohesion: 0.15
Nodes (13): add_words_batch(), _cap(), _kind_of(), migrate_dict_caps(), _parse_batch(), Убирает маркеры списка и отделяет перевод, если он на той же строке (через - – —, Первая буква термина - заглавная (с учётом орфографии), остальное не трогаем., Разовая миграция: приводит уже сохранённые слова словаря к виду с заглавной букв (+5 more)

### Community 16 - "Travel"
Cohesion: 0.26
Nodes (12): _country_card(), _plan_countries(), Страны из уже сохранённых планов поездок (вкладка «Планы»)., Подробный план поездки по текущей предложенной стране., Сохранить предложенную страну в закладки и сразу показать следующую., save_plan(), send_go(), send_plan() (+4 more)

### Community 17 - "Learning: Dictionary Management"
Cohesion: 0.24
Nodes (12): del_word(), _dict_counts(), _dict_kind(), _dict_lang(), _ensure_dict(), Возвращает словарь; если пусто - подгружает дефолтные NL-слова из dict_nl.json., 11:00 - Daily Words: метод дня недели + порция (3 слова + 2 фразы) из словаря., До 8 слов нужного языка из словаря - для примеров грамматики/тренажёра. (+4 more)

### Community 18 - "Learning: Grammar Trainer"
Cohesion: 0.20
Nodes (11): _adj(), again_grammar(), grammar_data(), _is_b1plus(), next_grammar(), random_grammar(), Следующая тема: полностью новая грамматика с объяснением., Случайная тема: новая грамматика уровня, игнорируя список изучаемых тем. (+3 more)

### Community 20 - "Verification Graders"
Cohesion: 0.20
Nodes (10): _apply_graders(), grade_disclaimer(), grade_emoji(), grade_text(), grade_umbrella(), Не больше max_n эмодзи. Лишние кластеры убираем, оставляя первый. -> (text, warn, Медицинский ответ обязан содержать дисклеймер; если нет - дописываем. -> (text,, Предупреждаем, если упомянут зонт, а дождя по сути нет. Текст НЕ меняем. -> (tex (+2 more)

### Community 21 - "Learning: Word Trainer"
Cohesion: 0.32
Nodes (8): _flag(), Слова (kind=word) нужного языка из словаря: [(word, ru), ...]., Задание тренажёра вокруг слова `word` (перевод `ru`) в формате fmt (gap/tf)., _render_train(), train_data(), train_next(), train_start(), _train_words()

### Community 22 - "Railway Deploy Config"
Cohesion: 0.29
Nodes (6): build, builder, deploy, restartPolicyType, startCommand, $schema

### Community 23 - "Skills Registry"
Cohesion: 0.33
Nodes (4): Declarative skills contract registry, Surface-bound verification graders, Реестр Skills-контрактов бота (ECC: skills как primary workflow surface).  Лёгки, Skill

### Community 24 - "Contract Tests"
Cohesion: 0.33
Nodes (3): Контракт-тесты: реестр скиллов непротиворечив, аудит callback'ов без нарушений., Каждый литеральный callback_data должен где-то обрабатываться (advisory)., test_no_unhandled_callbacks()

### Community 25 - "List Cleanup Engine"
Cohesion: 0.50
Nodes (4): open_cleanup(), Редактирование списка = режим чистки (пагинация + мультивыбор)., Свежий вход в режим чистки - сбрасываем выбор., send_dict_edit()

### Community 26 - "Safe Send & HTML Grader"
Cohesion: 0.50
Nodes (4): grade_html(), Проверка баланса разрешённых тегов в готовом HTML. -> warnings., Прогоняет грейдеры под surface, чистит markdown->HTML и шлёт с откатом на plain., safe_send()

## Knowledge Gaps
- **10 isolated node(s):** `$schema`, `builder`, `startCommand`, `restartPolicyType`, `Skill` (+5 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `esc()` connect `Learning: Translation & Topics` to `Leisure Content (movies/books/music)`, `My Day Summary & Formatting`, `Balance (health/food/doctor)`, `Weather & Cache`, `Bot Dispatcher & Menu`, `Wardrobe`, `Learning: List Cleanup`, `Learning: Detective Game`, `Travel`, `Learning: Dictionary Management`, `Learning: Grammar Trainer`, `Learning: Word Trainer`?**
  _High betweenness centrality (0.088) - this node is a cross-community bridge._
- **Why does `AgentShield security layer` connect `Security & ECC Config` to `Bot Dispatcher & Menu`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **What connects `Явные order/claude_model имеют приоритет; иначе берём орден/модель из тира.`, `Чинит неэкранированные двойные кавычки внутри строковых значений JSON.     Идём`, `Подсказка о локации для генерации фактов - зависит от выбранной страны.` to the rest of the system?**
  _107 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Leisure Content (movies/books/music)` be split into smaller, more focused modules?**
  _Cohesion score 0.07138535995160314 - nodes in this community are weakly interconnected._
- **Should `Security & ECC Config` be split into smaller, more focused modules?**
  _Cohesion score 0.05550416281221091 - nodes in this community are weakly interconnected._
- **Should `My Day Summary & Formatting` be split into smaller, more focused modules?**
  _Cohesion score 0.06852497096399536 - nodes in this community are weakly interconnected._
- **Should `Notes & Favorites` be split into smaller, more focused modules?**
  _Cohesion score 0.14919354838709678 - nodes in this community are weakly interconnected._