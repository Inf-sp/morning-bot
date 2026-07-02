from .builder import MessageSpec
from util import esc, cap_sentence


def full_forecast(header, periods, joke=""):
    lines = [f"<b>{esc(header)}</b>", ""]
    for period in periods:
        lines += [f"<b>{esc(period['label'])}:</b>", period["line"], ""]
    if joke:
        lines.append(esc(joke))
    return MessageSpec(text="\n".join(lines).strip(), parse_mode="HTML")


def day_forecast(header, main_lines, alert="", fact_title="", fact=""):
    lines = [f"<b>{esc(header)}</b>", ""]
    lines += list(main_lines or [])
    if alert:
        lines += ["", alert]
    elif fact:
        if fact_title:
            lines += ["", f"🌡️ <b>{esc(fact_title)}</b>", esc(fact)]
        else:
            lines += ["", esc(fact)]
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def week_forecast(rng, city, flag, groups, summary=""):
    lines = [f"<b>Ближайшая неделя • {esc(rng)} • {esc(city)} {flag}</b>", ""]
    for group in groups:
        lines.append(
            f"{group['icon']} {esc(group['label'])} — {esc(group['desc'])}, {group['temp']}"
        )
    if summary:
        lines += ["", "🌡️ <b>Метео-итог</b>", esc(_finish_sentence(cap_sentence(summary)))]
    return MessageSpec(text="\n".join(lines).strip(), parse_mode="HTML")


def _finish_sentence(text):
    text = (text or "").strip()
    if text and text[-1] not in ".!?…":
        return text + "."
    return text
