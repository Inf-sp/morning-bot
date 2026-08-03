import re
from html import unescape

from telegram import MessageEntity

from .builder import MessageBuilder
from . import rich


_LEADING_EMOJI_RE = re.compile(
    r"^[\s\U0001F1E6-\U0001FAFF\u2600-\u27BF\uFE0F]+"
)
_CASE_SENSITIVE_LABELS = {
    "автор", "город", "дата", "имя", "место", "название", "ссылка",
    "страна", "фраза", "твой ответ",
}


def _clean_line(line: str) -> str:
    line = unescape(line or "").strip()
    line = re.sub(r"</?(?:b|strong|i|em|code)>", "", line, flags=re.I)
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = re.sub(r"__(.*?)__", r"\1", line)
    return line.strip()


def _strip_title_emoji(line: str) -> str:
    return _LEADING_EMOJI_RE.sub("", line or "").strip()


def _strip_final_intro(line: str) -> str:
    return re.sub(
        r"^(?:последн(?:ий|ее)\s+(?:совет|предложение)|итог|важно|вывод)\s*:\s*",
        "",
        line or "",
        flags=re.I,
    ).strip()


def _split_leading_label(line: str):
    """Возвращает короткую подпись и текст после неё для строк ``Подпись: текст``."""
    match = re.match(r"^([^:\n]{1,64}):(?:\s*(.*))?$", line or "")
    if not match:
        return None
    label = match.group(1).strip()
    if not any(char.isalpha() for char in label) or len(label.split()) > 7:
        return None
    first_alpha = next((char for char in label if char.isalpha()), "")
    if not first_alpha.isupper():
        return None
    return label, (match.group(2) or "").strip()


def _rich_assistant_message(title, lines, quote_flags):
    """Render the same assistant answer as native headings/lists/quotes.

    The plain ``MessageBuilder`` output below remains the universal fallback;
    this function only describes presentation and never makes content choices.
    """
    blocks = [rich.heading(title, size=3)]
    bullets = []

    def flush_bullets():
        nonlocal bullets
        if bullets:
            blocks.append({
                "type": "list",
                "items": [
                    {"blocks": [rich.paragraph(item)]}
                    for item in bullets
                ],
            })
            bullets = []

    for index, line in enumerate(lines):
        line = str(line or "").strip()
        if not line:
            continue
        if line.startswith("- "):
            bullets.append(line[2:].strip())
            continue
        flush_bullets()
        label_parts = _split_leading_label(line)
        if quote_flags[index]:
            blocks.append({
                "type": "blockquote",
                "blocks": [rich.paragraph(line)],
            })
        elif label_parts:
            label, content = label_parts
            if content:
                blocks.append(rich.paragraph([
                    {"type": "bold", "text": f"{label}:"},
                    f" {content}",
                ]))
            else:
                blocks.append(rich.heading(label, size=4))
        else:
            blocks.append(rich.paragraph(line))
    flush_bullets()
    return rich.message(blocks)


def assistant_answer(answer: str):
    raw_lines = [_clean_line(line) for line in (answer or "").splitlines()]
    lines = [line for line in raw_lines if line]
    if not lines:
        lines = ["Пусто", "Попробуй ещё раз."]

    title = _strip_title_emoji(lines[0]).rstrip(".:") or "Ответ"
    body = lines[1:]
    b = MessageBuilder()
    b.section(title)
    if body:
        b.spacer()

    normalized_lines = []
    quote_flags = []
    for line in body:
        normalized = line.strip()
        is_quote = normalized.startswith((">", "»"))
        if is_quote:
            normalized = normalized.lstrip(">» ").strip()

        if normalized.lower().startswith(("это значит", "значит:")):
            normalized = "Что важно:"

        normalized_lines.append(normalized)
        quote_flags.append(is_quote)

    if normalized_lines:
        normalized_lines[-1] = _strip_final_intro(normalized_lines[-1])

    for idx, normalized in enumerate(normalized_lines):
        next_line = normalized_lines[idx + 1] if idx != len(normalized_lines) - 1 else ""
        label_parts = _split_leading_label(normalized)
        is_list_label = bool(label_parts and not label_parts[1] and next_line.startswith("- "))
        if quote_flags[idx]:
            b.add(normalized, MessageEntity.BLOCKQUOTE)
        elif label_parts:
            label, content = label_parts
            b.label(
                label,
                content or None,
                lowercase=False,
            )
        else:
            b.add(normalized)
        if idx != len(normalized_lines) - 1:
            if (
                normalized.startswith("- ") and next_line.startswith("- ")
                or is_list_label
            ):
                b.newline()
            else:
                b.blank()

    msg = b.build_stripped()
    msg.rich_message = _rich_assistant_message(title, normalized_lines, quote_flags)
    return msg
