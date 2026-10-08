"""Игры: локальные рекомендации, жанры, платформы и проверяемые премьеры."""

import logging
import asyncio
import hashlib
import re
import secrets
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

import ai
import config
import inclusive_recommendations
import igdb
import research
import recommendation_rotation as rotation
import recommendation_stoplist
import secure
import settings
import store
from ui import leisure as leisure_ui
from ui.navigation import nav_row

_log = logging.getLogger(__name__)

_GAME_PREMIERE_VIEWS = {}


GAME_PLATFORMS = (
    ("pc", "💻 ПК"),
    ("ps5", "🎮 PS5"),
    ("xbox", "🟢 Xbox Series"),
    ("switch", "🔴 Nintendo Switch"),
    ("mobile", "📱 Мобильные"),
    ("board", "🎲 Настолки"),
)
GAME_GENRES = (
    ("rpg", "RPG"),
    ("action", "Экшен"),
    ("strategy", "Стратегии"),
    ("adventure", "Приключения"),
    ("cozy", "Уютные"),
    ("horror", "Хоррор"),
)

_PLATFORM_LABEL = dict(GAME_PLATFORMS)
_PLATFORM_LABEL["other"] = "🕹️ Прочее"
_GENRE_LABEL = dict(GAME_GENRES)
_GENRE_LABEL["board"] = "Настолки"
_GENRE_LABEL["simulator"] = "Симулятор"
_GAME_PREMIERES_VERSION = 3
_GAME_SET_VIEW_TTL = 24 * 3600
_game_set_views = {}
_manual_game_choices = {}
_MANUAL_GAME_CHOICE_TTL = 15 * 60

_GAME_RECENCY_OPTIONS = (
    ("🆕 Новинки", "new"),
    ("📅 2020-е", "2020s"),
    ("До 2020 года", "classic"),
    ("Любые годы", ""),
)
_GAME_RATING_OPTIONS = (
    ("7.5+", "7.5"),
    ("8.0+", "8.0"),
    ("8.5+", "8.5"),
)

_GAME_CATALOG = (
    {
        "id": "baldurs-gate-3", "name": "Baldur’s Gate 3", "platforms": ["pc", "ps5", "xbox"],
        "poster": "https://static-cdn.jtvnw.net/ttv-boxart/Baldur%27s%20Gate%203-600x800.jpg",
        "year": 2023, "rating": 9.0,
        "genres": ["rpg", "strategy"],
        "description": "Большая ролевая история, где решения заметно меняют отношения, задания и финал.",
        "reasons": ["Можно проходить одному или в кооперативе", "Тактика в боях не мешает сильным персонажам"],
        "start": "создай героя под любимый стиль и не пытайся увидеть всё за одно прохождение",
    },
    {
        "id": "hades", "name": "Hades", "platforms": ["pc", "ps5", "xbox", "switch", "mobile"],
        "poster": "https://static-cdn.jtvnw.net/ttv-boxart/Hades-600x800.jpg",
        "year": 2020, "rating": 9.0,
        "genres": ["action"],
        "description": "Быстрый экшен с короткими забегами, живыми диалогами и понятным ростом между попытками.",
        "reasons": ["Удобно играть короткими сессиями", "Каждое поражение продолжает историю"],
        "start": "попробуй несколько видов оружия и выбери то, с которым легче держать ритм",
    },
    {
        "id": "alan-wake-2", "name": "Alan Wake 2", "platforms": ["pc", "ps5", "xbox"],
        "poster": "https://cdn.prod.website-files.com/64630b03551142e3347ae3da/64e6343ba7d56667e9034e5a_AW2_poster.webp",
        "year": 2023, "rating": 8.5,
        "genres": ["horror", "adventure"],
        "description": "Мрачный детективный хоррор с двумя героями, сильной постановкой и необычной структурой.",
        "reasons": ["Сюжет важнее бесконечных сражений", "Атмосфера работает как хороший сериал"],
        "start": "играй вечером в наушниках и внимательно изучай доску расследования",
    },
    {
        "id": "disco-elysium", "name": "Disco Elysium", "platforms": ["pc", "ps5", "xbox", "switch"],
        "poster": "https://static-cdn.jtvnw.net/ttv-boxart/Disco%20Elysium-600x800.jpg",
        "year": 2019, "rating": 9.0,
        "genres": ["rpg", "adventure"],
        "description": "Детективная RPG почти без обычных боёв, зато с сильными диалогами и свободой характера.",
        "reasons": ["Ошибки открывают не менее интересные сцены", "Подходит, если хочется взрослой истории"],
        "start": "не загружай сохранение после каждого провала — игра умеет превращать его в сюжет",
    },
    {
        "id": "it-takes-two", "name": "It Takes Two", "platforms": ["pc", "ps5", "xbox", "switch"],
        "poster": "https://static-cdn.jtvnw.net/ttv-boxart/It%20Takes%20Two-600x800.jpg",
        "year": 2021, "rating": 8.5,
        "genres": ["adventure", "action"],
        "description": "Кооперативное приключение для двоих, которое постоянно меняет правила и механики.",
        "reasons": ["Каждый игрок получает свою роль", "Новые идеи появляются почти в каждой главе"],
        "start": "пригласи человека, с которым комфортно договариваться вслух",
    },
    {
        "id": "stardew-valley", "name": "Stardew Valley", "platforms": ["pc", "ps5", "xbox", "switch", "mobile"],
        "poster": "https://static-cdn.jtvnw.net/ttv-boxart/Stardew%20Valley-600x800.jpg",
        "year": 2016, "rating": 9.0,
        "genres": ["cozy", "strategy"],
        "description": "Спокойная игра о ферме, соседях и маленьких целях без обязательной гонки за результатом.",
        "reasons": ["Темп легко настроить под себя", "Есть одиночный и совместный режимы"],
        "start": "в первый сезон посади понемногу разных культур и познакомься с жителями",
    },
    {
        "id": "cocoon", "name": "Cocoon", "platforms": ["pc", "ps5", "xbox", "switch"],
        "poster": "https://images.igdb.com/igdb/image/upload/t_cover_big/co4v2z.jpg",
        "year": 2023, "rating": 8.0,
        "genres": ["adventure"],
        "description": "Компактное приключение-головоломка без текста, где целые миры становятся инструментами.",
        "reasons": ["Механики объясняются без длинного обучения", "Прохождение не требует десятков часов"],
        "start": "не спеши искать подсказки — окружение почти всегда показывает следующий шаг",
    },
    {
        "id": "dune-imperium", "name": "Дюна: Империя", "platforms": ["board"],
        "poster": "https://d19y2ttatozxjp.cloudfront.net/assets/dune/DuneImperium_BoxArtKey.png",
        "year": 2020, "rating": 8.4,
        "genres": ["board", "strategy"],
        "description": "Настольная стратегия, соединяющая колодостроение, борьбу за влияние и конфликт на карте.",
        "reasons": ["Партии дают много решений без лишних правил", "Хорошо раскрывается при повторных встречах"],
        "start": "первую партию сыграй базовыми лидерами без дополнений",
    },
    {
        "id": "cascadia", "name": "Каскадия", "platforms": ["board"],
        "poster": "https://cdn.shopify.com/s/files/1/0440/6493/1998/products/a3220bfe8864d2929ca67e61b068cf68d5b20f4d.jpg?v=1619494793",
        "year": 2021, "rating": 8.0,
        "genres": ["board", "cozy", "strategy"],
        "description": "Спокойная настолка о создании природных зон и сочетании животных с подходящей местностью.",
        "reasons": ["Правила объясняются за несколько минут", "Есть хороший одиночный режим"],
        "start": "используй простые карточки целей из первого сценария",
    },
    {
        "id": "wingspan", "name": "Крылья", "platforms": ["board", "pc", "xbox", "switch", "mobile"],
        "poster": "https://static-cdn.jtvnw.net/ttv-boxart/Wingspan-600x800.jpg",
        "year": 2019, "rating": 8.1,
        "genres": ["board", "strategy", "cozy"],
        "description": "Красочная стратегия о птицах, где постепенно собирается эффективная природная система.",
        "reasons": ["Работает и как настолка, и в цифровой версии", "Сочетает спокойный темп и продуманные комбинации"],
        "start": "в первой партии следи прежде всего за едой и яйцами, а не за идеальным движком",
    },
    {
        "id": "azul", "name": "Азул", "platforms": ["board"],
        "poster": "https://cdn.shoplightspeed.com/shops/636231/files/68363129/1652x1652x2/plan-b-games-azul.jpg",
        "year": 2017, "rating": 7.8,
        "genres": ["board", "strategy"],
        "description": "Абстрактная настолка о сборе узоров с простыми ходами и неприятно приятной конкуренцией.",
        "reasons": ["Подходит для короткого вечера", "Правила простые, а решения быстро становятся глубокими"],
        "start": "первую партию сыграй вдвоём — так легче заметить цену каждого выбора",
    },
    {
        "id": "spirit-island", "name": "Остров духов", "platforms": ["board", "pc", "mobile"],
        "poster": "https://static-cdn.jtvnw.net/ttv-boxart/Spirit%20Island-600x800.jpg",
        "year": 2017, "rating": 8.3,
        "genres": ["board", "strategy"],
        "description": "Сложная кооперативная стратегия, где духи острова вместе отражают вторжение колонизаторов.",
        "reasons": ["У каждого духа действительно разный стиль", "Командное планирование важнее удачи"],
        "start": "возьми духа низкой сложности и играй без сценария и противника",
    },
    # Настолки без проверенной обложки показываются текстовой карточкой.
    *(
        {"platforms": ["board"], "poster": "", **item}
        for item in (
            # Новинки 2026: год и описание сверены по поиску, рейтинг ещё не устоялся.
            {
                "id": "sanibel", "name": "Sanibel",
                "year": 2026, "rating": 0, "genres": ["board", "cozy"],
                "description": "Спокойная выкладка тайлов от автора «Крыльев»: собираешь ракушки на пляже и складываешь из них узоры.",
                "reasons": ["Новая игра Elizabeth Hargrave", "Расслабленный темп для вечера"],
                "start": "сначала собирай узоры одной формы, а не всё подряд",
            },
            {
                "id": "hanami", "name": "Hanami",
                "year": 2026, "rating": 0, "genres": ["board", "strategy"],
                "description": "Переосмысление классики Reiner Knizia «Samurai»: борьба за влияние на пикниках под цветущей сакурой.",
                "reasons": ["Две карты: классическая Япония и Токио с новыми правилами", "Партия на 30–60 минут"],
                "start": "первую партию сыграй на карте Японии — это классические правила",
            },
            {
                "id": "duel-of-meloch", "name": "Duel of Meloch",
                "year": 2026, "rating": 0, "genres": ["board", "strategy", "action"],
                "description": "Дуэль от Stonemaier Games: один игрок играет по правилам Scythe, другой — по правилам Expeditions.",
                "reasons": ["Партия на 45 минут вдвоём", "Есть одиночный режим"],
                "start": "если знаешь Scythe, начни за его сторону",
            },
            {
                "id": "the-queens-dilemma", "name": "The Queen's Dilemma",
                "year": 2026, "rating": 0, "genres": ["board", "strategy", "adventure"],
                "description": "Легаси-продолжение The King's Dilemma: совет королевы решает судьбу страны, а карта меняется от решений.",
                "reasons": ["Сюжетная кампания для постоянной компании", "Решения влияют на всю игру"],
                "start": "соберите постоянный состав — кампанию играют одной компанией",
            },
            {
                "id": "wildscape", "name": "Wildscape",
                "year": 2026, "rating": 0, "genres": ["board", "cozy", "strategy"],
                "description": "Восстанавливаешь земли Африки: леса, болота и саванны, куда возвращаются животные.",
                "reasons": ["Был первым в BGG Hotness", "От 1 до 4 игроков"],
                "start": "сначала связывай похожие места обитания — так быстрее приходят животные",
            },
            {
                "id": "adas-dream", "name": "Ada's Dream",
                "year": 2025, "rating": 0, "genres": ["board", "strategy"],
                "description": "Евро-стратегия о том, как Ада Лавлейс строит первый компьютер: кубики и развитие движка.",
                "reasons": ["Победитель Diamant d'Or 2026", "Глубокая игра для опытных"],
                "start": "планируй кубики на пару ходов вперёд",
            },
            {
                "id": "seti", "name": "SETI: Search for Extraterrestrial Intelligence",
                "year": 2024, "rating": 8.4, "genres": ["board", "strategy"],
                "description": "Научная стратегия о поиске внеземной жизни: телескопы, зонды и анализ сигналов.",
                "reasons": ["Каждая партия складывается в свою исследовательскую историю", "Много путей к победе"],
                "start": "в первой партии держись одного-двух направлений исследований",
            },
            {
                "id": "arcs", "name": "Arcs",
                "year": 2024, "rating": 8.0, "genres": ["board", "strategy"],
                "description": "Космическая стратегия о борьбе за власть, где розыгрыш карт решает почти всё.",
                "reasons": ["Острое взаимодействие между игроками", "Короткие партии в базовом режиме"],
                "start": "начни с базовой игры без кампании",
            },
            {
                "id": "harmonies", "name": "Harmonies",
                "year": 2024, "rating": 7.9, "genres": ["board", "cozy", "strategy"],
                "description": "Спокойная головоломка о создании ландшафтов, где поселяются животные.",
                "reasons": ["Правила объясняются за пять минут", "Красивое поле растёт прямо на глазах"],
                "start": "бери карты животных с простыми узорами, пока не почувствуешь ритм",
            },
            {
                "id": "dune-imperium-uprising", "name": "Dune: Imperium – Uprising",
                "year": 2023, "rating": 8.6, "genres": ["board", "strategy"],
                "description": "Самостоятельная версия «Дюны: Империи» со шпионами, червями и новыми контрактами.",
                "reasons": ["Развивает идеи оригинала без лишней сложности", "Хорошо играется вшестером в командах"],
                "start": "если знаешь оригинал, сразу пробуй шпионов — это главная новая механика",
            },
            {
                "id": "sky-team", "name": "Sky Team",
                "year": 2023, "rating": 8.0, "genres": ["board", "strategy"],
                "description": "Кооперативная игра на двоих о посадке самолёта без права обсуждать ходы.",
                "reasons": ["Партия занимает около 15 минут", "Напряжение без сложных правил"],
                "start": "проходи аэропорты по порядку — сложность растёт постепенно",
            },
            {
                "id": "forest-shuffle", "name": "Forest Shuffle",
                "year": 2023, "rating": 7.9, "genres": ["board", "cozy"],
                "description": "Карточная игра о лесе: сажаешь деревья и заселяешь их животными.",
                "reasons": ["Компактная коробка для поездок", "Приятные комбинации без долгих расчётов"],
                "start": "в начале ставь больше деревьев — под ними откроется место для карт",
            },
            {
                "id": "earth", "name": "Earth",
                "year": 2023, "rating": 7.8, "genres": ["board", "cozy", "strategy"],
                "description": "Движкостроительная игра о создании экосистемы из растений, почвы и климата.",
                "reasons": ["Чужие ходы тоже дают тебе действия", "Хорошо работает в одиночку"],
                "start": "собери несколько карт, которые срабатывают от одного действия",
            },
            {
                "id": "faraway", "name": "Faraway",
                "year": 2023, "rating": 7.6, "genres": ["board", "cozy", "strategy"],
                "description": "Короткая карточная игра, где путешествие подсчитывается задом наперёд.",
                "reasons": ["Партия на 30 минут", "Необычная механика подсчёта очков"],
                "start": "думай о последних картах первыми — они считаются раньше остальных",
            },
            {
                "id": "heat", "name": "Heat: Pedal to the Metal",
                "year": 2022, "rating": 8.0, "genres": ["board", "action"],
                "description": "Гонки 1960-х, где скорость приходится оплачивать перегревом мотора.",
                "reasons": ["Азарт без кубиков", "Работает от двух до шести игроков"],
                "start": "сыграй первую трассу без улучшений и погоды",
            },
            {
                "id": "dorfromantik-board", "name": "Dorfromantik: The Board Game",
                "year": 2022, "rating": 7.8, "genres": ["board", "cozy"],
                "description": "Кооперативная выкладка тайлов с открытием новых элементов от партии к партии.",
                "reasons": ["Подходит для спокойного вечера", "Кампания открывает новое постепенно"],
                "start": "выполняй задания на тайлах в первую очередь — они дают больше всего очков",
            },
            {
                "id": "ark-nova", "name": "Ark Nova",
                "year": 2021, "rating": 8.5, "genres": ["board", "strategy"],
                "description": "Глубокая стратегия о строительстве современного зоопарка и защите видов.",
                "reasons": ["Огромная колода — партии сильно отличаются", "Есть хороший одиночный режим"],
                "start": "в первой партии не пытайся брать всё — выбери два-три направления",
            },
            {
                "id": "lost-ruins-of-arnak", "name": "Lost Ruins of Arnak",
                "year": 2020, "rating": 8.1, "genres": ["board", "adventure", "strategy"],
                "description": "Экспедиция на неизведанный остров: колодостроение плюс выставление рабочих.",
                "reasons": ["Тема приключения ощущается в механиках", "Средняя сложность для вечера"],
                "start": "раньше открывай новые места на карте — они дают больше ресурсов",
            },
            {
                "id": "gloomhaven-jaws-of-the-lion", "name": "Gloomhaven: Jaws of the Lion",
                "year": 2020, "rating": 8.4, "genres": ["board", "rpg", "adventure"],
                "description": "Тактическая кооперативная кампания с героями, которые растут от сценария к сценарию.",
                "reasons": ["Обучение встроено в первые сценарии", "Ощущение настоящей RPG за столом"],
                "start": "играй сценарии по порядку — первые пять учат правилам",
            },
            {
                "id": "brass-birmingham", "name": "Brass: Birmingham",
                "year": 2018, "rating": 8.6, "genres": ["board", "strategy"],
                "description": "Экономическая стратегия о промышленной революции, каналах и железных дорогах.",
                "reasons": ["Одна из самых высоко оценённых настолок", "Глубокая экономика без случайности"],
                "start": "в первой эпохе строй каналы рядом со своими зданиями",
            },
            {
                "id": "everdell", "name": "Everdell",
                "year": 2018, "rating": 8.0, "genres": ["board", "cozy", "strategy"],
                "description": "Уютное выставление рабочих о лесных зверях, строящих свой город.",
                "reasons": ["Очень красивое оформление", "Понятные комбинации построек и жителей"],
                "start": "бери пары «здание + житель» — второго можно поставить бесплатно",
            },
            {
                "id": "mansions-of-madness-2e", "name": "Mansions of Madness: Second Edition",
                "year": 2016, "rating": 8.0, "genres": ["board", "horror", "adventure"],
                "description": "Кооперативный хоррор-детектив по Лавкрафту с приложением-ведущим.",
                "reasons": ["Приложение берёт на себя правила монстров", "Сильная атмосфера расследования"],
                "start": "выбери короткий сценарий из базовой коробки",
            },
            {
                "id": "nemesis", "name": "Nemesis",
                "year": 2018, "rating": 8.3, "genres": ["board", "horror", "action"],
                "description": "Выживание на корабле с чужими, где у каждого игрока есть своя скрытая цель.",
                "reasons": ["Атмосфера фильма «Чужой» за столом", "Истории получаются разными каждый раз"],
                "start": "в первой партии играй в кооперативном режиме",
            },
            {
                "id": "arkham-horror-lcg", "name": "Arkham Horror: The Card Game",
                "year": 2016, "rating": 8.1, "genres": ["board", "horror", "rpg"],
                "description": "Кооперативная карточная кампания-расследование по мотивам Лавкрафта.",
                "reasons": ["Сюжет продолжается от сценария к сценарию", "Хорошо играется вдвоём и соло"],
                "start": "пройди первую кампанию базовой коробки готовыми колодами",
            },
            {
                "id": "eldritch-horror", "name": "Eldritch Horror",
                "year": 2013, "rating": 7.9, "genres": ["board", "horror", "adventure"],
                "description": "Кооперативное путешествие по миру, чтобы остановить пробуждение Древнего.",
                "reasons": ["Много историй на картах встреч", "Победа никогда не гарантирована"],
                "start": "начни с Азатота — это самый понятный противник",
            },
            {
                "id": "terraforming-mars", "name": "Terraforming Mars",
                "year": 2016, "rating": 8.4, "genres": ["board", "strategy"],
                "description": "Корпорации соревнуются в превращении Марса в пригодную для жизни планету.",
                "reasons": ["Сотни разных карт проектов", "Есть одиночный режим"],
                "start": "в первой партии возьми базовые корпорации без драфта",
            },
            {
                "id": "root", "name": "Root",
                "year": 2018, "rating": 8.0, "genres": ["board", "strategy", "action"],
                "description": "Асимметричная война лесных фракций, где каждая играет по своим правилам.",
                "reasons": ["Каждая фракция ощущается как отдельная игра", "Много взаимодействия"],
                "start": "новичкам дай Маркизу и Орлов — их проще всего понять",
            },
            {
                "id": "seven-wonders-duel", "name": "7 Wonders Duel",
                "year": 2015, "rating": 8.1, "genres": ["board", "strategy"],
                "description": "Дуэль двух цивилизаций с тремя разными путями к победе.",
                "reasons": ["Одна из лучших игр на двоих", "Партия на 30 минут"],
                "start": "следи за научной победой соперника — она приходит внезапно",
            },
            {
                "id": "great-western-trail-2e", "name": "Great Western Trail: Second Edition",
                "year": 2021, "rating": 8.3, "genres": ["board", "strategy"],
                "description": "Перегон скота через Дикий Запад с колодостроением и развитием маршрута.",
                "reasons": ["Глубокая стратегия для опытных игроков", "Партии сильно отличаются"],
                "start": "сначала улучши колоду, потом думай о дальних поставках",
            },
            {
                "id": "scythe", "name": "Scythe",
                "year": 2016, "rating": 8.2, "genres": ["board", "strategy"],
                "description": "Альтернативная Восточная Европа 1920-х: мехи, фермы и экономическое давление.",
                "reasons": ["Красивое оформление и миниатюры", "Конфликт важен, но не обязателен"],
                "start": "сосредоточься на одной ветке улучшений с первых ходов",
            },
            {
                "id": "castles-of-burgundy", "name": "The Castles of Burgundy",
                "year": 2011, "rating": 8.0, "genres": ["board", "strategy"],
                "description": "Классическая евро-стратегия о развитии поместья на кубиках и тайлах.",
                "reasons": ["Кубики дают свежие решения каждый ход", "Проверенная классика жанра"],
                "start": "раньше бери рабочих — они делают кубики гибче",
            },
            {
                "id": "parks", "name": "Parks",
                "year": 2019, "rating": 7.7, "genres": ["board", "cozy"],
                "description": "Путешествие по национальным паркам США с иллюстрациями в стиле постеров.",
                "reasons": ["Очень красивая игра", "Простые правила и спокойный темп"],
                "start": "бери снаряжение раньше — оно окупается весь сезон",
            },
            {
                "id": "calico", "name": "Calico",
                "year": 2020, "rating": 7.6, "genres": ["board", "cozy"],
                "description": "Головоломка о шитье лоскутного одеяла, на которое приходят коты.",
                "reasons": ["Компактная и уютная", "Каждый ход — маленькая головоломка"],
                "start": "выбери одного кота и строй узор под него",
            },
            {
                "id": "robinson-crusoe", "name": "Robinson Crusoe: Adventures on the Cursed Island",
                "year": 2012, "rating": 7.8, "genres": ["board", "adventure"],
                "description": "Кооперативное выживание на необитаемом острове со сценариями.",
                "reasons": ["Жёсткое и честное выживание", "Работает в одиночку"],
                "start": "начни с первого сценария и играй с собакой Пятницей",
            },
            {
                "id": "clank", "name": "Clank!",
                "year": 2016, "rating": 7.7, "genres": ["board", "adventure", "action"],
                "description": "Колодостроительное ограбление подземелья, где шум будит дракона.",
                "reasons": ["Весёлый азарт и риск", "Понятно даже новичкам"],
                "start": "не уходи слишком глубоко без пары карт передвижения",
            },
            {
                "id": "king-of-tokyo", "name": "King of Tokyo",
                "year": 2011, "rating": 7.1, "genres": ["board", "action"],
                "description": "Монстры дерутся за Токио на кубиках с выбором суперспособностей.",
                "reasons": ["Объясняется за пять минут", "Подходит для компании"],
                "start": "не сиди в Токио слишком долго, если здоровье на исходе",
            },
            {
                "id": "gloomhaven", "name": "Gloomhaven",
                "year": 2017, "rating": 8.6, "genres": ["board", "rpg", "adventure"],
                "description": "Огромная тактическая кампания, где герои уходят на покой и открывают новых.",
                "reasons": ["Сотни часов контента", "Каждый класс играется по-своему"],
                "start": "если не играл в Jaws of the Lion, начни с неё",
            },
            {
                "id": "descent-legends-of-the-dark", "name": "Descent: Legends of the Dark",
                "year": 2021, "rating": 7.9, "genres": ["board", "rpg", "adventure"],
                "description": "Кооперативная фэнтези-кампания с приложением и объёмным полем.",
                "reasons": ["Приложение ведёт сюжет и монстров", "Зрелищная игра за столом"],
                "start": "в первой миссии сосредоточься на понятном герое ближнего боя",
            },
            {
                "id": "cartographers", "name": "Cartographers",
                "year": 2019, "rating": 7.6, "genres": ["board", "cozy", "adventure"],
                "description": "Рисуешь карту королевства по заданиям и прячешься от набегов монстров.",
                "reasons": ["Любое число игроков", "Карта в конце радует глаз"],
                "start": "оставляй место под задания следующих сезонов",
            },
            {
                "id": "the-crew-deep-sea", "name": "The Crew: Mission Deep Sea",
                "year": 2021, "rating": 8.1, "genres": ["board", "adventure"],
                "description": "Кооперативная взятка, где вся команда выполняет миссии почти без слов.",
                "reasons": ["Партия на 20 минут", "Прогрессия через миссии"],
                "start": "соберите первые миссии из лёгких заданий",
            },
        )
    ),
)


def game_platforms(cid):
    selected = settings.get(cid, "game_platforms", [])
    valid = {key for key, _label in GAME_PLATFORMS}
    if not isinstance(selected, list):
        return []
    normalized = []
    for key in selected:
        values = ("xbox", "switch", "mobile") if key == "other" else (key,)
        for value in values:
            if value in valid and value not in normalized:
                normalized.append(value)
    return normalized


def game_platform_labels(cid):
    return [_PLATFORM_LABEL[key] for key in game_platforms(cid)]


def _effective_platforms(cid):
    # Настолки — отдельная кнопка; без явного выбора цифровой подбор их не берёт.
    return game_platforms(cid) or [key for key, _label in GAME_PLATFORMS if key != "board"]


def _game_recency(cid):
    value = str(settings.get(cid, "game_recency", "") or "")
    return value if value in {option for _label, option in _GAME_RECENCY_OPTIONS} else ""


def _game_min_rating(cid):
    value = str(settings.get(cid, "game_min_rating", "") or "")
    valid = {option for _label, option in _GAME_RATING_OPTIONS}
    return float(value) if value in valid else None


def _platform_signature(cid):
    return hashlib.sha256("|".join(sorted(_effective_platforms(cid))).encode()).hexdigest()[:16]


def _favorite_game_name(item):
    if isinstance(item, dict):
        return str(item.get("name") or item.get("value") or "").strip()
    return str(item or "").strip()


def normalize_favorite_game(value):
    """Сохраняет известную игру с локальными метаданными, неизвестную — без выдумок."""
    name = " ".join(_favorite_game_name(value).split()).strip(" ,;.-")
    if not name:
        return None
    is_board = bool(re.search(r"\b(настолка|настольная игра)\b", name, flags=re.IGNORECASE))
    if is_board:
        name = re.sub(
            r"\b(настолка|настольная игра)\b", "", name, flags=re.IGNORECASE,
        ).strip(" ,;.-")
    match = next((item for item in _GAME_CATALOG if item["name"].casefold() == name.casefold()), None)
    if match:
        return dict(match)
    return {
        "name": name,
        "genres": ["board"] if is_board else [],
        "platforms": ["board"] if is_board else [],
    }


def enrich_favorite_game(item):
    """Дополняет неизвестную цифровую игру проверенными метаданными IGDB."""
    prepared = dict(item or {})
    if prepared.get("platforms") == ["board"]:
        return prepared
    return igdb.enrich_game_recommendation(prepared)


def _favorite_games(cid):
    return store.get_list(config.FAVORITE_GAMES_KEY, cid)


def _favorite_game_signature(cid):
    names = sorted(_favorite_game_name(item).casefold() for item in _favorite_games(cid)
                   if _favorite_game_name(item))
    return hashlib.sha256("|".join(names).encode()).hexdigest()[:16]


def _game_signature(cid):
    payload = (
        f"{_platform_signature(cid)}|{_game_recency(cid)}|{_game_min_rating(cid) or ''}"
        f"|{_favorite_game_signature(cid)}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _youtube_trailer_search_url(title):
    query = " ".join(str(title or "").split())
    return (
        f"https://www.youtube.com/results?search_query={quote_plus(f'{query} game official trailer')}"
        if query else ""
    )


def _ensure_game_trailer_url(item):
    prepared = dict(item or {})
    if "board" in (prepared.get("platforms") or []) and not str(
        prepared.get("trailer_url") or ""
    ).strip():
        prepared["trailer_url"] = _youtube_trailer_search_url(
            prepared.get("name") or prepared.get("title")
        )
    return prepared


def _eligible_games(cid, genre=None, board=False):
    board_platform = board or genre == "board"
    platforms = {"board"} if board_platform else set(_effective_platforms(cid))
    hidden = {value.casefold() for value in recommendation_stoplist.values(cid, "game")}
    candidates = [
        item for item in _GAME_CATALOG
        if platforms.intersection(item["platforms"])
        and (not genre or genre in item["genres"])
        and item["name"].casefold() not in hidden
    ]
    current_year = datetime.now(config.TZ).year
    recency = _game_recency(cid)
    min_rating = _game_min_rating(cid)

    def preferred(item):
        year = int(item.get("year") or 0)
        if recency == "new" and year < current_year - 3:
            return False
        if recency == "2020s" and year < 2020:
            return False
        if recency == "classic" and (not year or year >= 2020):
            return False
        # Рейтинг новинки ещё не устоялся — неизвестный рейтинг не отсекает игру.
        rating = float(item.get("rating") or 0)
        return min_rating is None or not rating or rating >= min_rating

    filtered = [item for item in candidates if preferred(item)]
    return filtered or candidates


def _decorate_game(item, cid, *, genre=None, board=False):
    board_platform = board or genre == "board"
    platforms = {"board"} if board_platform else set(_effective_platforms(cid))
    visible_platforms = [key for key in item["platforms"] if key in platforms]
    primary_genre = item["genres"][0] if item.get("genres") else ""
    return {
        **dict(item),
        "platform_labels": [_PLATFORM_LABEL[key] for key in visible_platforms],
        "genre_label": _GENRE_LABEL.get(primary_genre, primary_genre),
    }


def _board_style(item):
    genres = [value for value in (item or {}).get("genres") or [] if value != "board"]
    return genres[0] if genres else ""


def pick_game(cid, *, genre=None, refresh=False, board=False):
    """Локальный подбор без AI: платформы + жанр + защита от недавних повторов."""
    profile = store.get_profile(cid)
    today = datetime.now(config.TZ).date()
    year, week, _weekday = today.isocalendar()
    week_key = f"{year}-W{week:02d}"
    signature = _game_signature(cid)
    cached = profile.get("game_daily") or {}
    if (not refresh and not genre and cached.get("week") == week_key
            and cached.get("signature") == signature and isinstance(cached.get("item"), dict)):
        return _decorate_game(cached["item"], cid, genre=genre, board=board)

    pool = _eligible_games(cid, genre=genre, board=board)
    if not pool:
        return {}
    favorites = _favorite_games(cid)
    favorite_names = {_favorite_game_name(item).casefold() for item in favorites if _favorite_game_name(item)}
    favorite_genres = {
        str(value) for item in favorites if isinstance(item, dict)
        for value in (item.get("genres") or []) if str(value)
    }
    not_favorite = [item for item in pool if item["name"].casefold() not in favorite_names]
    if not_favorite:
        pool = not_favorite
    seen = [str(value) for value in profile.get("game_seen", []) if str(value)]
    current_year = datetime.now(config.TZ).year

    def recommendation_rank(item):
        overlap = len(favorite_genres.intersection(item.get("genres") or []))
        year = int(item.get("year") or 0)
        # Сначала персональное совпадение, затем лучшие новые игры; после них
        # каталог естественно переходит к прошлым годам без ошибки и повтора.
        year_distance = abs(current_year - year) if year else 999
        return (-overlap, year_distance, -float(item.get("rating") or 0), item["name"])

    pool = sorted(pool, key=recommendation_rank)
    game_key = lambda value: (
        str(value.get("id") or "") if isinstance(value, dict) else str(value or "")
    )
    candidates = rotation.candidates_for_cycle(
        pool, seen, current=seen[-1] if seen else None, key=game_key,
    )
    # После полного круга — сначала давно не показанные, иначе ротация
    # залипает на первых по рангу (особенно в маленьком каталоге настолок).
    seen_order = {marker: index for index, marker in enumerate(seen)}
    candidates = sorted(candidates, key=lambda value: seen_order.get(game_key(value), -1))
    item = candidates[0]
    if board or genre == "board":
        # Настолки «живые»: случайная из свежих, по возможности другого жанра.
        pool_by_key = {game_key(value): value for value in pool}
        previous = next((pool_by_key[marker] for marker in reversed(seen) if marker in pool_by_key), None)
        window = candidates[:6]
        varied = [value for value in window if _board_style(value) != _board_style(previous)]
        item = secrets.choice(varied or window)
    daily_entry = {"week": week_key, "signature": signature, "item": dict(item)}

    def save_selection(current):
        current["game_seen"] = rotation.remember(
            current.get("game_seen", []), item["id"], limit=200,
        )
        if not genre:
            current["game_daily"] = daily_entry
        return current, None

    store.mutate_profile(cid, save_selection)
    return _decorate_game(item, cid, genre=genre, board=board)


def _game_keyboard(*, no_match=False, genre=None, board=False):
    rows = []
    if board or genre == "board":
        board_genre = genre if genre and genre != "board" else None
        if not no_match:
            rows.append([InlineKeyboardButton(
                "✨ Другая игра", callback_data=f"vg_gb_{board_genre}" if board_genre else "vg_board",
            )])
        # Настолки: доступен подбор по жанру внутри настольного режима.
        rows.append([InlineKeyboardButton("🎭 Выбрать жанр", callback_data="vg_genres_board")])
        if not no_match:
            rows.append([InlineKeyboardButton("Не нравится", callback_data="game_no", api_kwargs={"style": "danger"})])
    else:
        if not no_match:
            rows.append([InlineKeyboardButton(
                "✨ Другая игра", callback_data=f"vg_next_{genre}" if genre else "vg_next",
            )])
        rows.append([InlineKeyboardButton("🎭 Выбрать жанр", callback_data="vg_genres")])
        rows.append([InlineKeyboardButton("🎲 Настолки", callback_data="vg_board")])
        if not no_match:
            rows.append([InlineKeyboardButton("Не нравится", callback_data="game_no", api_kwargs={"style": "danger"})])
    if no_match:
        rows.append([InlineKeyboardButton("📝 Выбрать предпочтения", callback_data="game_prefs")])
    rows.append(nav_row("m_leisure"))
    return InlineKeyboardMarkup(rows)


async def game_dislike(bot, cid, *, status=None):
    """«Не нравится»: игра уходит в чёрный список, сразу показывается следующая."""
    rec = store.last_recos.get(str(cid))
    if isinstance(rec, dict) and rec.get("kind") == "game" and rec.get("items"):
        recommendation_stoplist.add(cid, "game", rec["items"][0], "hidden")
        _reset_game_daily(cid)
        await send_game_recommendation(
            bot, cid, status=status, refresh=True, genre=rec.get("genre"), board=bool(rec.get("board")),
        )
        return
    await send_game_recommendation(bot, cid, status=status, refresh=True)


async def game_love(bot, cid, q=None):
    """Добавляет текущую рекомендацию игры в набор без дублей."""
    profile = store.get_profile(cid)
    daily = profile.get("game_daily") if isinstance(profile, dict) else None
    item = daily.get("item") if isinstance(daily, dict) else None
    if not isinstance(item, dict) or not item.get("name"):
        return
    existing = {_favorite_game_name(value).casefold() for value in _favorite_games(cid)}
    if item["name"].casefold() not in existing:
        store.add_to_list(config.FAVORITE_GAMES_KEY, cid, item)
        _reset_game_daily(cid)
    await send_game_recommendation(bot, cid, q=q, refresh=True)


def _game_genre_title(value):
    label = _GENRE_LABEL.get(str(value), str(value or ""))
    return label.split(" ", 1)[-1] if " " in label else (label or "Без жанра")


def _game_set_records(cid):
    records = []
    for raw in store.ensure_list_ids(config.FAVORITE_GAMES_KEY, cid):
        item = dict(raw)
        value = item.get("value")
        if value and not item.get("name"):
            item = {**(normalize_favorite_game(value) or {}), "id": item.get("id")}
        name = _favorite_game_name(item)
        genres = [str(value) for value in item.get("genres") or [] if str(value)]
        actual_genres = [value for value in genres if value != "board"]
        if "board" in (item.get("platforms") or []):
            genre = "Настолки"
        else:
            genre = _game_genre_title(actual_genres[0]) if actual_genres else "Без жанра"
        item.update({"name": name, "genre": genre, "genre_label": genre})
        records.append(item)
    return records


def _new_game_set_view(cid):
    now = time.time()
    for token, view in list(_game_set_views.items()):
        if now - view.get("created_at", 0) > _GAME_SET_VIEW_TTL:
            _game_set_views.pop(token, None)
    genres = {}
    for item in _game_set_records(cid):
        genres.setdefault(item["genre"], []).append(item)
    for items in genres.values():
        items.sort(key=lambda item: item["name"].casefold())
    genre_order = {
        _game_genre_title(key): index for index, (key, _label) in enumerate(GAME_GENRES)
    }
    genre_order["Настолки"] = len(genre_order)
    genre_order["Без жанра"] = len(genre_order) + 1
    ordered = sorted(
        genres,
        key=lambda value: (genre_order.get(value, len(genre_order)), value.casefold()),
    )
    token = secrets.token_hex(3)
    view = {"cid": str(cid), "created_at": now,
            "genres": [(genre, genres[genre]) for genre in ordered]}
    _game_set_views[token] = view
    return token, view


def _game_set_view(cid, token):
    view = _game_set_views.get(token)
    if not view or view.get("cid") != str(cid) or time.time() - view.get("created_at", 0) > _GAME_SET_VIEW_TTL:
        _game_set_views.pop(token, None)
        return None
    return view


async def send_game_set(bot, cid, q=None):
    token, view = _new_game_set_view(cid)
    total = sum(len(items) for _genre, items in view["genres"])
    msg = leisure_ui.game_set_home(total, [
        {"genre": genre, "names": [item["name"] for item in items]}
        for genre, items in view["genres"]
    ])
    rows = [[InlineKeyboardButton("✅ Добавить игру", callback_data="as_loveadd_games")],
            *[[InlineKeyboardButton(f"{genre} · {len(items)}", callback_data=f"vg_setg:{token}:{index}:0")]
              for index, (genre, items) in enumerate(view["genres"])] ]
    rows.append([InlineKeyboardButton(
        "📝 Выбрать предпочтения", callback_data="game_prefs",
    )])
    rows.append(nav_row("lz_lib"))
    await _deliver(bot, cid, msg, InlineKeyboardMarkup(rows), q=q)


async def send_game_set_genre(bot, cid, token, genre_index, page=0, q=None):
    view = _game_set_view(cid, token)
    if view is None or not 0 <= genre_index < len(view["genres"]):
        await send_game_set(bot, cid, q=q)
        return
    genre, items = view["genres"][genre_index]
    page = max(0, min(int(page), len(items) - 1))
    item = items[page]
    card = _decorate_game(item, cid) if item.get("platforms") else dict(item)
    card = await asyncio.to_thread(enrich_favorite_game, card)
    card["platform_labels"] = [
        _PLATFORM_LABEL[key] for key in card.get("platforms") or [] if key in _PLATFORM_LABEL
    ]
    item.update(card)
    msg = leisure_ui.game_set_card(card)
    rows = []
    if len(items) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"vg_setg:{token}:{genre_index}:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"vg_setg:{token}:{genre_index}:{(page + 1) % len(items)}"),
        ])
    # «❌ Удалить» — первой строкой, над листанием.
    rows.insert(0, [InlineKeyboardButton(
        "❌ Удалить", callback_data=f"vg_setd:{token}:{item['id'][:8]}:{genre_index}:{page}",
    )])
    rows.append([InlineKeyboardButton("✅ Добавить игру", callback_data="as_loveadd_games")])
    rows.append(nav_row("vg_set"))
    kb = InlineKeyboardMarkup(rows)
    poster = str(card.get("poster") or "").strip()
    if q is not None and poster:
        try:
            await q.edit_message_media(
                media=InputMediaPhoto(
                    media=poster, caption=msg.text, caption_entities=msg.entities,
                ),
                reply_markup=kb,
            )
            return
        except Exception:
            _log.debug("send_game_set_genre: ignored error", exc_info=True)
    if poster:
        try:
            await bot.send_photo(
                chat_id=cid, photo=poster, caption=msg.text,
                caption_entities=msg.entities, reply_markup=kb,
            )
            return
        except Exception:
            _log.debug("send_game_set_genre: ignored error", exc_info=True)
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
    )


def _game_set_item(cid, token, short_id):
    view = _game_set_view(cid, token)
    if view is None:
        return None
    return next((item for _genre, items in view["genres"] for item in items
                 if str(item.get("id") or "").startswith(short_id)), None)


async def send_game_set_card(bot, cid, token, short_id, genre_index, page):
    item = _game_set_item(cid, token, short_id)
    if item is None:
        await send_game_set(bot, cid)
        return
    card = _decorate_game(item, cid) if item.get("platforms") else dict(item)
    card = await asyncio.to_thread(enrich_favorite_game, card)
    card["platform_labels"] = [
        _PLATFORM_LABEL[key] for key in card.get("platforms") or [] if key in _PLATFORM_LABEL
    ]
    msg = leisure_ui.game_set_card(card)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"vg_setd:{token}:{short_id}:{genre_index}:{page}")],
        nav_row(f"vg_setg:{token}:{genre_index}:{page}"),
    ])
    if card.get("poster"):
        try:
            await bot.send_photo(chat_id=cid, photo=card["poster"], caption=msg.text,
                                 caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            _log.debug("send_game_set_card: ignored error", exc_info=True)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def confirm_game_set_delete(bot, cid, token, short_id, genre_index, page, q=None):
    item = _game_set_item(cid, token, short_id)
    if item is None:
        await send_game_set(bot, cid, q=q)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"vg_setdok:{token}:{short_id}")],
        [InlineKeyboardButton("Отмена", callback_data=f"vg_setg:{token}:{genre_index}:{page}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    await _deliver(bot, cid, leisure_ui.game_delete_confirmation(item["name"]), kb, q=q)


async def delete_game_set_item(bot, cid, token, short_id, q=None):
    item = _game_set_item(cid, token, short_id)
    if item:
        store.remove_from_list_by_ids(config.FAVORITE_GAMES_KEY, cid, [item["id"]])
        _reset_game_daily(cid)
    _game_set_views.pop(token, None)
    await send_game_set(bot, cid, q=q)


async def send_favorite_games_added_card(bot, cid, items):
    items = [dict(item) for item in items or [] if isinstance(item, dict)]
    if len(items) != 1:
        await send_game_set(bot, cid)
        return
    item = items[0]
    genres = [str(value) for value in item.get("genres") or [] if str(value)]
    item["genre_label"] = _game_genre_title(genres[0]) if genres else "Без жанра"
    if item.get("platforms"):
        item = _decorate_game(item, cid)
    item = await asyncio.to_thread(enrich_favorite_game, item)
    item["platform_labels"] = [
        _PLATFORM_LABEL[key] for key in item.get("platforms") or [] if key in _PLATFORM_LABEL
    ]
    genres = [value for value in item.get("genres") or [] if value != "board"]
    item["genre_label"] = (
        _game_genre_title(genres[0]) if genres else
        ("Настолки" if "board" in (item.get("platforms") or []) else "Без жанра")
    )
    msg = leisure_ui.favorite_game_added_card(item)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎚️ Мой набор игр", callback_data="vg_set")],
        nav_row("lz_lib"),
    ])
    if item.get("poster"):
        try:
            await bot.send_photo(chat_id=cid, photo=item["poster"], caption=msg.text,
                                 caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            _log.debug("send_favorite_games_added_card: ignored error", exc_info=True)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


def _manual_game_choice(token, cid):
    state = _manual_game_choices.get(token)
    if (not state or state.get("cid") != str(cid)
            or time.time() - float(state.get("created_at") or 0) > _MANUAL_GAME_CHOICE_TTL):
        _manual_game_choices.pop(token, None)
        return None
    return state


def _prepare_manual_game_card(item):
    card = dict(item or {})
    genres = card.get("genres") or []
    card["genre_label"] = _game_genre_title(genres[0]) if genres else ""
    card["platform_labels"] = [_PLATFORM_LABEL[key] for key in card.get("platforms") or []
                               if key in _PLATFORM_LABEL]
    return card


async def _show_manual_game_candidate(bot, cid, token, index, *, q=None):
    state = _manual_game_choice(token, cid)
    if state is None:
        await bot.send_message(chat_id=cid, text="Выбор устарел. Добавь игру ещё раз.")
        return
    choices = state.get("choices") or []
    index = int(index) % len(choices)
    card = _prepare_manual_game_card(choices[index])
    msg = leisure_ui.game_set_card(card)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Добавить игру", callback_data=f"game_add_ok:{token}:{index}"),
        InlineKeyboardButton("✨ Другая игра", callback_data=f"game_add_next:{token}:{index}"),
    ]])
    if q is not None:
        try:
            await q.edit_message_media(
                media=InputMediaPhoto(media=card["poster"], caption=msg.text,
                                      caption_entities=msg.entities), reply_markup=kb,
            )
            state["current_index"] = index
            return
        except Exception:
            _log.debug("_show_manual_game_candidate: ignored error", exc_info=True)
    await bot.send_photo(chat_id=cid, photo=card["poster"], caption=msg.text,
                         caption_entities=msg.entities, reply_markup=kb)
    state["current_index"] = index


async def offer_manual_favorite_game(bot, cid, value, origin="base"):
    choices = await asyncio.to_thread(igdb.search_game_candidates, value)
    if not choices:
        prefix = "loveaddls" if origin == "leisure" else "loveadd"
        store.pending_input[str(cid)] = f"{prefix}_games"
        await bot.send_message(chat_id=cid, text=(
            "Не получилось найти подтверждённую игру с обложкой. "
            "Уточни название или год выпуска."
        ))
        return
    token = secrets.token_hex(4)
    _manual_game_choices[token] = {
        "cid": str(cid), "origin": origin, "created_at": time.time(),
        "choices": choices, "current_index": 0,
    }
    await _show_manual_game_candidate(bot, cid, token, 0)


async def handle_manual_game_add_callback(bot, cid, q, data):
    try:
        action, token, raw_index = str(data or "").split(":", 2)
        index = int(raw_index)
    except ValueError:
        return
    state = _manual_game_choice(token, cid)
    if state is None:
        await bot.send_message(chat_id=cid, text="Выбор устарел. Добавь игру ещё раз.")
        return
    choices = state.get("choices") or []
    current = int(state.get("current_index", index))
    if action == "game_add_next":
        if current + 1 >= len(choices):
            prefix = "loveaddls" if state.get("origin") == "leisure" else "loveadd"
            store.pending_input[str(cid)] = f"{prefix}_games"
            await bot.send_message(chat_id=cid, text=(
                "Других подтверждённых вариантов не нашлось. Уточни название или год выпуска."
            ))
            return
        await _show_manual_game_candidate(bot, cid, token, current + 1, q=q)
        return
    if action != "game_add_ok" or not 0 <= current < len(choices):
        return
    item = choices[current]
    _manual_game_choices.pop(token, None)
    existing = {_favorite_game_name(value).casefold() for value in _favorite_games(cid)}
    already = item["name"].casefold() in existing
    if not already:
        store.add_to_list(config.FAVORITE_GAMES_KEY, cid, item)
        _reset_game_daily(cid)
    await send_favorite_games_added_card(bot, cid, [item])


def _genre_keyboard(board=False):
    prefix = "vg_gb_" if board else "vg_g_"
    buttons = [InlineKeyboardButton(label, callback_data=f"{prefix}{key}") for key, label in GAME_GENRES]
    rows = [[button] for button in buttons]
    if not board:  # премьеры — видеоигры, у настолок новинки нет
        rows.insert(0, [InlineKeyboardButton("🆕 Новинка", callback_data="nov_game")])
    rows.append(nav_row("m_leisure"))
    return InlineKeyboardMarkup(rows)


async def _deliver(bot, cid, msg, markup, *, q=None, status=None):
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=markup,
                             disable_web_page_preview=True)
        return
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=markup,
                                      disable_web_page_preview=True)
            return
        except Exception:
            _log.debug("_deliver: ignored error", exc_info=True)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=markup, disable_web_page_preview=True)


async def send_game_recommendation(
    bot, cid, *, q=None, status=None, refresh=False, genre=None, board=False,
):
    item = None
    if inclusive_recommendations.is_due(cid, "game"):
        candidate = next((
            value for value in _eligible_games(cid, genre=genre, board=board)
            if inclusive_recommendations.is_inclusive("game", value.get("name"))
        ), None)
        if candidate:
            item = _decorate_game({**candidate, "lgbt": True}, cid, genre=genre, board=board)
    item = item or pick_game(cid, genre=genre, refresh=refresh, board=board)
    if item:
        store.last_recos[str(cid)] = {"kind": "game", "items": [item["name"]], "genre": genre, "board": board}
    if board or genre == "board":
        await _send_board_game(bot, cid, item, genre=genre, q=q, status=status)
        return
    if item:
        item = await asyncio.to_thread(igdb.enrich_game_recommendation, item)
        item = _ensure_game_trailer_url(item)
        inclusive = bool(item.get("lgbt")) or inclusive_recommendations.is_inclusive(
            "game", item.get("name"),
        )
        item["lgbt"] = inclusive
        inclusive_recommendations.record(cid, "game", inclusive)
    msg = leisure_ui.game_card(item)
    markup = _game_keyboard(no_match=not item, genre=genre, board=board)
    poster = str(item.get("poster") or "").strip() if item else ""
    if poster:
        try:
            await bot.send_photo(
                chat_id=cid,
                photo=poster,
                caption=msg.text,
                caption_entities=msg.entities,
                reply_markup=markup,
            )
            return
        except Exception:
            _log.debug("send_game_recommendation: ignored error", exc_info=True)
    await _deliver(bot, cid, msg, markup, q=q, status=status)


async def _send_board_game(bot, cid, item, *, genre=None, q=None, status=None):
    """Настолки — текстовая карточка в стиле бота, без обложки и IGDB."""
    if item:
        item = _ensure_game_trailer_url(item)
        inclusive = bool(item.get("lgbt")) or inclusive_recommendations.is_inclusive(
            "game", item.get("name"),
        )
        inclusive_recommendations.record(cid, "game", inclusive)
        year = int(item.get("year") or 0)
        item = {
            **item,
            "lgbt": inclusive,
            "genre_labels": [
                _GENRE_LABEL.get(value, value)
                for value in item.get("genres") or [] if value != "board"
            ][:2],
            "is_new": year == datetime.now(config.TZ).year,
        }
    msg = leisure_ui.board_game_card(item)
    markup = _game_keyboard(no_match=not item, genre=genre, board=True)
    await _deliver(bot, cid, msg, markup, q=q, status=status)


async def send_game_genres(bot, cid, q=None, board=False):
    await _deliver(bot, cid, leisure_ui.game_genres_screen(), _genre_keyboard(board=board), q=q)


def _preferences_keyboard(cid):
    selected = set(game_platforms(cid))
    recency = _game_recency(cid)
    rating = str(settings.get(cid, "game_min_rating", "") or "")
    rows = [[InlineKeyboardButton(
        ("✅ " if key in selected else "□ ") + label,
        callback_data=f"set_game_platform_{key}",
    )] for key, label in GAME_PLATFORMS]
    rows.extend([[InlineKeyboardButton(
        ("✅ " if recency == value else "□ ") + label,
        callback_data=f"set_game_recency_{value or 'any'}",
    )] for label, value in _GAME_RECENCY_OPTIONS])
    rows.extend([[InlineKeyboardButton(
        ("✅ " if rating == value else "□ ") + f"⭐ {label}",
        callback_data=f"set_game_rating_{value}",
    )] for label, value in _GAME_RATING_OPTIONS])
    rows.append(nav_row("vg_set"))
    return InlineKeyboardMarkup(rows)


async def send_game_preferences(bot, cid, q=None):
    recency_labels = dict((value, label) for label, value in _GAME_RECENCY_OPTIONS)
    rating = _game_min_rating(cid)
    msg = leisure_ui.game_preferences(
        game_platform_labels(cid),
        recency_labels.get(_game_recency(cid), "Любые годы"),
        f"от {rating:.1f}" if rating is not None else "любая",
    )
    await _deliver(bot, cid, msg, _preferences_keyboard(cid), q=q)


async def toggle_game_platform(bot, cid, platform, q=None):
    valid = {key for key, _label in GAME_PLATFORMS}
    if platform in valid:
        selected = game_platforms(cid)
        if platform in selected:
            selected = [key for key in selected if key != platform]
        else:
            selected.append(platform)
        settings.set_(cid, "game_platforms", selected)
        _reset_game_daily(cid)
    await send_game_preferences(bot, cid, q=q)


def _reset_game_daily(cid):
    def change(profile):
        profile.pop("game_daily", None)
        return profile, None

    store.mutate_profile(cid, change)


async def toggle_game_recency(bot, cid, value, q=None):
    if value in {"new", "2020s", "classic", "any"}:
        settings.set_(cid, "game_recency", "" if value == "any" else value)
        _reset_game_daily(cid)
    await send_game_preferences(bot, cid, q=q)


async def toggle_game_rating(bot, cid, value, q=None):
    valid = {option for _label, option in _GAME_RATING_OPTIONS}
    if value in valid:
        current = str(settings.get(cid, "game_min_rating", "") or "")
        settings.set_(cid, "game_min_rating", "" if current == value else value)
        _reset_game_daily(cid)
    await send_game_preferences(bot, cid, q=q)


def _premiere_cache_get(signature, today, *, allow_stale=False):
    data = store._load(config.GAME_PREMIERES_CACHE_KEY) or {}
    entry = data.get(signature) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("version") != _GAME_PREMIERES_VERSION:
        return None
    if bool(entry.get("igdb_configured")) != igdb.configured():
        return None
    try:
        expires = date.fromisoformat(str(entry.get("expires") or ""))
    except ValueError:
        return None
    if expires < today and not allow_stale:
        return None
    items = entry.get("items")
    return [dict(item) for item in items] if isinstance(items, list) else None


def _premiere_cache_set(signature, today, items):
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[signature] = {
            "version": _GAME_PREMIERES_VERSION,
            "igdb_configured": igdb.configured(),
            "expires": (today + timedelta(days=7)).isoformat(),
            "items": [dict(item) for item in items],
        }
        return data, None
    store.mutate_kv(config.GAME_PREMIERES_CACHE_KEY, mutate)


def _premiere_date_label(value):
    months = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return ""
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


def _normalize_premieres(payload, source_urls, selected_platforms, start_date, end_date=None):
    raw_items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(raw_items, list):
        return []
    aliases = {
        "pc": "pc", "пк": "pc", "windows": "pc",
        "ps5": "ps5", "playstation 5": "ps5",
        "xbox": "xbox", "xbox series": "xbox", "xbox series x|s": "xbox",
        "switch": "switch", "nintendo switch": "switch", "switch 2": "switch",
        "mobile": "mobile", "ios": "mobile", "android": "mobile", "мобильные": "mobile",
        "board": "board", "board game": "board", "настолки": "board",
    }
    result, seen = [], set()
    latest = end_date or (start_date + timedelta(days=180))
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())
        url = str(item.get("url") or "").strip()
        try:
            release = date.fromisoformat(str(item.get("date") or ""))
        except ValueError:
            continue
        platforms = []
        for value in item.get("platforms") or []:
            normalized = aliases.get(str(value).strip().casefold())
            if normalized and normalized not in platforms:
                platforms.append(normalized)
        if (not title or title.casefold() in seen or url not in source_urls
                or not (start_date <= release <= latest)
                or not set(platforms).intersection(selected_platforms)):
            continue
        seen.add(title.casefold())
        summary = " ".join(str(item.get("summary") or "").split())
        if summary and summary[-1] not in ".!?…":
            summary += "."
        result.append({
            "title": title,
            "date": release.isoformat(),
            "date_label": _premiere_date_label(release.isoformat()),
            "platforms": platforms,
            "platform_label": " · ".join(_PLATFORM_LABEL[key] for key in platforms),
            "genre": " ".join(str(item.get("genre") or "").split()),
            "summary": summary,
            "url": url,
        })
    result.sort(key=lambda item: item["date"])
    return result[:8]


def _game_season(today):
    if today.month in (12, 1, 2):
        start_year = today.year if today.month == 12 else today.year - 1
        return date(start_year, 12, 1), date(start_year + 1, 3, 1) - timedelta(days=1), "зимы"
    if today.month in (3, 4, 5):
        return date(today.year, 3, 1), date(today.year, 5, 31), "весны"
    if today.month in (6, 7, 8):
        return date(today.year, 6, 1), date(today.year, 8, 31), "лета"
    return date(today.year, 9, 1), date(today.year, 11, 30), "осени"


def _rotated_season_items(items, today):
    rows = list(items or [])
    if len(rows) <= 3:
        return rows
    offset = today.toordinal() % len(rows)
    return [rows[(offset + index) % len(rows)] for index in range(3)]


def _premiere_signature(cid, start_date, end_date):
    return f"{_platform_signature(cid)}:{start_date.isoformat()}:{end_date.isoformat()}"


def cached_season_premieres(cid, *, allow_stale=True):
    """Сезонная витрина игр из кэша (до 3 на день, по кругу); без сети. None — кэша нет."""
    today = datetime.now(config.TZ).date()
    start_date, end_date, _season = _game_season(today)
    items = _premiere_cache_get(
        _premiere_signature(cid, start_date, end_date), today, allow_stale=allow_stale,
    )
    if items is None:
        return None
    return [
        {**item, "trailer_url": str(item.get("trailer_url") or "").strip()
         or _youtube_trailer_search_url(item.get("title"))}
        for item in _rotated_season_items(items, today)
    ]


async def get_game_premieres(cid, *, refresh=False, seasonal=False):
    today = datetime.now(config.TZ).date()
    if seasonal:
        start_date, end_date, season = _game_season(today)
    else:
        start_date, end_date, season = today, today + timedelta(days=180), ""
    signature = _premiere_signature(cid, start_date, end_date)
    cached = _premiere_cache_get(signature, today)
    if cached is not None:
        return cached
    if not refresh:
        return _premiere_cache_get(signature, today, allow_stale=True) or []

    search_labels = {
        "pc": "PC Windows", "ps5": "PlayStation 5",
        "xbox": "Xbox Series X S", "switch": "Nintendo Switch Switch 2",
        "mobile": "iOS Android mobile games", "board": "board games",
    }
    platform_labels = [label for key, label in GAME_PLATFORMS if key in _effective_platforms(cid)]
    search_platforms = " ".join(search_labels[key] for key in _effective_platforms(cid))
    query = (
        f"most popular notable game releases {season} {start_date.year} {search_platforms}"
        if seasonal else f"upcoming game release dates {search_platforms} {today.year}"
    )
    sources = await asyncio.to_thread(
        research.web_search, query, 8,
        scenario="game_releases", allow_tavily=True, search_priority="tavily",
    )
    sources = [item for item in sources if item.get("url") and (item.get("content") or item.get("title"))]
    if not sources:
        items = await asyncio.to_thread(
            igdb.get_upcoming_games, set(_effective_platforms(cid)), today=start_date,
            days=(end_date - start_date).days,
        )
        for item in items:
            item["date_label"] = _premiere_date_label(item.get("date"))
        if items:
            _premiere_cache_set(signature, today, items)
        return items
    source_urls = {str(item["url"]).strip() for item in sources}
    source_text = "\n---\n".join(
        f"URL: {item['url']}\n{item.get('title', '')}\n{str(item.get('content') or '')[:700]}"
        for item in sources
    )[:7000]
    prompt = (
        f"Сегодня {today.isoformat()}. Извлеки подтверждённые релизы игр "
        f"с {start_date.isoformat()} по {end_date.isoformat()}. "
        + ("Верни до 8 самых популярных и заметных. " if seasonal else "") +
        f"для платформ: {', '.join(platform_labels)}. Используй только факты и URL из материалов. "
        "Не придумывай дату, платформу или ссылку. Название оставь официальным, genre и summary пиши по-русски; "
        "summary — одно короткое предложение без рекламы.\n"
        f"{secure.wrap_untrusted(source_text, 'материалы о релизах игр')}\n"
        'JSON без markdown: {"items":[{"title":"Название","date":"YYYY-MM-DD",'
        '"platforms":["pc","ps5","xbox","switch","mobile","board"],"genre":"жанр",'
        '"summary":"одно предложение","url":"точный URL из материалов"}]}'
    )
    try:
        payload = await ai.allm_json(
            prompt, 1400, tier="cheap", module="leisure_games",
            fallback_allowed=True, privacy_level="public",
            cache_context={
                "scenario": "game_season" if seasonal else "game_premieres",
                "start": start_date.isoformat(), "end": end_date.isoformat(),
                "platforms": sorted(_effective_platforms(cid)), "sources": sorted(source_urls),
                "schema_version": 1,
            },
        )
    except Exception:
        payload = {}
    items = _normalize_premieres(
        payload, source_urls, set(_effective_platforms(cid)), start_date, end_date,
    )
    if not items:
        items = await asyncio.to_thread(
            igdb.get_upcoming_games, set(_effective_platforms(cid)), today=start_date,
            days=(end_date - start_date).days,
        )
        for item in items:
            item["date_label"] = _premiere_date_label(item.get("date"))
    if items:
        items = await asyncio.to_thread(igdb.enrich_game_premieres, items)
        _premiere_cache_set(signature, today, items)
    return items


async def warm_game_premieres_cache(cid):
    """Обновляет премьерную витрину для платформ пользователя перед рассылкой."""
    await get_game_premieres(cid, refresh=True)


async def send_game_premieres(bot, cid, *, status=None):
    items = await get_game_premieres(cid)
    if not items:
        items = await get_game_premieres(cid, refresh=True)
    items = [
        item for item in items
        if str(item.get("poster") or "").strip()
    ][:7]
    _GAME_PREMIERE_VIEWS[str(cid)] = items
    msg, markup, page = _game_premiere_view(cid, 0)
    if items:
        await bot.send_photo(
            chat_id=cid, photo=items[page]["poster"], caption=msg.text,
            caption_entities=msg.entities, reply_markup=markup,
        )
        return
    await _deliver(bot, cid, msg, markup, status=status)


def _game_premiere_view(cid, page=0):
    items = _GAME_PREMIERE_VIEWS.get(str(cid)) or []
    page = max(0, min(int(page), len(items) - 1)) if items else 0
    msg = leisure_ui.game_premieres_screen([items[page]] if items else [])
    rows = []
    if len(items) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"game_premiere_page:{(page - 1) % len(items)}"),
            InlineKeyboardButton(f"{page + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"game_premiere_page:{(page + 1) % len(items)}"),
        ])
    rows.append(nav_row("lz_prem"))
    return msg, InlineKeyboardMarkup(rows), page


async def show_game_premiere_page(cid, q, page):
    items = _GAME_PREMIERE_VIEWS.get(str(cid)) or []
    if not items:
        return
    msg, kb, page = _game_premiere_view(cid, page)
    await q.edit_message_media(
        media=InputMediaPhoto(media=items[page]["poster"], caption=msg.text,
                              caption_entities=msg.entities),
        reply_markup=kb,
    )
