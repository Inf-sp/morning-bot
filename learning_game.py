"""Языковая игра-детектив: состояние, генерация, ответы и подсказки."""

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import store
import verify
from ui import learning as learning_ui


def _code(language):
    if language in ("nl", "en"):
        return language
    return "nl" if language == "нидерландский" else "en"


def _language_for_code(code):
    return "английский" if code == "en" else "нидерландский"


def _active_language_code(cid):
    code = store.get_learning_language(cid)
    if code in ("nl", "en"):
        return code
    import settings
    return _code(settings.study_lang(cid))


def _flag(language):
    return "🇳🇱" if _code(language) == "nl" else "🇬🇧"


# ================= ИГРА-ДЕТЕКТИВ =================
# Текст игры локализован под активный язык обучения, а кнопки остаются русскими.
GAME_UI = {
    "русский": {
        "title": "Угадай персонажа · Русский",
        "reply_next": "Напиши ответ следующим сообщением — можно на любом языке.",
        "hint": "💡 Подсказка",
        "hint_title": "💡 Подсказка",
        "reveal": "😞 Сдаюсь",
        "found": "✅ Дело раскрыто!",
        "remember": "📚 Запомни:",
        "again": "✨ Ещё одна загадка",
        "back": "⬅️ Назад",
        "home": "#️⃣ Главная",
        "wrong": "❌ Не то",
        "retry": "Попробуй ещё один раз.",
    },
    "английский": {
        "title": "Угадай персонажа · English",
        "reply_next": "Напиши ответ следующим сообщением — можно на любом языке.",
        "hint": "💡 Подсказка",
        "hint_title": "💡 Hint",
        "reveal": "😞 Сдаюсь",
        "found": "✅ Case solved!",
        "remember": "📚 Remember:",
        "again": "✨ Ещё одна загадка",
        "back": "⬅️ Назад",
        "home": "#️⃣ Главная",
        "wrong": "❌ Not yet",
        "retry": "Try one more time.",
    },
    "нидерландский": {
        "title": "Угадай персонажа · Nederlands",
        "reply_next": "Напиши ответ следующим сообщением — можно на любом языке.",
        "hint": "💡 Подсказка",
        "hint_title": "💡 Hint",
        "reveal": "😞 Сдаюсь",
        "found": "✅ Zaak opgelost!",
        "remember": "📚 Onthoud:",
        "again": "✨ Ещё одна загадка",
        "back": "⬅️ Назад",
        "home": "#️⃣ Главная",
        "wrong": "❌ Niet juist",
        "retry": "Probeer nog één keer.",
    },
}


_GAME_CATEGORY_LABELS = {
    "animal": "животное",
    "food": "еда",
    "object": "предмет",
    "profession": "профессия",
    "transport": "транспорт",
    "place": "место",
    "character": "герой",
}

_GAME_CATEGORY_ALIASES = {
    "животное": "animal", "животные": "animal", "animal": "animal",
    "еда": "food", "продукт": "food", "продукты": "food", "food": "food",
    "блюдо": "food", "dish": "food",
    "предмет": "object", "object": "object",
    "профессия": "profession", "profession": "profession",
    "транспорт": "transport", "transport": "transport",
    "место": "place", "place": "place",
    "герой": "character", "персонаж": "character", "character": "character",
}

_GAME_REQUEST_FOCUSES = (
    ("very familiar animal", "animal"),
    ("very familiar fruit or vegetable", "food"),
    ("ordinary everyday object", "object"),
    ("very familiar animal", "animal"),
    ("very familiar fruit or vegetable", "food"),
    ("common type of transport", "transport"),
    ("common profession", "profession"),
    ("well-known place", "place"),
    ("very famous film or cartoon character", "character"),
)


def _game_category_key(value):
    """Normalise the generator's category to the small supported set."""
    return _GAME_CATEGORY_ALIASES.get(str(value or "").strip().casefold(), "")


def _game_category_label(data):
    category = _game_category_key(data.get("category"))
    if category:
        return _GAME_CATEGORY_LABELS[category]
    # Older cached/AI cards did not have a category. Keep those rounds useful
    # when their answer matches the local, curated catalogue.
    answer = data.get("answer", "")
    for cards in _LOCAL_GAME_CARDS.values():
        for card in cards:
            if _game_same(answer, card.get("answer", "")):
                return _GAME_CATEGORY_LABELS[card["category"]]
    return "другое"

def _game_ui(lang=None):
    return GAME_UI.get(lang) or GAME_UI["русский"]


def _dot(s):
    """Гарантирует точку в конце предложения/подсказки."""
    s = (s or "").strip()
    if s and s[-1] not in ".!?…:":
        s += "."
    return s


def _game_norm(s):
    return re.sub(r"[^0-9a-zа-яё]+", "", (s or "").lower())


def _game_same(a, b):
    a, b = _game_norm(a), _game_norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 5 and len(b) >= 5 and (a in b or b in a):
        return True
    if abs(len(a) - len(b)) <= 2:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        return diff <= 2
    return False


def _game_is_recent(d, recent):
    names = [d.get("answer", "")] + list(d.get("aliases") or [])
    return any(_game_same(name, old) for name in names for old in (recent or []))


def _game_recent(cid):
    """Все уже загаданные пользователю ответы и варианты названий.

    Ключ ``game_recent`` из старых профилей сохраняем как начальную историю,
    а новые раунды больше не вытесняют старые: повторять загадку нельзя.
    """
    prof = store.get_profile(cid)
    persisted = prof.get("game_seen", prof.get("game_recent", [])) if isinstance(prof, dict) else []
    mem = store.game_recent.get(str(cid), [])
    out = []
    for name in list(persisted) + list(mem):
        name = (name or "").strip()
        if name and not any(_game_same(name, old) for old in out):
            out.append(name)
    store.game_recent[str(cid)] = out
    return out


def _set_game_recent(cid, rec):
    rec = [str(x).strip() for x in (rec or []) if str(x).strip()]
    store.game_recent[str(cid)] = rec
    store.mutate_profile(cid, lambda profile: (
        {**profile, "game_seen": rec}, None,
    ))


def _remember_game_answer(cid, d):
    names = [d.get("answer", "")] + list(d.get("aliases") or [])
    rec = _game_recent(cid)
    for name in names:
        name = (name or "").strip()
        if name and not any(_game_same(name, old) for old in rec):
            rec.append(name)
    _set_game_recent(cid, rec)


_GAME_SIGNATURE_MARKERS = (
    "ус", "whisker", "snorhaar", "snorhar", "муз", "mouse", "mice", "muis", "хобот", "trunk", "slurf", "грива", "mane", "manen",
    "паутина", "web", "spinnen", "волшебн", "wand", "toverstaf", "зелён", "зелен", "green", "groen",
    "болот", "swamp", "moeras", "крыл", "wing", "vleugel", "чёрно-бел", "черно-бел", "black and white", "zwart-wit",
    "лёд", "лед", "ice", "ijs", "мёд", "мед", "honey", "honing", "маск", "mask", "gotham", "готэм",
    "hogwarts", "хогвартс", "arendelle", "аренд", "fiona", "фиона", "minnie", "минни", "mufasa", "муфаса",
    "дональд", "donald", "красн.*шорт", "red shorts", "rode broek", "осёл", "осел", "donkey",
    "картоф", "potato", "aardappel", "сыр", "cheese", "kaas", "колес", "wheel", "wiel", "руль", "steering wheel", "stuur",
    "водител", "driver", "chauffeur", "пилот", "pilot", "piloot", "врач", "doctor", "arts", "учител", "teacher", "leraar",
    "пожарн", "firefighter", "brandweer", "автобус", "bus", "поезд", "train", "trein", "самолёт", "самолет", "plane", "vliegtuig",
    "мяу", "meow", "miauw", "лаять", "bark", "blaf", "woof", "хвост", "tail", "staart", "лап", "paws", "poten",
    "печь", "oven", "fornuis", "экран", "screen", "scherm", "звон", "ring", "bellen", "больн", "sick", "ziek",
    "больниц", "hospital", "ziekenhuis", "пациент", "patient", "patiënt", "ученик", "student", "leerling",
)
_GAME_VAGUE_CLUE_MARKERS = (
    "активен ночью", "active at night", "'s nachts actief", "большие глаза", "big eyes", "grote ogen",
    "любит рыбу", "likes fish", "houdt van vis", "известн.*друг", "известн.*подруг", "known friend",
    "bekend vriend", "bekend vriendinnetje",
)


def _game_sentences(text):
    return [part.strip() for part in re.split(r"(?<=[.!?…])\s+", str(text or "").strip()) if part.strip()]


def _game_has_answer(text, answer, aliases=()):
    source = str(text or "").casefold()
    for value in [answer] + list(aliases or []):
        value = str(value or "").strip().casefold()
        if not value:
            continue
        words = [re.escape(word) for word in re.findall(r"[\wÀ-ÿА-яЁё]+", value)]
        if words and re.search(
            r"(?<![\wÀ-ÿА-яЁё])" + r"\s+".join(words) + r"(?![\wÀ-ÿА-яЁё])",
            source,
        ):
            return True
    return False


def _game_has_target_language(text, lang):
    # Русские переводы принадлежат только ALIASES и WORDS.
    return not re.search(r"[А-Яа-яЁё]", str(text or ""))


def _game_word_in_text(word, text):
    word = str(word or "").strip().casefold()
    source = str(text or "").casefold()
    if not word or not source:
        return False
    return word in source or any(
        _fuzzy(word, token)
        for token in re.findall(r"[a-zà-öø-ÿ]+", source)
    )


def _parse_game_words(raw, source_text):
    basic_words = {"ik", "i", "zijn", "be", "een", "a", "an", "the"}
    words = []
    for item in str(raw or "").split(";"):
        parts = [part.strip() for part in item.split("|", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            continue
        word, translation = parts
        if word.casefold() not in basic_words and _game_word_in_text(word, source_text):
            words.append({"word": word, "translation": translation})
    return words[:3]


_LOCAL_GAME_CARDS = {
    "нидерландский": (
        {
            "description": "Ik woon vaak bij mensen. Overdag slaap ik graag op warme plekken. Ik kan heel stil lopen en soms jaag ik op kleine dieren.",
            "answer": "de kat", "category": "animal", "aliases": ["кошка", "cat", "kat", "poes"], "answer_en": "cat",
            "hint": "Ik heb snorharen en ik zeg miauw.",
            "explain": "Een kat woont vaak bij mensen. Het dier heeft snorharen en jaagt soms op muizen.",
            "words": [{"word": "snorharen", "translation": "усы"}, {"word": "jagen", "translation": "охотиться"}],
        },
        {
            "description": "Ik woon vaak bij mensen en ik hou van wandelen. Ik hoor goed en ik kom snel als iemand mij roept. Soms bewaak ik het huis.",
            "answer": "de hond", "category": "animal", "aliases": ["собака", "dog", "hond"], "answer_en": "dog",
            "hint": "Ik heb een natte neus en ik blaf als ik blij ben.",
            "explain": "Een hond woont vaak bij mensen. Hij wandelt graag en kan het huis bewaken.",
            "words": [{"word": "wandelen", "translation": "гулять"}, {"word": "bewaken", "translation": "охранять"}],
        },
        {
            "description": "Ik ben rond en ik kom vaak uit de oven. Mensen delen mij graag met vrienden. Ik heb vaak tomaat en andere lekkere dingen bovenop. Je eet mij meestal warm.",
            "answer": "de pizza", "category": "food", "aliases": ["пицца", "pizza"], "answer_en": "pizza",
            "hint": "Ik heb vaak kaas en ik word in punten gesneden.",
            "explain": "Een pizza komt uit de oven en wordt vaak warm met kaas gegeten.",
            "words": [{"word": "oven", "translation": "духовка"}, {"word": "delen", "translation": "делить"}],
        },
        {
            "description": "Ik rijd elke dag door de stad en stop vaak onderweg. Veel mensen kunnen tegelijk met mij reizen. Je wacht meestal bij een halte voordat je instapt. Ik heb een vaste route.",
            "answer": "de bus", "category": "transport", "aliases": ["автобус", "bus"], "answer_en": "bus",
            "hint": "Ik heb grote wielen en deuren voor veel passagiers.",
            "explain": "Een bus rijdt een vaste route en neemt veel passagiers mee.",
            "words": [{"word": "halte", "translation": "остановка"}, {"word": "instappen", "translation": "садиться в транспорт"}],
        },
        {
            "description": "Ik werk vaak in een ziekenhuis of een praktijk. Mensen komen bij mij als zij ziek zijn of pijn hebben. Ik stel vragen en probeer hen te helpen. Soms schrijf ik een recept.",
            "answer": "de dokter", "category": "profession", "aliases": ["врач", "doctor", "dokter", "arts"], "answer_en": "doctor",
            "hint": "Ik onderzoek patiënten en luister naar hun klachten.",
            "explain": "Een dokter helpt zieke mensen en onderzoekt patiënten.",
            "words": [{"word": "pijn", "translation": "боль"}, {"word": "onderzoeken", "translation": "осматривать, исследовать"}],
        },
        {
            "description": "Ik leef in een warm land en ik ben heel groot. Ik eet planten en loop vaak samen met mijn familie. Mijn oren zijn groot en ik kan goed zwemmen. Ik ben sterk.",
            "answer": "de olifant", "category": "animal", "aliases": ["слон", "elephant", "olifant"], "answer_en": "elephant",
            "hint": "Ik heb een lange slurf en grote witte tanden.",
            "explain": "Een olifant is groot, heeft een slurf en leeft vaak met zijn familie.",
            "words": [{"word": "slurf", "translation": "хобот"}, {"word": "sterk", "translation": "сильный"}],
        },
    ),
    "английский": (
        {
            "description": "I often live with people. During the day I like to sleep in warm places. I can walk very quietly and sometimes hunt small animals.",
            "answer": "the cat", "category": "animal", "aliases": ["кошка", "cat", "kat", "poes"], "answer_en": "cat",
            "hint": "I have whiskers and I say meow.",
            "explain": "A cat often lives with people. It has whiskers and sometimes hunts mice.",
            "words": [{"word": "whiskers", "translation": "усы"}, {"word": "hunt", "translation": "охотиться"}],
        },
        {
            "description": "I often live with people and I like going for walks. I can hear very well and I come quickly when someone calls me. Sometimes I guard the house.",
            "answer": "the dog", "category": "animal", "aliases": ["собака", "dog", "hond"], "answer_en": "dog",
            "hint": "I have a wet nose and I say woof.",
            "explain": "A dog often lives with people. It likes walks and can guard the house.",
            "words": [{"word": "walk", "translation": "гулять"}, {"word": "guard", "translation": "охранять"}],
        },
        {
            "description": "I am round and often come from the oven. People like to share me with friends. I often have tomato and other tasty things on top. You usually eat me warm.",
            "answer": "the pizza", "category": "food", "aliases": ["пицца", "pizza"], "answer_en": "pizza",
            "hint": "I often have cheese and people cut me into slices.",
            "explain": "A pizza comes from the oven and people often eat it warm with cheese.",
            "words": [{"word": "oven", "translation": "духовка"}, {"word": "share", "translation": "делить"}],
        },
        {
            "description": "I drive through the city every day and stop many times. Many people can travel with me at once. You usually wait at a stop before you get in. I have a fixed route.",
            "answer": "the bus", "category": "transport", "aliases": ["автобус", "bus"], "answer_en": "bus",
            "hint": "I have big wheels and doors for many passengers.",
            "explain": "A bus follows a route and takes many passengers with it.",
            "words": [{"word": "route", "translation": "маршрут"}, {"word": "passenger", "translation": "пассажир"}],
        },
        {
            "description": "I often work in a hospital or a clinic. People come to me when they are sick or have pain. I ask questions and try to help them. Sometimes I write a prescription.",
            "answer": "the doctor", "category": "profession", "aliases": ["врач", "doctor", "dokter", "arts"], "answer_en": "doctor",
            "hint": "I examine patients and listen to their problems.",
            "explain": "A doctor helps sick people and examines patients.",
            "words": [{"word": "pain", "translation": "боль"}, {"word": "examine", "translation": "осматривать"}],
        },
        {
            "description": "I live in a warm country and I am very big. I eat plants and often walk with my family. My ears are large and I can swim well. I am strong.",
            "answer": "the elephant", "category": "animal", "aliases": ["слон", "elephant", "olifant"], "answer_en": "elephant",
            "hint": "I have a long trunk and big white teeth.",
            "explain": "An elephant is big, has a trunk and often lives with its family.",
            "words": [{"word": "trunk", "translation": "хобот"}, {"word": "strong", "translation": "сильный"}],
        },
    ),
}

_EXTRA_LOCAL_SUBJECTS = (
    ("animal", "rabbit", "konijn", "кролик", "I have long ears and I can jump very well.", "Ik heb lange oren en ik kan heel goed springen.", "I have a soft tail.", "Ik heb een zachte staart."),
    ("animal", "horse", "paard", "лошадь", "I can run fast and people can ride on my back.", "Ik kan snel rennen en mensen kunnen op mijn rug rijden.", "I have a long mane.", "Ik heb een lange manen."),
    ("animal", "cow", "koe", "корова", "I live on a farm and I eat grass every day.", "Ik woon op een boerderij en ik eet elke dag gras.", "People can get milk from me.", "Mensen kunnen melk van mij krijgen."),
    ("animal", "lion", "leeuw", "лев", "I am a large wild animal and I live with a group.", "Ik ben een groot wild dier en ik leef met een groep.", "The male has a big mane.", "Het mannetje heeft een grote manen."),
    ("animal", "giraffe", "giraf", "жираф", "I am very tall and I like to eat leaves from trees.", "Ik ben heel lang en ik eet graag bladeren van bomen.", "I have a very long neck.", "Ik heb een heel lange nek."),
    ("animal", "penguin", "pinguïn", "пингвин", "I live in cold places and I can swim very well.", "Ik leef op koude plekken en ik kan heel goed zwemmen.", "I am black and white and I cannot fly.", "Ik ben zwart en wit en ik kan niet vliegen."),
    ("animal", "turtle", "schildpad", "черепаха", "I move slowly and I can live for many years.", "Ik beweeg langzaam en ik kan heel veel jaren leven.", "I carry a hard shell on my back.", "Ik draag een hard schild op mijn rug."),
    ("animal", "bee", "bij", "пчела", "I am small and I fly from flower to flower.", "Ik ben klein en ik vlieg van bloem naar bloem.", "I make honey and I can sting.", "Ik maak honing en ik kan steken."),
    ("animal", "butterfly", "vlinder", "бабочка", "I am an insect and I often sit on flowers.", "Ik ben een insect en ik zit vaak op bloemen.", "I have colourful wings.", "Ik heb kleurrijke vleugels."),
    ("animal", "owl", "uil", "сова", "I am a bird and I am often awake at night.", "Ik ben een vogel en ik ben vaak wakker in de nacht.", "I have large eyes and I can fly quietly.", "Ik heb grote ogen en ik kan stil vliegen."),
    ("animal", "monkey", "aap", "обезьяна", "I am good at climbing and I live with other animals.", "Ik kan goed klimmen en ik leef met andere dieren.", "I like bananas and I have a long tail.", "Ik houd van bananen en ik heb een lange staart."),
    ("animal", "tiger", "tijger", "тигр", "I am a large wild cat and I can run very fast.", "Ik ben een grote wilde kat en ik kan heel snel rennen.", "I have dark stripes on my orange fur.", "Ik heb donkere strepen op mijn oranje vacht."),
    ("food", "apple", "appel", "яблоко", "People often eat me as a snack and I can be red or green.", "Mensen eten mij vaak als snack en ik kan rood of groen zijn.", "I grow on a tree.", "Ik groei aan een boom."),
    ("food", "banana", "banaan", "банан", "People often eat me as a snack and I am yellow.", "Mensen eten mij vaak als snack en ik ben geel.", "You remove my peel before eating me.", "Je haalt mijn schil eraf voordat je mij eet."),
    ("food", "carrot", "wortel", "морковь", "I am a vegetable and I am usually orange.", "Ik ben een groente en ik ben meestal oranje.", "I grow under the ground.", "Ik groei onder de grond."),
    ("food", "tomato", "tomaat", "помидор", "I am red and people often put me in a salad.", "Ik ben rood en mensen doen mij vaak in een salade.", "I have many small seeds inside.", "Ik heb veel kleine zaden vanbinnen."),
    ("food", "cucumber", "komkommer", "огурец", "I am long, green and full of water.", "Ik ben lang, groen en vol water.", "People often cut me into a salad.", "Mensen snijden mij vaak in een salade."),
    ("food", "strawberry", "aardbei", "клубника", "I am small, sweet and often red.", "Ik ben klein, zoet en vaak rood.", "I have tiny seeds on my skin.", "Ik heb kleine zaadjes op mijn schil."),
    ("food", "lemon", "citroen", "лимон", "I am yellow and people use me in drinks or food.", "Ik ben geel en mensen gebruiken mij in drinken of eten.", "My taste is very sour.", "Mijn smaak is heel zuur."),
    ("food", "orange", "sinaasappel", "апельсин", "I am round and people often make juice from me.", "Ik ben rond en mensen maken vaak sap van mij.", "I have a thick orange peel.", "Ik heb een dikke oranje schil."),
    ("food", "grape", "druif", "виноград", "I am small and I can be green or purple.", "Ik ben klein en ik kan groen of paars zijn.", "I grow together in a bunch.", "Ik groei samen in een tros."),
    ("food", "watermelon", "watermeloen", "арбуз", "I am very big and I have a green skin outside.", "Ik ben heel groot en ik heb een groene schil aan de buitenkant.", "Inside I am red and full of water.", "Vanbinnen ben ik rood en vol water."),
    ("food", "potato", "aardappel", "картофель", "I grow under the ground and I have a brown skin.", "Ik groei onder de grond en ik heb een bruine schil.", "People make fries from me.", "Mensen maken friet van mij."),
    ("food", "broccoli", "broccoli", "брокколи", "I am a green vegetable and I look like a small tree.", "Ik ben een groene groente en ik lijk op een kleine boom.", "People often cook me before eating me.", "Mensen koken mij vaak voordat ze mij eten."),
)


def _extra_local_game_cards(clue_lang):
    """Знакомые темы поддерживают игру, когда внешний генератор недоступен."""
    dutch = clue_lang == "нидерландский"
    cards = []
    for category, english, dutch_word, russian, en_clue, nl_clue, en_hint, nl_hint in _EXTRA_LOCAL_SUBJECTS:
        answer = f"de {dutch_word}" if dutch else f"the {english}"
        if dutch:
            description = (
                "Veel mensen kennen mij goed. Je kunt mij in het dagelijks leven of in de natuur zien. "
                f"{nl_clue}"
            )
            hint, explain = nl_hint, "Deze extra hint maakt het antwoord duidelijker."
            words = [{"word": "kennen", "translation": "знать"}, {"word": "natuur", "translation": "природа"}]
        else:
            description = (
                "Many people know me well. You can see me in everyday life or in nature. "
                f"{en_clue}"
            )
            hint, explain = en_hint, "This extra clue makes the answer clearer."
            words = [{"word": "everyday", "translation": "повседневный"}, {"word": "nature", "translation": "природа"}]
        cards.append({
            "description": description, "answer": answer, "answer_en": english,
            "category": category, "aliases": [russian, english, dutch_word],
            "hint": hint, "explain": explain, "words": words, "_trusted_local": True,
        })
    return cards


def _local_game_data(clue_lang, recent):
    cards = list(_LOCAL_GAME_CARDS.get(clue_lang) or _LOCAL_GAME_CARDS["английский"])
    cards.extend(_extra_local_game_cards(clue_lang))
    for card in cards:
        if not _game_is_recent(card, recent):
            return dict(card)
    return {}


def _description_is_guessable(data, lang=None):
    """Проверяет связное языковое описание, а не список отдельных улик."""
    description = " ".join(str(data.get("description") or "").split())
    answer = str(data.get("answer") or "").strip()
    aliases = data.get("aliases") or []
    hint = " ".join(str(data.get("hint") or "").split())
    sentences = _game_sentences(description)
    if not answer or not hint or not (2 <= len(sentences) <= 5):
        return False
    if not 20 <= len(description.split()) <= 55:
        return False
    if _game_has_answer(description, answer, aliases) or _game_has_answer(hint, answer, aliases):
        return False
    if lang and (
        not _game_has_target_language(description, lang)
        or not _game_has_target_language(hint, lang)
        or not _game_has_target_language(data.get("explain", ""), lang)
    ):
        return False
    if data.get("_trusted_local"):
        return True
    combined = " ".join((description, hint, data.get("explain", ""))).casefold()
    vague_count = sum(bool(re.search(marker, combined)) for marker in _GAME_VAGUE_CLUE_MARKERS)
    signature_count = sum(bool(re.search(marker, combined)) for marker in _GAME_SIGNATURE_MARKERS)
    hint_signature_count = sum(bool(re.search(marker, hint.casefold())) for marker in _GAME_SIGNATURE_MARKERS)
    if signature_count < 1 or vague_count >= 2 or hint_signature_count < 1:
        return False
    # Подсказка обязана добавлять отличительный признак, а не повторять общий текст.
    return any(
        marker not in description.casefold()
        for marker in _GAME_SIGNATURE_MARKERS
        if re.search(marker, hint.casefold())
    )


def _clues_are_guessable(data):
    """Совместимое имя; новые раунды проверяют DESCRIPTION."""
    return _description_is_guessable(data)


def game_data(clue_lang, recent, attempt=0):
    subject, expected_category = _GAME_REQUEST_FOCUSES[(len(recent) + attempt) % len(_GAME_REQUEST_FOCUSES)]
    avoid = ("Не загадывай ничего из этого списка и их переводы/синонимы: " + ", ".join(recent[-80:])) if recent else ""
    prompt = f"""Create a short guessing game for a language learner.
Target language: {clue_lang}. CEFR level: A1-A2.
Choose exactly one {subject}. It must be familiar to a beginner.
{avoid}
Write a natural mini-description of 3-4 connected sentences, about 25-45 words total.
Use simple, natural language. Do not write a list of clues. Include 2-4 concrete characteristics.
Make guessing possible without making the answer immediately obvious. Never include the answer itself.
Avoid metaphors, vague statements, trivia, specialist knowledge and factual errors.
HINT must be one strong short sentence in the target language. It should make the answer almost obvious
without saying the answer; use a distinctive characteristic, not a generic fact.
EXPLAIN must be 1-2 short natural sentences in the target language.
WORDS must contain at most 3 useful words from DESCRIPTION, HINT or EXPLAIN, with Russian translations.
Do not choose function words such as ik, I, zijn, be, een, a, the.
Attempt: {attempt + 1}. Return exactly these fields, one per line, without markdown:
DESCRIPTION: 3-4 connected sentences in {clue_lang}, 25-45 words
ANSWER: answer in {clue_lang}
CATEGORY: exactly {expected_category}
ALIASES: accepted Russian, English and Dutch names separated by |
ENGLISH: English name in 1-4 words
HINT: one strong hint in {clue_lang}
EXPLAIN: 1-2 short sentences in {clue_lang}
WORDS: word|Russian translation; word|Russian translation; word|Russian translation"""
    try:
        raw = ai.llm(
            prompt, 900, 1.0, tier="cheap", module="learning_game",
            fallback_allowed=True, privacy_level="public",
        )
    except Exception:
        # Загадка не должна превращаться в ошибку интерфейса, если одновременно
        # недоступны все AI-провайдеры и последний общий OpenRouter fallback.
        return _local_game_data(clue_lang, recent)
    out = {}
    for key, field in (("DESCRIPTION", "description"), ("ANSWER", "answer"), ("CATEGORY", "category"),
                       ("ALIASES", "aliases"),
                       ("ENGLISH", "answer_en"),
                       ("HINT", "hint"), ("EXPLAIN", "explain"), ("WORDS", "words_raw")):
        m = re.search(rf"{key}:\s*(.+?)(?=\n[A-Z]+\d*:\s|\Z)", raw, re.S)
        out[field] = m.group(1).strip() if m else ""
    out["aliases"] = [x.strip() for x in out.get("aliases", "").split("|") if x.strip()]
    out["category"] = _game_category_key(out.get("category"))
    source_text = " ".join((out.get("description", ""), out.get("hint", ""), out.get("explain", "")))
    out["description"] = " ".join(str(out.get("description") or "").split())
    out["hint"] = " ".join(str(out.get("hint") or "").split())
    out["explain"] = " ".join(str(out.get("explain") or "").split())
    out["words"] = _parse_game_words(out.get("words_raw", ""), source_text)
    return out


def _game_play_kb(ui, *, hint_available):
    rows = []
    if hint_available:
        rows.append([InlineKeyboardButton(ui["hint"], callback_data="game_hint")])
    rows.append([InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")])
    rows.append([
        InlineKeyboardButton(ui["back"], callback_data="m_learn"),
        InlineKeyboardButton(ui["home"], callback_data="m_menu"),
    ])
    return InlineKeyboardMarkup(rows)


def _game_result_kb(ui):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["again"], callback_data="game_again")],
        [
            InlineKeyboardButton(ui["back"], callback_data="m_learn"),
            InlineKeyboardButton(ui["home"], callback_data="m_menu"),
        ],
    ])


async def start(bot, cid, status=None):
    store.challenge_state.pop(str(cid), None)
    lang = _language_for_code(_active_language_code(cid))
    store.game_config[str(cid)] = {"lang": lang}
    await send_game(bot, cid, status=status)

async def send_game(bot, cid, status=None):
    store.challenge_state.pop(str(cid), None)   # фикс: чтобы перевод не перехватывал
    cfg = store.game_config.get(str(cid), {"lang": "английский"})
    lang = cfg.get("lang", "английский")
    ui = _game_ui(lang)
    recent = _game_recent(cid)
    attempted = list(recent)
    try:
        d = {}
        for attempt in range(5):
            cand = game_data(lang, attempted, attempt=attempt)
            if (cand.get("answer") and _description_is_guessable(cand, lang)
                    and not _game_is_recent(cand, recent)):
                d = cand
                break
            if cand.get("answer"):
                attempted += [cand.get("answer", "")] + list(cand.get("aliases") or [])
        if not d:
            # Повторы и невалидный формат от AI не должны оставлять пользователя
            # без раунда. Локальный резерв сверяем только с реальными сыгранными
            # загадками: неудачная попытка AI не может спрятать новую карточку.
            fallback = _local_game_data(lang, recent)
            if fallback.get("answer") and _description_is_guessable(fallback, lang):
                d = fallback
            else:
                msg = learning_ui.game_no_new_round(ui)
                kb = _game_result_kb(ui)
                if status is not None:
                    await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
                else:
                    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
                return
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_learn"); return
    _remember_game_answer(cid, d)
    category = _game_category_label(d)
    store.game_state[str(cid)] = {"answer": d.get("answer", ""), "answer_en": d.get("answer_en", ""),
                                  "aliases": d.get("aliases", []),
                                  "category": category,
                                  "description": d.get("description", ""),
                                  "hint": _dot(d.get("hint", "")),
                                  "hint_used": False,
                                  "explain": _dot(d.get("explain", "")),
                                  "words": d.get("words", []), "tries": 0}
    msg = learning_ui.game_card(ui, d.get("description", ""), category=category)
    kb = _game_play_kb(ui, hint_available=True)
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
    else:
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)

def _fuzzy(a, b):
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    if abs(len(a) - len(b)) <= 3:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        return diff <= 3
    return False


def _get_english_query(st):
    """Возвращает только английское название ответа для поиска фото."""
    explicit = str(st.get("answer_en") or "").strip()
    if explicit and re.search(r"[A-Za-z]", explicit):
        return explicit

    # Старые состояния не имели отдельного answer_en. По контракту генератора
    # второй alias — английский: русский | английский | нидерландский.
    aliases = [str(alias or "").strip() for alias in (st.get("aliases") or [])]
    if len(aliases) > 1 and re.search(r"[A-Za-z]", aliases[1]):
        return aliases[1]
    if re.search(r"[A-Za-z]", str(st.get("answer") or "")):
        return str(st.get("answer") or "").strip()
    for alias in aliases:
        if re.search(r"[A-Za-z]", alias):
            return alias
    return ""


async def _send_game_result(bot, cid, st, ui, kb):
    import travel_photos
    msg = learning_ui.game_found(
        ui, st.get("answer", ""), st.get("explain", ""), st.get("words", []),
    )
    query = _get_english_query(st)
    photo = None
    if query:
        try:
            photo = travel_photos.find_illustration(query)
        except Exception:
            pass
    if (_is_landscape_photo(photo)):
        try:
            await bot.send_photo(
                chat_id=cid,
                photo=photo["url"],
                caption=msg.text,
                caption_entities=msg.entities,
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


def _is_landscape_photo(photo):
    """Детектив показывает только горизонтальный кадр с одним объектом."""
    if not isinstance(photo, dict) or not photo.get("url"):
        return False
    try:
        return int(photo.get("width") or 0) > int(photo.get("height") or 0) > 0
    except (TypeError, ValueError):
        return False


async def _finish_game_round(bot, cid, st, ui):
    """Единое завершение для правильного ответа, сдачи и второй ошибки."""
    store.game_state.pop(str(cid), None)
    _remember_game_answer(cid, st)
    await _send_game_result(bot, cid, st, ui, _game_result_kb(ui))


async def game_answer(bot, cid, text):
    st = store.game_state.get(str(cid))
    if not st:
        return False
    cfg = store.game_config.get(str(cid), {"lang": "русский"})
    ui = _game_ui(cfg["lang"])
    guess = text.lower().strip()
    names = [st["answer"]] + st.get("aliases", [])
    pool = []
    for n in names:
        n = (n or "").lower().strip()
        pool += [n] + n.split()
    correct = any(_fuzzy(guess, p) for p in pool if p)
    if correct:
        await _finish_game_round(bot, cid, st, ui)
        return True
    st["tries"] = st.get("tries", 0) + 1
    if st["tries"] >= 2:
        await _finish_game_round(bot, cid, st, ui)
    else:
        kb = _game_play_kb(ui, hint_available=not st.get("hint_used"))
        await bot.send_message(chat_id=cid, text=f"{ui['wrong']}\n\n{ui['retry']}", reply_markup=kb)
    return True


async def game_hint(bot, cid, q):
    st = store.game_state.get(str(cid))
    ui = _game_ui(store.game_config.get(str(cid), {}).get("lang", "русский"))
    if st and not st.get("hint_used") and st.get("hint"):
        st["hint_used"] = True
        kb = _game_play_kb(ui, hint_available=False)
        try:
            await q.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        msg = learning_ui.game_hint(ui, st["hint"])
        await q.message.reply_text(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
    else:
        await q.message.reply_text(ui["hint_title"])


async def game_reveal(bot, cid, q):
    st = store.game_state.get(str(cid))
    ui = _game_ui(store.game_config.get(str(cid), {}).get("lang", "русский"))
    if not st:
        return
    try:
        await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _finish_game_round(bot, cid, st, ui)
