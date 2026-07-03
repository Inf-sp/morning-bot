import asyncio
from datetime import datetime
import html
import logging
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store

_log = logging.getLogger(__name__)
import ai
import rerank
import util
import verify
import secure
from ui import balance as balance_ui
from ui import food as food_ui
import memory
import settings

TZ = config.TZ

# ===== Холодильник: категории =====
# Порядок dict определяет приоритет матчинга. Сначала более специфичные группы,
# потом широкие, чтобы "масло сливочное" и "сок апельсиновый" не улетали не туда.
_FRIDGE_KEYWORDS: dict = {
    "заморозка": [
        "заморож", "замороз", "frozen", "diepvries",
        "картофель фри", "картошка фри", "фри",
        "пельмен", "вареник", "равиоли", "драник",
        "kipnugget", "nugget", "наггет", "пицц",
    ],
    "мясо и рыба": [
        # мясо и птица
        "курич", "курен", "куриц", "курин", "chicken", "kip", "kipfilet", "kippen",
        "говядин", "свинин", "фарш", "индейк", "баранин",
        "сосис", "колбас", "ветчин", "бекон", "салями", "утк", "кролик", "стейк", "котлет",
        "шашлык", "карбонад", "окорок", "грудин", "вырезк", "мортаделл",
        "крылыш", "желудоч",
        "gehakt", "rund", "varken",
        # рыба и морепродукты
        "рыб", "лосос", "сёмг", "семг", "тунец", "треска", "сельд", "сёлд", "селед",
        "скумбри", "форел", "икр", "креветк", "мидии", "кальмар", "осьминог", "краб",
        "шпрот", "сардин", "анчоус", "палтус", "минтай", "хек", "судак", "карп",
        "тиляпи", "дорад", "сибас", "haring", "zalm", "tonijn", "kabeljauw", "garnalen",
    ],
    "овощи": [
        "помидор", "томат", "огурец", "огурц", "морков", "репчат",
        "лук", "чеснок", "перец болгар", "перец свеж", "картофел", "картошк",
        "брокколи", "цукини", "кабачок", "баклажан", "шпинат", "салат",
        "капуст", "свёкл", "свекл", "сельдерей", "петрушк", "укроп",
        "кинза", "базилик", "рукол", "горошек", "кукуруз",
        "редис", "тыкв", "артишок", "спаржа", "порей", "фенхел",
        "авокадо", "имбир", "пастернак", "топинамбур", "дайкон",
        "коул слоу", "кол слоу", "coleslaw", "cole slaw",
        "komkommer", "tomaat", "tomaten", "wortel", "kool",
        "bloemkool", "aardappel", "champignon", "paddenstoel", "гриб", "шампиньон",
    ],
    "фрукты": [
        "яблок", "банан", "апельсин", "лимон", "лайм", "мандарин", "груш",
        "слива", "сливы", "сливу", "персик", "нектарин", "абрикос",
        "ягод", "малин", "клубник", "черник", "виноград", "киви",
        "манго", "ананас", "смородин", "вишн", "черешн",
        "папайя", "гранат", "инжир", "хурм",
        "дын", "арбуз", "клюкв", "голубик", "брусник", "ежевик",
        "appel", "banaan", "sinaasappel", "peer", "druif", "druiven",
    ],
    "молочное и яйца": [
        # проверяем до фруктов — иначе 'сливочное' матчит 'слив' из фруктов
        "масло слив", "сливочн", "молок", "кефир", "йогурт", "творог",
        "сметан", "сливк", "сыр", "пармезан", "моцарелл", "рикотт",
        "бри", "камамбер", "фет", "гауд", "эдам", "чеддер", "халум",
        "ряженк", "варенец", "айран", "кумыс", "яйц",
        "melk", "kaas", "eieren", "yoghurt", "boter",
    ],
    "крупы и макароны": [
        "рис", "греч", "гречк", "овсянк", "овёс", "макарон", "спагетт", "паст", "лапш",
        "хлопь", "киноа", "булгур", "кускус", "перловк", "пшен", "чечевиц",
        "нут", "фасол", "горох", "боб", "ячмен", "полба", "амарант",
        "вермишел", "пенне", "фетучин", "тальятелл",
        "rijst", "havermout", "noedel", "noodles",
        "мук", "крахмал", "тток", "tteok", "topokki", "yopokki", "булугур",
    ],
    "хлеб и выпечка": [
        "хлеб", "батон", "булочк", "тост", "лаваш", "пита",
        "лепёшк", "лепешк", "багет", "чиабатт", "круасс",
        "бублик", "сушк", "хлебц", "хрустящ", "afbakbrood", "broodjes", "brood",
        "тесто",
    ],
    "напитки": [
        # проверяем до фруктов — иначе 'сок апельсиновый' матчит 'апельсин' из фруктов
        "чай", "кофе", "сок", "морс", "компот", "квас", "лимонад",
        "минерал", "газировк", "энергетик", "пиво", "вино", "сидр",
        "какао", "смузи", "напиток", "cola",
    ],
    "снеки и сладости": [
        "печень", "шоколад", "конфет", "батончик", "чипс", "снэк", "сухар",
        "печен", "пирог", "торт", "кекс", "десерт", "морожен",
        "орех", "миндаль", "фисташ", "кешью", "фундук", "арахис",
        "пряник", "вафл", "мармелад", "зефир",
        "noten", "chips", "koek", "koekje", "drop", "hagelslag",
    ],
    "специи и соусы": [
        "соль", "сахар", "специ", "приправ", "соус", "уксус", "горчиц", "кетчуп",
        "майонез", "соев", "песто", "тахин", "хумус",
        "масло оливк", "масло растит", "масло подсолн", "подсолнечн", "масло кунжут",
        "перец чёрн", "перец черн", "перец молот", "паприк", "карри", "куркум",
        "корица", "ваниль", "лавров", "орегано", "тимьян", "розмарин",
        "мускат", "чили", "острый соус",
        "мёд", "мед", "варень", "джем", "конфитюр",
        "томатная паст", "бульон", "кубик", "намаз", "tom yam", "tom kha",
        "peper", "zout",
    ],
}
_CAT_EMOJI: dict = {
    "мясо и рыба": "🥩", "овощи": "🥦", "фрукты": "🍎", "молочное и яйца": "🥛",
    "крупы и макароны": "🍝", "хлеб и выпечка": "🍞", "специи и соусы": "🧂",
    "напитки": "🥤", "снеки и сладости": "🍪", "заморозка": "❄️",
    "прочее": "📦",
}
# Короткие названия для кнопок (чтобы помещались в 2 столбца)
_CAT_BTN_LABEL: dict = {
    "мясо и рыба": "Мясо/рыба", "молочное и яйца": "Молочное",
    "крупы и макароны": "Крупы/паста", "хлеб и выпечка": "Хлеб/выпечка",
    "специи и соусы": "Специи", "снеки и сладости": "Снеки",
    "заморозка": "Заморозка",
}
_CAT_ORDER = [
    "мясо и рыба", "овощи", "фрукты", "молочное и яйца",
    "крупы и макароны", "хлеб и выпечка", "напитки", "специи и соусы",
    "снеки и сладости", "заморозка", "прочее",
]

# Устаревшие категории → новые (для миграции существующих записей)
_CAT_REMAP = {
    "мясо":     "мясо и рыба",
    "рыба":     "мясо и рыба",
    "молочное": "молочное и яйца",
    "крупы":    "крупы и макароны",
    "хлеб":     "хлеб и выпечка",
    "специи":   "специи и соусы",
    "напитки":  "напитки",
    "сладости": "снеки и сладости",
    "снеки":    "снеки и сладости",
    "заморозка": "заморозка",
    "замороженное": "заморозка",
}
_CAT_VALID = frozenset(_CAT_ORDER)
_FRIDGE_FALLBACK_TARGET = {
    "овощи": "овощи",
    "фрукты": "фрукты",
    "молочное и яйца": "молочное и яйца",
    "крупы и макароны": "крупы и макароны",
    "хлеб и выпечка": "хлеб и выпечка",
    "напитки": "напитки",
    "специи и соусы": "специи и соусы",
    "снеки и сладости": "снеки и сладости",
    "мясо и рыба": "мясо и рыба",
    "заморозка": "заморозка",
    "прочее": "прочее",
}

FRIDGE_MIN_CAT = 3  # минимум продуктов для отдельной кнопки категории

_FRIDGE_NOT_PRODUCT_PATTERNS = [
    "редкие покупки", "редкая покупка", "редко покуп", "покупки", "купить",
    "редкие продукты", "редкий продукт",
    "список", "категория", "прочее", "другое",
    "fresh market", "oost-europese supermarkt", "бумажные полотенца",
]
_FRIDGE_NOT_PRODUCT_EXACT = {
    "свежий", "свежая", "копченый", "копчёный", "мягкое", "мягкий",
    "мороженый", "мороженая", "мороженые", "замороженный", "замороженная",
    "японские",
    "продукты", "продукт", "мой холодильник", "холодильник",
    "ah", "jumbo", "deka", "lidl",
}

_FRIDGE_CAT_EXACT = {
    "айсберг": "овощи",
    "батончики": "снеки и сладости",
    "желудочки": "мясо и рыба",
    "крахмал": "крупы и макароны",
    "крылышки": "мясо и рыба",
    "салями": "мясо и рыба",
    "томатная паста": "специи и соусы",
    "подсолнечное масло": "специи и соусы",
    "кокосовое молоко": "специи и соусы",
    "намазка": "специи и соусы",
}

_FRIDGE_NAME_EXACT = {
    "кол слоу": "коул слоу",
    "коул слоу": "коул слоу",
    "cole slaw": "коул слоу",
    "coleslaw": "коул слоу",
    "koolsla": "коул слоу",
    "гречу": "гречка",
    "гречка": "гречка",
    "селедка": "сельдь",
    "селёдка": "сельдь",
    "сельдь": "сельдь",
    "фри": "картофель фри",
    "картошка фри": "картофель фри",
    "курица ножки": "куриные ножки",
    "ножки куриные": "куриные ножки",
    "куриные ножки": "куриные ножки",
    "копченая курица": "копчёная курица",
    "копчёная курица": "копчёная курица",
    "afbakbroodjes": "булочки",
    "картофельные драники": "драники",
    "драники": "драники",
    "авокадо": "авокадо",
    "крабовые палочки": "крабовые палочки",
    "свежий хлеб": "хлеб",
    "багет": "багет",
    "яйца": "яйца",
    "черри-томаты": "томаты",
    "черри томаты": "томаты",
    "томаты": "томаты",
    "огурцы": "огурцы",
    "редис": "редис",
    "перец": "перец",
    "котлета для бургера": "котлета",
    "молоко": "молоко",
    "чеснок": "чеснок",
    "лук": "лук",
    "спагетти": "спагетти",
    "макароны": "макароны",
    "соленый огурец": "солёный огурец",
    "солёный огурец": "солёный огурец",
    "сыр": "сыр",
    "зеленый горошек": "горошек",
    "зелёный горошек": "горошек",
    "рис для плова": "рис",
    "рис": "рис",
    "йогурт греческий": "греческий йогурт",
    "греческий йогурт": "греческий йогурт",
    "батончики": "батончики",
    "намаз на хлеб": "намазка",
    "орехи": "орехи",
    "креветки": "креветки",
    "креветки в кляре": "креветки",
    "зерновой творог": "творог",
    "творог": "творог",
    "кукуруза": "кукуруза",
    "фасоль": "фасоль",
    "айсберг": "айсберг",
    "коул-слоу": "коул слоу",
    "hagelslag": "hagelslag",
    "фруктовый hagelslag": "hagelslag",
    "замороженные овощи": "замороженные овощи",
    "картофель": "картофель",
    "масло сливочное": "сливочное масло",
    "сливочное масло": "сливочное масло",
    "плавленый сыр": "плавленый сыр",
    "рисовая лапша": "рисовая лапша",
    "кокосовое молоко": "кокосовое молоко",
    "кинза": "кинза",
    "петрушка": "петрушка",
    "укроп": "укроп",
    "зеленый лук": "зелёный лук",
    "зелёный лук": "зелёный лук",
    "фарш": "фарш",
    "крылышки": "крылышки",
    "желудочки": "желудочки",
    "куриные желудочки": "желудочки",
    "киви": "киви",
    "виноград": "виноград",
    "мандарины": "мандарины",
    "яблоки": "яблоки",
    "бананы": "бананы",
    "черешня": "черешня",
    "говядина": "говядина",
    "тесто для гедз": "тесто гедза",
    "подсолнечное масло": "подсолнечное масло",
    "зеленый чай": "зелёный чай",
    "зелёный чай": "зелёный чай",
    "сырки": "сырки",
    "пельмени": "пельмени",
    "майонез": "майонез",
    "пряники": "пряники",
    "сосиски": "сосиски",
    "салями закарпатская": "салями",
    "салями": "салями",
    "приправа для плова": "приправа",
    "сухарики": "сухарики",
    "крутоны": "сухарики",
    "сахар": "сахар",
    "соль": "соль",
    "topokki": "ттокпокки",
    "tteokbokki": "ттокпокки",
    "yopokki": "ттокпокки",
    "булугур": "булгур",
    "булгур": "булгур",
    "kipnuggets": "наггетсы",
    "джем": "джем",
    "пицца": "пицца",
    "куриный бульон": "бульон",
    "приправа": "приправа",
    "лавровы лист": "лавровый лист",
    "лавровый лист": "лавровый лист",
    "вино": "вино",
    "ветчина mortadella": "мортаделла",
    "mortadella": "мортаделла",
    "мука": "мука",
    "томатная паста": "томатная паста",
    "крахмал": "крахмал",
}

_FRIDGE_SPLIT_PREFIXES = {
    "фрукты": ["киви", "виноград", "мандарины", "яблоки", "бананы", "черешня"],
    "курица": ["куриные ножки", "крылышки", "желудочки"],
    "салатную смесь": ["айсберг", "коул слоу"],
}

_FRIDGE_LINE_SPLITS = {
    "картофельные драники, фри": ["драники", "картофель фри"],
    "свежий или копченый лосось": ["лосось"],
    "свежий или копчёный лосось": ["лосось"],
    "свежий хлеб или багет": ["хлеб", "багет"],
    "масло сливочное или мягкое": ["сливочное масло"],
    "спагетти и макароны": ["спагетти", "макароны"],
}

_FRIDGE_BRAND_PATTERNS = [
    r"\s+-\s+.*$",
    r"\s+из\s+ah\b.*$",
    r"\s+из\s+jumbo\b.*$",
    r"\s+deka\b.*$",
    r"\s+lidl\b.*$",
    r"\s+bonne maman\b.*$",
    r"\s+crosta\s*&\s*mollica\b.*$",
    r"\s+da michele\b.*$",
    r"\s+ah\b.*$",
    r"\s+\d+[,.]\d+\s*€.*$",
]


def _fridge_normalize_input(text: str) -> str:
    """Подготовить пользовательский список: HTML/маркированный текст -> строки продуктов."""
    t = html.unescape(str(text or ""))
    t = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", t)
    t = re.sub(r"(?i)<\s*/\s*li\s*>", "\n", t)
    t = re.sub(r"(?i)<\s*li[^>]*>", "\n", t)
    t = re.sub(r"(?i)<\s*/?\s*(ul|ol)[^>]*>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return t


def _fridge_cat(name: str) -> str:
    """Определить категорию продукта по ключевым словам."""
    n = name.lower()
    if n in _FRIDGE_CAT_EXACT:
        return _FRIDGE_CAT_EXACT[n]
    if n in ("ui", "uien", "sla", "kropsla"):
        return "овощи"
    if n == "ham":
        return "мясо и рыба"
    if n in ("water", "mineraalwater"):
        return "напитки"
    if n == "watermeloen":
        return "фрукты"
    if n in ("paprika", "rode paprika", "gele paprika", "groene paprika"):
        return "овощи"
    if "перец" in n:
        if any(k in n for k in ("чёрн", "черн", "молот", "душист", "чили", "cayenne", "кайен")):
            return "специи и соусы"
        return "овощи"
    if "koolsla" in n or "coleslaw" in n or "cole slaw" in n or "коул слоу" in n or "кол слоу" in n:
        return "овощи"
    for cat, keywords in _FRIDGE_KEYWORDS.items():
        if any(k in n for k in keywords):
            return cat
    return "прочее"


def _fridge_clean_name(name: str) -> str:
    """Приводит продукт к одному читаемому названию без смены смысла."""
    n = _fridge_normalize_input(name)
    n = re.sub(r"\s+", " ", str(n).lower().strip(" -—:•\t"))
    n = re.sub(r"^[^\wа-яё]+", "", n, flags=re.IGNORECASE).strip()
    for pattern in _FRIDGE_BRAND_PATTERNS:
        n = re.sub(pattern, "", n).strip()
    n = re.sub(r"\([^)]*\)", "", n).strip()
    if n in _FRIDGE_NAME_EXACT:
        return _FRIDGE_NAME_EXACT[n]
    if n.startswith("свежий ") or n.startswith("свежая "):
        n = re.sub(r"^свеж(ий|ая|ее|ие)\s+", "", n).strip()
    if n.startswith("мороженый ") or n.startswith("мороженая ") or n.startswith("мороженые "):
        n = re.sub(r"^морожен(ый|ая|ое|ые)\s+", "", n).strip()
    if "koolsla" in n or "coleslaw" in n or "cole slaw" in n or "коул слоу" in n or "кол слоу" in n:
        return "коул слоу"
    if "греч" in n:
        return "гречка"
    if "карто" in n and "фри" in n:
        return "картофель фри"
    if "кур" in n and "нож" in n:
        return "куриные ножки"
    if "коп" in n and "кур" in n:
        return "копчёная курица"
    if "сельд" in n or "селед" in n or "селёд" in n:
        return "сельдь"
    if "лосос" in n:
        return "лосось"
    if "кревет" in n:
        return "креветки"
    if "краб" in n and "палоч" in n:
        return "крабовые палочки"
    if "котлет" in n:
        return "котлета"
    if "пицц" in n:
        return "пицца"
    if "тток" in n or "topokki" in n or "tteok" in n or "yopokki" in n:
        return "ттокпокки"
    if "томат" in n and "паст" in n:
        return "томатная паста"
    if "фасол" in n:
        return "фасоль"
    if "кукуруз" in n:
        return "кукуруза"
    if "том" in n and ("ям" in n or "кха" in n):
        return "том ям"
    if "бульон" in n:
        return "бульон"
    if "масло" in n and "подсолн" in n:
        return "подсолнечное масло"
    if "масло" in n and ("слив" in n or "мягк" in n):
        return "сливочное масло"
    words = n.split()
    if len(words) > 2:
        n = " ".join(words[:2])
    return n


def _fridge_split_input(text: str) -> list[str]:
    """Разбивает пользовательский список на отдельные продукты."""
    out: list[str] = []
    for raw_line in _fridge_normalize_input(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower().strip(" -—:•\t")
        if not _fridge_is_product(low):
            continue
        if low in _FRIDGE_LINE_SPLITS:
            out.extend(_FRIDGE_LINE_SPLITS[low])
            continue
        for prefix, items in _FRIDGE_SPLIT_PREFIXES.items():
            if low.startswith(prefix):
                out.extend(items)
                break
        else:
            inner = re.findall(r"\(([^)]*)\)", low)
            line_no_parens = re.sub(r"\([^)]*\)", "", low)
            chunks = [line_no_parens]
            chunks.extend(inner)
            for chunk in chunks:
                chunk = re.sub(r"\s+-\s+.*$", "", chunk).strip()
                parts = re.split(r",|;|/|\s+или\s+|\s+и\s+", chunk)
                for part in parts:
                    name = _fridge_clean_name(part)
                    if _fridge_is_product(name):
                        out.append(name)
    result = []
    seen = set()
    for name in out:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            result.append(name)
    return result


def _fridge_reject_reason(line: str) -> str:
    """Коротко объясняет, почему строка из большого списка не стала продуктом."""
    low = str(line).lower().strip(" -—:•\t")
    if not low:
        return ""
    if any(p in low for p in ("fresh market", "oost-europese supermarkt")):
        return "это название магазина/раздела"
    if "бумажные полотенца" in low:
        return "это не продукт"
    if "редкие покупки" in low or "редкие продукты" in low:
        return "это заголовок, а не продукт"
    if any(p in low for p in ("список", "категория", "прочее", "другое")):
        return "это служебная строка"
    if low in _FRIDGE_NOT_PRODUCT_EXACT:
        return "это уточнение без продукта"
    return ""


def _fridge_rejected_lines(text: str) -> list[tuple[str, str]]:
    rejected = []
    for raw_line in _fridge_normalize_input(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        reason = _fridge_reject_reason(line)
        if reason:
            rejected.append((_fridge_clean_name(line), reason))
    return rejected


def _fridge_is_product(name: str) -> bool:
    """Отсекает заголовки/пояснения, которые пользователь мог вставить вместе со списком."""
    n = name.lower().strip(" -—:•\t")
    if not n or len(n) < 2:
        return False
    if any(p in n for p in _FRIDGE_NOT_PRODUCT_PATTERNS):
        return False
    if n in _FRIDGE_NOT_PRODUCT_EXACT:
        return False
    return True


def _fridge_migrate(items: list) -> list:
    """Конвертировать старые строки в {name, cat, on}. Мигрирует устаревшие категории."""
    result = []
    for it in items:
        if isinstance(it, dict):
            names = _fridge_split_input(str(it.get("name", ""))) or [_fridge_clean_name(it.get("name", ""))]
            for name in names:
                if not _fridge_is_product(name):
                    continue
                detected = _fridge_cat(name)
                result.append({**it, "name": name, "cat": detected})
        else:
            for s in _fridge_split_input(str(it)):
                if not _fridge_is_product(s):
                    continue
                result.append({"name": s, "cat": _fridge_cat(s), "on": True})
    dedup = {}
    for it in result:
        key = it["name"].casefold()
        if key in dedup:
            dedup[key]["on"] = bool(dedup[key].get("on", True) or it.get("on", True))
        else:
            dedup[key] = it
    return list(dedup.values())


def _fridge_by_cat_display(items: list) -> dict:
    """Словарь cat → [(global_idx, item)] для отображения.
    Категории с менее чем FRIDGE_MIN_CAT продуктами сливаются в 'прочее'."""
    by_cat = _fridge_by_cat(items)
    result: dict = {cat: [] for cat in _CAT_ORDER}
    for cat in _CAT_ORDER:
        for gi, it in by_cat.get(cat, []):
            target = cat if len(by_cat.get(cat, [])) >= FRIDGE_MIN_CAT or cat == "прочее" else _FRIDGE_FALLBACK_TARGET.get(cat, "прочее")
            result[target].append((gi, it))
    return {cat: sorted(items, key=lambda x: x[1].get("name", "").casefold()) for cat, items in result.items() if items}


def _fridge_available(items: list) -> list:
    """Имена продуктов с on=True (для рецепта)."""
    return [it["name"] for it in _fridge_migrate(items) if it.get("on", True)]

def _food_card(d, label="Рецепт дня"):
    """Единый формат карточки рецепта для радара и нового рецепта."""
    return food_ui.food_card(d, label=label)

DOCTOR_INTRO = (
    "👩🏻‍⚕️ Врач\n\n"
    "Дам общую справочную информацию о здоровье и лекарствах. Это не диагноз и не назначение - "
    "при тревожных симптомах обратись к специалисту.\n\n"
    "Опиши, что беспокоит, или спроси про лекарство 👇"
)

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def _clean_card_text(value):
    return balance_ui.clean_card_text(value)

def _finish_dot(value):
    return balance_ui.finish_dot(value)

def _build_entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Рекомендации:"):
    msg = balance_ui.entity_card(title, summary, quote, bullets, final, bullet_label)
    return msg.text, msg.entities

# универсальная клавиатура под ответом: [Продолжить][Короче|Глубже][⭐][В меню]
def _ans_kb(cont_label="🔄 Продолжить", cont_cb="chat_retry", depth=True):
    rows = []
    if cont_label and cont_cb:
        rows.append([(cont_label, cont_cb)])
    if depth:
        rows.append([("✂️ Короче", "ans_short"), ("🔬 Глубже", "ans_deep")])
    rows.append([("⏳ Позже", "as_fav"), ("◀️ Назад", "m_close")])
    return _kb(rows)

def _recipe_kb():
    return _kb([
        [("✨ Ещё рецепт", "as_food")],
        [("◀️ Назад", "m_close")],
    ])

def _recipe_typed_kb():
    """Клавиатура после «Новый рецепт» — только выбор типа приёма пищи."""
    return _kb([
        [("🍳 Завтрак", "a_recipe_breakfast"), ("🥗 Обед", "a_recipe_lunch"), ("🍽️ Ужин", "a_recipe_dinner")],
        [("◀️ Назад", "m_food")],
    ])

def _fridge_recipe_kb():
    return _kb([
        [("🔄 Заменить", "as_fridge_cook")],
        [("◀️ Назад", "m_food")],
    ])

def _back_kb():
    return _kb([[("◀️ Назад", "m_close")]])


async def _send(bot, cid, text, kb=None, surface="card"):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    text, _w = verify.grade_text(text, surface)   # health->дисклеймер, chat->≤1 эмодзи
    for w in _w:
        print(f"[verify] {surface}: {w}")
    store.last_answer[str(cid)] = text
    store.last_source.setdefault(str(cid), "Ассистент")
    store.last_surface[str(cid)] = surface       # для «Короче/Глубже»
    html = util.tg_html(text)
    chunks = [html[i:i+4000] for i in range(0, len(html), 4000)]
    for i, c in enumerate(chunks):
        markup = (kb if kb is not None else _ans_kb()) if i == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=c, parse_mode="HTML", reply_markup=markup)
        except Exception:
            # если HTML невалиден - отправляем как обычный текст, без падения
            await bot.send_message(chat_id=cid, text=c, reply_markup=markup)


_LEFTOVER_RECENT_LIMIT = 12

def _leftover_recent(cid):
    """Последние названия блюд из остатков — для anti-repeat в промпте."""
    return store.get_list(config.LEFTOVER_RECIPES_SEEN_KEY, cid)

def _leftover_remember(cid, name):
    """Добавляет название в историю anti-repeat, храня не больше _LEFTOVER_RECENT_LIMIT штук."""
    if not name:
        return
    recent = _leftover_recent(cid)
    recent = [n for n in recent if n.lower() != name.lower()] + [name]
    store.set_list(config.LEFTOVER_RECIPES_SEEN_KEY, cid, recent[-_LEFTOVER_RECENT_LIMIT:])


# ---------- Кулинарный радар ----------
def _my_recipe_pref(cid):
    """Контекст из базы рецептов для промпта (первые 5 названий)."""
    if not cid:
        return ""
    saved = store.get_list(config.MY_RECIPES_KEY, str(cid))[:5]
    names = ", ".join(r.get("name", "") for r in saved if r.get("name"))
    return f"Пользователь любит готовить: {names}. Похожий стиль приветствуется.\n" if names else ""


def _gen_recipe(constraint, cid=None):
    pref = _my_recipe_pref(cid)
    pr = (settings.priority_context(cid) + "\n") if cid and settings.priority_context(cid) else ""
    return ai.llm_json(
        f"{pr}{pref}Ты — шеф-повар с идеальной логикой. "
        f"Создай 1 рецепт ({constraint}), 1 человек, электрическая плита, духовка SAGE.\n"
        "Правила:\n"
        "• Каждый продукт из ингредиентов обязан появиться в шагах приготовления.\n"
        "• Не меняй технику без веской причины: начал на сковороде — не гони в духовку. Минимум посуды.\n"
        "• Сумма минут по шагам должна строго равняться полю time.\n"
        "• В каждом шаге: глагол в повелительном наклонении + конкретика (минуты, уровень огня, крышка).\n"
        "• 3–5 шагов. Один шаг — одно-два действия. Без вводных слов и описаний вкуса.\n"
        "• В ингредиентах всегда добавляй базу (масло, соль, перец), если нужна для готовки.\n"
        'JSON (без markdown): {"name":"Название блюда","time":"X мин","servings":"1 порц.",'
        '"ingredients":"список через запятую",'
        '"steps":["Глагол + действие + конкретика","шаг 2","шаг 3"],'
        '"full":"тот же рецепт в том же стиле: сначала заголовок, затем <b>Ингредиенты</b>, затем <b>Приготовление</b>, затем <b>😋 Приятного аппетита!</b>. '
        'Без времени и порции, без лишнего текста."}',
        900, tier="cheap")

def _recipe_card(d):
    return _food_card(d, label="Рецепт дня")

async def send_recipe(bot, cid, constraint="обычное блюдо"):
    try:
        d = await asyncio.to_thread(_gen_recipe, constraint, cid=cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", constraint)
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card.text
    await bot.send_message(chat_id=cid, text=card.text, entities=card.entities, reply_markup=_recipe_kb())

async def send_recipe_featured(bot, cid):
    """Новый рецепт из меню — под результатом кнопки завтрак/обед/ужин."""
    try:
        d = await asyncio.to_thread(_gen_recipe, "любое блюдо под вкус пользователя", cid=cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", "featured")
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card.text
    await bot.send_message(chat_id=cid, text=card.text, entities=card.entities, reply_markup=_recipe_typed_kb())

async def send_recipe_push(bot, cid):
    """Уведомление 12:30 — без кнопок."""
    try:
        d = await asyncio.to_thread(_gen_recipe, "любое блюдо под вкус пользователя", cid=cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card.text
    await bot.send_message(chat_id=cid, text=card.text, entities=card.entities)


def _gen_leftovers_recipe(ingredients, cid=None):
    avoid = _leftover_recent(cid) if cid else []
    avoid_line = f"Не предлагай снова: {', '.join(avoid)}.\n" if avoid else ""
    return ai.llm_json(
        f"{avoid_line}Есть продукты: {secure.wrap_untrusted(ingredients, 'продукты')}. "
        "Предложи 1 простой рецепт только из них (+ базовые специи, максимум 1 доп продукт). 1 человек.\n"
        'JSON: {"name":"название","time":"X мин","servings":"1 порц.",'
        '"ingredients":"список использованных продуктов через запятую",'
        '"steps":["шаг 1 (до 15 слов)","шаг 2","шаг 3"]}',
        500, tier="cheap")

async def send_leftovers(bot, cid, ingredients):
    try:
        d = await asyncio.to_thread(_gen_leftovers_recipe, ingredients, cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("leftovers", ingredients)
    _leftover_remember(cid, d.get("name", ""))
    card = _food_card(d, label="Рецепт из холодильника")
    store.last_source[str(cid)] = "Питание · Остатки"
    store.last_answer[str(cid)] = card.text
    await bot.send_message(chat_id=cid, text=card.text, entities=card.entities, reply_markup=_fridge_recipe_kb())


_FRIDGE_PAGE = 8  # продуктов на страницу в категории


def _fridge_by_cat(items: list) -> dict:
    """Словарь cat → [(global_idx, item)] для отображения."""
    by_cat: dict = {}
    for i, it in enumerate(items):
        cat = it.get("cat", "прочее")
        by_cat.setdefault(cat, []).append((i, it))
    return by_cat


# ---------- Мой холодильник: главный экран (категории) ----------
async def send_fridge(bot, cid, q=None, back="m_food"):
    cid_s = str(cid)
    raw = store.get_list(config.FRIDGE_KEY, cid_s)
    items = _fridge_migrate(raw)
    if items != raw:
        store.set_list(config.FRIDGE_KEY, cid_s, items)

    if not items:
        msg = food_ui.fridge_home_empty()
        rows = [
            [InlineKeyboardButton("✏️ Добавить продукты", callback_data="as_fridge_add")],
            [InlineKeyboardButton("◀️ Назад", callback_data=back)],
        ]
    else:
        available = sum(1 for it in items if it.get("on", True))
        by_cat = _fridge_by_cat_display(items)
        msg = food_ui.fridge_home(len(items), available)
        present_cats = [c for c in _CAT_ORDER if c in by_cat]
        cat_btns = []
        for ci, cat in enumerate(present_cats):
            cat_items = by_cat[cat]
            on_cnt = sum(1 for _, it in cat_items if it.get("on", True))
            emoji = _CAT_EMOJI.get(cat, "📦")
            label = _CAT_BTN_LABEL.get(cat, cat.capitalize())
            cat_btns.append(InlineKeyboardButton(
                f"{emoji} {label} {on_cnt}/{len(cat_items)}",
                callback_data=f"as_fridge_cat_{ci}_0"
            ))
        rows = [[
            InlineKeyboardButton("✏️ Добавить", callback_data="as_fridge_add"),
            InlineKeyboardButton("❌ Удалить", callback_data="as_fridge_clean"),
        ]]
        rows.extend([[btn] for btn in cat_btns])
        rows.append([InlineKeyboardButton("◀️ Назад", callback_data=back)])

    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ---------- Экран категории (пагинация + toggle + отдельная чистка) ----------
async def send_fridge_cat(bot, cid, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    by_cat = _fridge_by_cat_display(items)

    # Определяем имя категории по индексу в present_cats (с учётом мержа малых)
    present_cats = [c for c in _CAT_ORDER if c in by_cat]
    if cat_idx >= len(present_cats):
        await send_fridge(bot, cid, q); return
    cat = present_cats[cat_idx]
    cat_items = by_cat[cat]  # [(global_idx, item)]

    total = len(cat_items)
    pages = max(1, (total + _FRIDGE_PAGE - 1) // _FRIDGE_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = cat_items[page * _FRIDGE_PAGE:(page + 1) * _FRIDGE_PAGE]

    emoji = _CAT_EMOJI.get(cat, "📦")
    on_cnt = sum(1 for _, it in cat_items if it.get("on", True))
    msg = food_ui.fridge_category(emoji, cat.capitalize(), total, on_cnt)

    # Один продукт в строку: названия должны читаться полностью.
    rows = [[
        InlineKeyboardButton("✏️ Добавить", callback_data=f"as_fridge_add_{cat_idx}"),
        InlineKeyboardButton("❌ Удалить", callback_data="as_fridge_clean"),
    ]]
    for gi, it in chunk:
        mark = "🟢" if it.get("on", True) else "⚪"
        name_short = it["name"][:40]
        rows.append([
            InlineKeyboardButton(f"{mark} {name_short}", callback_data=f"as_fridge_tgl_{gi}_{cat_idx}_{page}")
        ])

    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"as_fridge_cat_{cat_idx}_{(page-1) % pages}"),
            InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"as_fridge_cat_{cat_idx}_{(page+1) % pages}"),
        ])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="as_fridge_home")])

    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def fridge_add_done(bot, cid, text, cat_idx: int = -1):
    cid_s = str(cid)
    items_new = _fridge_split_input(text)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    existing = {it["name"].lower() for it in items}
    added = []
    duplicates = []
    for name in items_new:
        key = name.lower()
        if name and key not in existing:
            cat = _fridge_cat(name)
            items.append({"name": name, "cat": cat, "on": True})
            existing.add(key)
            added.append(name)
        elif name:
            duplicates.append(name)
    store.set_list(config.FRIDGE_KEY, cid_s, items)
    added_by_cat = {}
    for name in added:
        added_by_cat.setdefault(_fridge_cat(name), []).append(name)
    rejected = _fridge_rejected_lines(text)
    msg = food_ui.fridge_updated(added_by_cat, added, duplicates, rejected, _CAT_ORDER, _CAT_EMOJI, _CAT_BTN_LABEL)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    if cat_idx >= 0:
        await send_fridge_cat(bot, cid, cat_idx, 0)
    else:
        await send_fridge(bot, cid)


def _fridge_payload_from_chat(text: str) -> str:
    raw = str(text or "").strip()
    low = raw.lower()
    if "<li" in low and "продукт" in low:
        return _fridge_normalize_input(raw)

    patterns = [
        r"(?:добавь|добавить|закинь|запиши|сохрани)\s+"
        r"(?:это\s+)?(?:в\s+)?(?:список\s+)?(?:моих\s+)?"
        r"(?:продуктов|продукты|холодильник)\s*[:\-—]?\s*(.+)",
        r"(?:в\s+)?(?:продукты|холодильник)\s*[:\-—]\s*(.+)",
        r"🛒\s*продукты\s*[:\-—]?\s*(.+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


async def try_add_fridge_from_chat(bot, cid, text) -> bool:
    payload = _fridge_payload_from_chat(text)
    if not payload:
        return False
    if not _fridge_split_input(payload):
        return False
    await fridge_add_done(bot, cid, payload)
    return True


async def fridge_toggle(bot, cid, idx: int, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    if 0 <= idx < len(items):
        items[idx]["on"] = not items[idx].get("on", True)
        store.set_list(config.FRIDGE_KEY, cid_s, items)
    await send_fridge_cat(bot, cid, cat_idx, page, q)


async def fridge_del(bot, cid, idx: int, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    if 0 <= idx < len(items):
        items.pop(idx)
        store.set_list(config.FRIDGE_KEY, cid_s, items)
    await send_fridge_cat(bot, cid, cat_idx, page, q)


async def send_fridge_recipe(bot, cid):
    raw = store.get_list(config.FRIDGE_KEY, str(cid))
    available = _fridge_available(raw)
    if not available:
        msg = food_ui.fridge_empty_for_recipe()
        await bot.send_message(chat_id=cid, text=msg.text)
        return
    await send_leftovers(bot, cid, ", ".join(available))


# ---------- База рецептов ----------
async def save_my_recipe(bot, cid):
    cid_s = str(cid)
    d = store.last_recipe.get(cid_s)
    if not d or not d.get("name"):
        await bot.send_message(chat_id=cid, text="Нет рецепта для сохранения."); return
    saved = store.get_list(config.MY_RECIPES_KEY, cid_s)
    names_lower = [r.get("name", "").lower() for r in saved]
    if d["name"].lower() in names_lower:
        await bot.send_message(chat_id=cid, text=f"«{util.esc(d['name'])}» уже есть в твоих рецептах."); return
    store.add_to_list(config.MY_RECIPES_KEY, cid_s, d)
    await bot.send_message(chat_id=cid, text=f"❤️ «{util.esc(d['name'])}» сохранён в базе рецептов.")


async def send_my_recipes(bot, cid):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if not recipes:
        msg = food_ui.my_recipes_empty()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="as_notes")]])
    else:
        msg = food_ui.my_recipes_list(recipes)
        rows = []
        for i, r in enumerate(recipes):
            name = r.get("name", f"Рецепт {i+1}")[:30]
            rows.append([InlineKeyboardButton(f"📖 {name}", callback_data=f"as_my_recipe_{i}")])
        rows.insert(0, [InlineKeyboardButton("❌ Удалить", callback_data="as_recipe_clean")])
        rows.append([InlineKeyboardButton("◀️ Назад", callback_data="as_notes")])
        kb = InlineKeyboardMarkup(rows)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_my_recipe_full(bot, cid, idx):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if idx >= len(recipes):
        await bot.send_message(chat_id=cid, text="Рецепт не найден."); return
    d = recipes[idx]
    store.last_recipe[cid_s] = d
    card = _food_card(d, label="Рецепт")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить из базы", callback_data=f"as_my_recipe_del_{idx}")],
        [InlineKeyboardButton("◀️  к списку", callback_data="as_my_recipes")],
    ])
    await bot.send_message(chat_id=cid, text=card.text, entities=card.entities, reply_markup=kb)


async def my_recipe_del(bot, cid, idx):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if idx < len(recipes):
        name = recipes[idx].get("name", "рецепт")
        recipes.pop(idx)
        store.set_list(config.MY_RECIPES_KEY, cid_s, recipes)
        await bot.send_message(chat_id=cid, text=f"❌ «{util.esc(name)}» удалён из базы рецептов.")
    await send_my_recipes(bot, cid)


# ---------- СДВГ / Следующий шаг ----------
def _pick_lagom(cid) -> str:
    """Берёт один неиспользованный Лагом-принцип, при исчерпании — сбрасывает счётчик."""
    import memory
    items = memory.get_lagom(cid)
    if not items:
        return ""
    seen = store.get_list(config.MOTIV_LAGOM_SEEN_KEY, cid)
    unused = [i for i in range(len(items)) if i not in seen]
    if not unused:
        seen = []
        unused = list(range(len(items)))
        store.set_list(config.MOTIV_LAGOM_SEEN_KEY, cid, [])
    import random
    idx = random.choice(unused)
    seen.append(idx)
    store.set_list(config.MOTIV_LAGOM_SEEN_KEY, cid, seen)
    return items[idx]

def _gen_motiv(cid):
    import random
    lagom = _pick_lagom(cid)
    angles = ["физическое действие", "ограничение", "мини-ритуал", "перезагрузку", "один микрошаг"]
    angle = random.choice(angles)
    lagom_ctx = f"Принцип лагома пользователя: «{lagom}»\n" if lagom else ""
    priority_ctx = f"{settings.priority_context(cid)}\n" if settings.priority_context(cid) else ""
    prompt = (
        f"{priority_ctx}"
        f"{lagom_ctx}"
        f"Предложи {angle} на основе этого принципа. "
        "Без философии и клише. Конкретно, коротко, на русском. "
        "Верни JSON (без markdown):\n"
        '{"steps":["конкретное действие или ограничение","ещё одно если нужно"],'
        '"why":"1-2 предложения: зачем это работает прямо сейчас"}'
    )
    try:
        d = ai.llm_json(prompt, 300, tier="smart")
        steps = [str(s).strip() for s in (d.get("steps") or []) if str(s).strip()]
        why = str(d.get("why", "")).strip()
    except Exception:
        steps = ["Встань и пройди круг по комнате"]
        why = "Движение быстро снижает внутренний шум и помогает начать с малого"
    lagom_full = lagom if lagom else "Один шаг лучше идеального плана."
    return _build_entity_card(
        "Мотивация",
        lagom_full,
        why,
        steps,
        "Сделай первый шаг сейчас, без подготовки.",
        bullet_label="Действие:",
    )


async def send_motiv_push(bot, cid):
    """09:00 — плановая мотивация (без 'Секунду...')."""
    out, entities = _gen_motiv(cid)
    store.last_source[str(cid)] = "Баланс · Мотивация"
    store.last_answer[str(cid)] = out
    await bot.send_message(chat_id=cid, text=out, entities=entities)


# ---------- роли ----------
def _role_system(role):
    if role == "state":
        return ("Ты спокойный помощник по состоянию, фокусу и мотивации ( психотерапевт). "
                "Выслушай, разложи ситуацию на 1-3 конкретных шага, поддержи коротко. Без воды, с эмодзи. "
        )
    if role == "doctor":
        return ("Ты помощник по здоровью. Это справочная информация, не диагноз. "
                "Отвечай кратко и верни строго валидный JSON без markdown:\n"
                "{\"title\":\"Разбор симптомов\","
                "\"summary\":\"1 короткое предложение: основная жалоба\","
                "\"quote\":\"1-2 предложения: на что это может быть похоже, без диагноза\","
                "\"bullets\":[\"рекомендация\", \"когда срочно к врачу\"],"
                "\"final\":\"короткий безопасный итог с точкой\"}")
    return "Ты полезный ассистент."

_MED_RE = ("лекарств", "таблет", "препарат", "доз", "мг ", " мг", "метилфенидат", "ибупрофен",
           "парацетамол", "антибиотик", "капл", "сироп", "мазь", "витамин", "пилюл", "concerta",
           "ritalin", "риталин", "медикамент", "побочк", "побочн", "как принимать")

def _is_med_question(text):
    t = (text or "").lower()
    return any(k in t for k in _MED_RE)

def _med_system():
    return ("Ты помощник по лекарствам. Это справочная информация, не назначение. "
            "Не подбирай дозировку и схему. Верни строго валидный JSON без markdown:\n"
            "{\"title\":\"Разбор лекарства\","
            "\"summary\":\"1 короткое предложение: о каком препарате вопрос\","
            "\"quote\":\"1-2 предложения: зачем применяют и что важно знать\","
            "\"bullets\":[\"частая побочка или риск\", \"когда обратиться к врачу\", \"что уточнить у врача\"],"
            "\"final\":\"короткий безопасный итог с точкой\"}")

def _doctor_candidates(symptoms):
    data = ai.llm_json(
        f"Пользователь описал: {symptoms}\nДай 6 коротких справочных тезисов (общая информация о возможных "
        "причинах/состояниях при таких симптомах; НЕ диагноз). JSON: {\"items\": [\"тезис\", ...]}", 900, tier="cheap")
    return [x for x in data.get("items", []) if isinstance(x, str) and x.strip()]

def _fallback_health_card(title, user_text):
    return {
        "title": title,
        "summary": f"Запрос: {_clean_card_text(user_text)[:160]}",
        "quote": "По описанию нельзя поставить диагноз заочно, но можно оценить риски и ближайшие действия.",
        "bullets": [
            "Следи за усилением симптомов, температурой, дыханием, болью и общим состоянием",
            "Обратись к врачу срочно, если состояние быстро ухудшается или симптомы выраженные",
            "Не начинай лекарства и дозировки без инструкции врача или фармацевта",
        ],
        "final": "Это справочная информация, не диагноз и не назначение.",
    }

async def _send_health_card(bot, cid, data, kb=None):
    text, entities = _build_entity_card(
        data.get("title") or "Разбор симптомов",
        data.get("summary") or "",
        data.get("quote") or "",
        data.get("bullets") or [],
        data.get("final") or "Это справочная информация, не диагноз и не назначение.",
        bullet_label=data.get("bullet_label") or "Рекомендации:",
    )
    store.last_answer[str(cid)] = text
    store.last_source.setdefault(str(cid), "Здоровье")
    store.last_surface[str(cid)] = "health"
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=kb)

async def doctor_answer(bot, cid, symptoms):
    if secure.is_dangerous_med(symptoms):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health")
        return
    await bot.send_chat_action(chat_id=cid, action="typing")
    safe_symptoms = secure.wrap_untrusted(symptoms, "симптомы пользователя")
    if _is_med_question(symptoms):
        prompt = f"{_med_system()}\n\nВопрос про лекарство: {safe_symptoms}"
        try:
            d = await ai.allm_json(prompt, 900, route="claude", module="health")
        except Exception as e:
            _log.warning("doctor medicine AI failed, using fallback: %r", e, exc_info=True)
            d = _fallback_health_card("Разбор лекарства", symptoms)
        store.last_source[str(cid)] = "Здоровье · Лекарство"
        store.last_action[str(cid)] = ("role", "doctor", symptoms)
        await _send_health_card(bot, cid, d, kb=_ans_kb(None, None, depth=False))
        return
    passages = []
    try:
        cands = await asyncio.to_thread(_doctor_candidates, symptoms)
        ranked = rerank.rerank(symptoms, cands, top_n=3)
        passages = [t for t, _ in ranked]
    except Exception:
        passages = []
    base = _role_system("doctor")
    if passages:
        ctx = "\n".join(f"- {p}" for p in passages)
        prompt = f"{base}\n\nНаиболее релевантные тезисы (по симптомам):\n{ctx}\n\nСимптомы: {safe_symptoms}"
    else:
        prompt = f"{base}\n\nСимптомы: {safe_symptoms}"
    try:
        d = await ai.allm_json(prompt, 900, route="claude", module="health")
    except Exception as e:
        _log.warning("doctor symptoms AI failed, using fallback: %r", e, exc_info=True)
        d = _fallback_health_card("Разбор симптомов", symptoms)
    store.last_source[str(cid)] = "Здоровье · Врач"
    store.last_action[str(cid)] = ("role", "doctor", symptoms)
    await _send_health_card(bot, cid, d, kb=_ans_kb(None, None, depth=False))

async def handle_role(bot, cid, role, text):
    if role == "doctor":
        await doctor_answer(bot, cid, text); return
    if secure.is_dangerous_med(text):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health"); return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        route = "claude" if role == "state" else "openrouter"
        out = await ai.allm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7, route=route)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_action[str(cid)] = ("role", role, text)
    cont = ("✨ Ещё совет", "chat_retry") if role == "state" else ("🔄 Продолжить", "chat_retry")
    await _send(bot, cid, out, kb=_ans_kb(*cont), surface="chat" if role == "state" else "card")


# ---------- Дневник тревоги ----------
async def send_daycheck(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)   # фикс: ответ не уйдёт в Обратный перевод
    store.game_state.pop(cid, None)
    worries = store.get_list(config.WORRIES_KEY, cid)
    msg = balance_ui.worries_diary(worries)
    store.pending_input[cid] = "worry"
    rows = []
    if worries:
        rows.append([InlineKeyboardButton("❌ Очистить все тревоги", callback_data="worry_clearall")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="m_close")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))

async def send_evening_review(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)
    store.game_state.pop(cid, None)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    all_worries = store.get_list(config.WORRIES_KEY, cid)
    worries = [w for w in all_worries if w.get("date", today) == today]
    if not worries:
        msg = balance_ui.evening_review_empty()
        await bot.send_message(chat_id=cid, entities=msg.entities, text=msg.text)
        store.pending_input[cid] = "worry"
        return
    wlist = "\n".join(f"- {w['text']}" for w in worries)
    try:
        d = await ai.allm_json(
            "Ты спокойный психолог. Разбери тревоги человека с СДВГ по-доброму, на русском.\n"
            "Нужно коротко, без медицинских назначений и без длинной поддержки.\n"
            "Для каждой тревоги дай одну короткую интерпретацию: что может быть фактом, а что предположением.\n"
            "Итог дня - 1 короткое предложение.\n"
            'Верни JSON: {"items":[{"worry":"тревога как есть","note":"коротко, до 20 слов"}],'
            '"summary":"короткий итог, до 22 слов"}\n\n'
            f"Тревоги:\n{wlist}", 700, 0.5)
    except Exception as e:
        _log.warning("send_evening_review: LLM failed, analysis empty: %s", e)
        d = {}
    items = d.get("items") or []
    summary = (d.get("summary") or "").strip()
    msg = balance_ui.evening_review(worries, items, summary)
    rows = [
        [InlineKeyboardButton("❌ Очистить все тревоги", callback_data="worry_clearall")],
    ]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))

async def worry_clear_all(bot, cid):
    cid = str(cid)
    worries = store.get_list(config.WORRIES_KEY, cid)
    if worries:
        summary = f"Разобрано тревог: {len(worries)}"
        store.add_to_list(config.DIARY_KEY, cid, {"date": datetime.now(TZ).strftime("%d.%m"), "text": summary})
    store.set_list(config.WORRIES_KEY, cid, [])
    msg = balance_ui.worries_cleared()
    await bot.send_message(chat_id=cid, text=msg.text)

async def save_worries(bot, cid, text):
    cid = str(cid)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    new = [{"text": w.strip(), "status": "pending", "date": today} for w in text.split("\n") if w.strip()]
    existing = store.get_list(config.WORRIES_KEY, cid)
    store.set_list(config.WORRIES_KEY, cid, existing + new)
    msg = balance_ui.worries_saved(len(new))
    await bot.send_message(chat_id=cid, text=msg.text)


_MOTIV_KB = _kb([[("✨ Ещё мотивации", "as_motiv")], [("◀️ Назад", "m_balance")]])

_ONESHOT = {}


# ---------- роутер кнопок Баланса ----------
async def handle_callback(bot, cid, q, data):
    # Кулинарный радар
    if data == "as_food":
        await util.ack_loading(q); await send_recipe(bot, cid, "обычное блюдо"); return

# дневник тревоги
    if data == "as_daycheck":
        await send_daycheck(bot, cid); return
    if data == "as_worryreview":
        await send_evening_review(bot, cid); return
    # мотивация
    if data == "as_motiv":
        await util.ack_loading(q)
        try:
            out, entities = _gen_motiv(cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        store.last_source[str(cid)] = "Баланс · Мотивация"
        store.last_answer[str(cid)] = out
        store.last_surface[str(cid)] = "card"
        await bot.send_message(chat_id=cid, text=out, entities=entities, reply_markup=_MOTIV_KB)
        return
    # одноразовая генерация (прочее)
    if data in _ONESHOT:
        gen, lbl, cb = _ONESHOT[data]
        await util.ack_loading(q)
        try:
            out = gen(cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        store.last_action[str(cid)] = ("oneshot", data)
        store.last_source[str(cid)] = {"as_motiv": "Здоровье · Мотивация"}.get(data, "Ассистент")
        await _send(bot, cid, out, kb=_ans_kb(lbl, cb))
        return
    # врач
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=DOCTOR_INTRO, reply_markup=_back_kb()); return
    # холодильник
    if data in ("as_fridge", "as_fridge_home"):
        await send_fridge(bot, cid, q); return
    if data.startswith("as_fridge_cat_"):
        parts = data.split("_")  # as_fridge_cat_{ci}_{page}
        try:
            await send_fridge_cat(bot, cid, int(parts[3]), int(parts[4]), q)
        except (ValueError, IndexError):
            await send_fridge(bot, cid, q)
        return
    if data.startswith("as_fridge_add_"):
        # добавление из категории: as_fridge_add_{ci}
        try:
            ci = int(data.split("_")[-1])
        except (ValueError, IndexError):
            ci = -1
        store.pending_input[str(cid)] = f"fridge_add_{ci}"
        await bot.send_message(chat_id=cid,
            text="✏️ Напиши продукты через запятую или с новой строки — добавлю в список.",
            reply_markup=_back_kb()); return
    if data == "as_fridge_add":
        store.pending_input[str(cid)] = "fridge_add_-1"
        await bot.send_message(chat_id=cid,
            text="✏️ Напиши продукты через запятую или с новой строки — добавлю в список.",
            reply_markup=_back_kb()); return
    if data == "as_fridge_cook":
        await util.ack_loading(q); await send_fridge_recipe(bot, cid); return
    if data == "as_fridge_clean":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "fridge"); return
    if data.startswith("as_fridge_tgl_"):
        # as_fridge_tgl_{idx}_{ci}_{page}
        parts = data.split("_")
        try:
            await fridge_toggle(bot, cid, int(parts[3]), int(parts[4]), int(parts[5]), q)
        except (ValueError, IndexError):
            await send_fridge(bot, cid, q)
        return
    if data.startswith("as_fridge_del_"):
        # as_fridge_del_{idx}_{ci}_{page}
        parts = data.split("_")
        try:
            await fridge_del(bot, cid, int(parts[3]), int(parts[4]), int(parts[5]), q)
        except (ValueError, IndexError):
            await send_fridge(bot, cid, q)
        return
    # база рецептов
    if data == "as_recipe_save":
        await save_my_recipe(bot, cid); return
    if data == "as_recipe_clean":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "recipes"); return
    if data == "as_my_recipes":
        await send_my_recipes(bot, cid); return
    if data.startswith("as_my_recipe_del_"):
        try:
            await my_recipe_del(bot, cid, int(data.split("_")[-1]))
        except (ValueError, IndexError):
            pass
        return
    if data.startswith("as_my_recipe_"):
        try:
            await send_my_recipe_full(bot, cid, int(data.split("_")[-1]))
        except (ValueError, IndexError):
            pass
        return


# ---------- «Продолжить» / «Ещё раз» ----------
async def retry(bot, cid):
    la = store.last_action.get(str(cid))
    if la and la[0] == "oneshot":
        gen, lbl, cb = _ONESHOT[la[1]]
        try:
            out = gen(cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        await _send(bot, cid, out, kb=_ans_kb(lbl, cb)); return
    if la and la[0] == "recipe":
        await send_recipe(bot, cid, la[1]); return
    if la and la[0] == "leftovers":
        await send_leftovers(bot, cid, la[1]); return
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
        await verify.safe_error(bot, cid, e); return
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
        await verify.safe_error(bot, cid, e); return
    await _send(bot, cid, out, surface=surface)
