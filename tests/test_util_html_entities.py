import pytest

import util


def _slice_u16(text, offset, length):
    """Достаёт подстроку по UTF-16 offset/length — так же, как это делает Telegram."""
    u16 = text.encode("utf-16-le")
    return u16[offset * 2:(offset + length) * 2].decode("utf-16-le")


@pytest.mark.unit
def test_html_to_entities_bold_and_italic():
    plain, entities = util.html_to_entities("<b>Заголовок</b> обычный <i>курсив</i>")

    assert plain == "Заголовок обычный курсив"
    bold = next(e for e in entities if e.type == "bold")
    italic = next(e for e in entities if e.type == "italic")
    assert plain[bold.offset:bold.offset + bold.length] == "Заголовок"
    assert plain[italic.offset:italic.offset + italic.length] == "курсив"


@pytest.mark.unit
def test_html_to_entities_handles_emoji_offsets():
    plain, entities = util.html_to_entities("🥣 <b>Рецепт дня</b>")

    bold = entities[0]
    assert _slice_u16(plain, bold.offset, bold.length) == "Рецепт дня"


@pytest.mark.unit
def test_html_to_entities_unescapes_entities():
    plain, entities = util.html_to_entities("<b>Омлет &lt;сыр&gt;</b> и &amp; молоко")

    assert plain == "Омлет <сыр> и & молоко"
    bold = entities[0]
    assert plain[bold.offset:bold.offset + bold.length] == "Омлет <сыр>"


@pytest.mark.unit
def test_html_to_entities_link():
    plain, entities = util.html_to_entities('Ссылка: <a href="https://example.com">тут</a>')

    link = entities[0]
    assert link.type == "text_link"
    assert link.url == "https://example.com"
    assert plain[link.offset:link.offset + link.length] == "тут"


@pytest.mark.unit
def test_html_to_entities_empty_and_plain():
    assert util.html_to_entities("") == ("", [])
    assert util.html_to_entities(None) == ("", [])
    assert util.html_to_entities("просто текст") == ("просто текст", [])
