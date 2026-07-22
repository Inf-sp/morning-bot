from ui.myday import day_summary


def test_day_summary_keeps_capitalized_dictionary_translation_after_arrow():
    message = day_summary(
        "Ср, 15 июля",
        "Алкмар",
        flag="🇳🇱",
        word_line="Slim → Худой, умный.",
        word_lang="nl",
    )

    assert "🇳🇱 Нидерландский: Slim → Худой, умный." in message.text
    assert "→ Худой" in message.text
