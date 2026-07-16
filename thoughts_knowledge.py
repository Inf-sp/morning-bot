"""Проверенная компактная база рекомендаций для раздела «Мысли».

Тексты здесь — короткие авторские пересказы, а не фрагменты источников. В поиск
попадают только заранее разрешённые NICE/NHS-материалы, выбранные руководства по
СДВГ и протокол безопасности. ZeroEntropy ранжирует этот закрытый набор; при
недоступности API используется детерминированный локальный fallback.
"""

import re

import config
import rerank


GUIDANCE = (
    {
        "types": ("practical_problem", "emotion", "unknown"),
        "source": "NICE NG87",
        "url": "https://www.nice.org.uk/guidance/ng87/chapter/recommendations",
        "text": "Уменьшить отвлечения, дать короткую письменную инструкцию и разбить фокус на короткие отрезки с паузами.",
    },
    {
        "types": ("practical_problem",),
        "source": "Mastering Your Adult ADHD",
        "url": "https://www.guilford.com/books/Mastering-Your-Adult-ADHD/Safren-Sprich-Perlman-Otto/9780190235567",
        "text": "Превратить большую задачу в один наблюдаемый первый шаг, который можно начать без дополнительной подготовки.",
    },
    {
        "types": ("practical_problem", "emotion"),
        "source": "The Adult ADHD Tool Kit",
        "url": "https://www.routledge.com/The-Adult-ADHD-Tool-Kit-Using-CBT-to-Facilitate-Coping-Inside-and-Out/Ramsay-Rostain/p/book/9780415815895",
        "text": "Выбрать действие на 5–15 минут, сделать время видимым коротким таймером и сократить число решений до одного.",
    },
    {
        "types": ("practical_problem", "emotion", "unknown"),
        "source": "Russell Barkley — externalising executive functions",
        "url": "https://www.russellbarkley.org/",
        "text": "Не удерживать намерение в рабочей памяти: вынести его во внешнюю запись, напоминание или видимый следующий шаг.",
    },
    {
        "types": ("anxious_prediction", "unknown"),
        "source": "NHS Every Mind Matters — Tackling your worries",
        "url": "https://www.nhs.uk/every-mind-matters/mental-wellbeing-tips/self-help-cbt-techniques/tackling-your-worries/",
        "text": "Сначала записать мысль, затем отличить решаемую проблему от гипотетического прогноза и не искать уверенности там, где фактов пока нет.",
    },
    {
        "types": ("anxious_prediction", "practical_problem"),
        "source": "NHS Every Mind Matters — worry time",
        "url": "https://www.nhs.uk/every-mind-matters/mental-wellbeing-tips/self-help-cbt-techniques/tackling-your-worries/",
        "text": "Если сейчас ничего практического сделать нельзя, отложить мысль до отдельного короткого времени и вернуться к текущему делу.",
    },
    {
        "types": ("emotion",),
        "source": "NHS — anxiety, fear and panic",
        "url": "https://www.nhs.uk/mental-health/feelings-symptoms-behaviours/feelings-and-symptoms/anxiety-fear-panic/",
        "text": "Не пытаться сделать всё сразу: выбрать маленькую достижимую цель, сменить обстановку или обратиться к человеку, которому доверяешь.",
    },
    {
        "types": ("crisis",),
        "source": "113 Zelfmoordpreventie / 112 Netherlands",
        "url": "https://www.113.nl/",
        "text": "При непосредственной опасности звонить 112; при суицидальных мыслях — 113 или 0800-0113; прямо сейчас связаться с близким или специалистом.",
    },
)


def _local_score(query, document):
    tokens = set(re.findall(r"[a-zа-яё]{3,}", str(query).casefold()))
    haystack = str(document).casefold()
    return sum(token in haystack for token in tokens)


def retrieve(query, thought_type, top_n=3):
    candidates = [
        item for item in GUIDANCE
        if thought_type in item["types"]
    ]
    if not candidates:
        return []
    documents = [f'{item["source"]}: {item["text"]}' for item in candidates]
    if config.ZEROENTROPY_API_KEY:
        try:
            return [text for text, _score in rerank.rerank(query, documents, top_n=top_n)]
        except Exception:
            pass
    ranked = sorted(documents, key=lambda value: _local_score(query, value), reverse=True)
    return ranked[:top_n]
