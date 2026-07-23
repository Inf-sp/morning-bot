from ui.learning import exercise_result


def _result(entry, *, correct=True, chosen=""):
    return exercise_result(
        {"exercise_type": "choose_translation", "term": entry["term"], "entry": entry},
        correct,
        chosen=chosen,
    ).text


def test_result_card_uses_article_plural_and_saved_example():
    text = _result({
        "lang": "nl", "term": "gevolg", "article": "het",
        "translation": "последствие", "pos": "noun", "plural": "gevolgen",
        "examples": [{"text": "Dat kan ernstige gevolgen hebben.",
                      "translation": "Это может иметь серьёзные последствия."}],
    })

    assert "Правильно:" not in text
    assert "Het gevolg → Последствие" in text
    assert "Разбор: существительное · het-слово" in text
    assert "Множественное число: de gevolgen" in text
    assert "💡 Полезно: Dat kan ernstige gevolgen hebben → Это может иметь серьёзные последствия" in text


def test_result_card_shows_only_verified_verb_forms():
    text = _result({
        "lang": "nl", "term": "gaan", "translation": "идти", "pos": "verb",
        "verb_type": "irregular", "analysis_confidence": 0.98,
        "infinitive": "gaan", "past_singular": "ging", "perfect_form": "gegaan",
        "examples": [{"text": "Ik ga morgen naar Amsterdam.",
                      "translation": "Я завтра еду в Амстердам."}],
    })

    assert "Разбор: неправильный глагол" in text
    assert "Формы: gaan · ging · gegaan" in text
    assert "Прошедшее время" not in text


def test_result_card_hides_unverified_verb_forms_and_marks_close_answer():
    entry = {
        "lang": "nl", "term": "geschikt", "translation": "подходящий", "pos": "adj",
        "examples": [{"text": "Deze jas is geschikt voor de winter.",
                      "translation": "Эта куртка подходит для зимы."}],
    }
    text = exercise_result(
        {"exercise_type": "translate_context", "term": "geschikt", "entry": entry},
        False,
        chosen="geschikte",
        language_report={"issues": [{"issue_type": "grammar"}],
                         "explanation": "Проверь окончание прилагательного."},
    ).text

    assert text.startswith("🟡 Почти")
    assert "Твой ответ: geschikte" in text
    assert "Почему: Проверь окончание прилагательного." in text
    assert "Разбор: прилагательное" in text
    assert "Формы:" not in text


def test_multiword_term_is_phrase_and_cannot_show_noun_metadata():
    text = _result({
        "lang": "nl", "term": "Geld dat op je rekening staat",
        "translation": "деньги на счёте", "pos": "noun", "article": "de",
        "plural": "тегоеден",
        "examples": [{"text": "Met tegoed bedoelen we geld dat op je rekening staat.",
                      "translation": "Под средствами на счёте мы понимаем деньги на счёте."}],
    })

    assert "Разбор: фраза" in text
    assert "Множественное число" not in text
    assert "Geld dat op je rekening staat → Деньги на счёте" in text
    assert "💡 Полезно: Met tegoed bedoelen we geld dat op je rekening staat →" in text


def test_translation_comments_are_not_shown_and_mixed_plural_is_hidden():
    text = _result({
        "lang": "nl", "term": "vanwege", "translation": "из-за (как door, omdat); по причине",
        "pos": "preposition", "plural": "de тегоeden",
        "examples": [{"text": "Vanwege de regen bleven we thuis.",
                      "translation": "Из-за дождя мы остались дома."}],
    })

    assert "Vanwege → Из-за · по причине" in text
    assert "как door" not in text
    assert "тегоеден" not in text
