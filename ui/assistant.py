import re
from html import unescape

from telegram import MessageEntity

from .builder import MessageBuilder


_LEADING_EMOJI_RE = re.compile(
    r"^[\s\U0001F1E6-\U0001FAFF\u2600-\u27BF\uFE0F]+"
)


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
        is_list_label = normalized.endswith(":") and next_line.startswith("- ")
        entity_type = MessageEntity.BLOCKQUOTE if quote_flags[idx] else MessageEntity.BOLD if is_list_label else None
        b.add(normalized, entity_type)
        if idx != len(normalized_lines) - 1:
            if (
                normalized.startswith("- ") and next_line.startswith("- ")
                or is_list_label
            ):
                b.newline()
            else:
                b.blank()

    return b.build_stripped()
