import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import language_tool
import trainer
import trainer_grading
from trainer_engine import EXERCISE_TRANSLATE_CONTEXT
from ui.learning import exercise_result


class FakeResponse:
    status_code = 200
    headers = {}

    def json(self):
        return {
            "language": {"code": "nl-NL"},
            "matches": [{
                "message": "Mogelijke spelfout gevonden.",
                "shortMessage": "Spelling",
                "offset": 11,
                "length": 5,
                "replacements": [{"value": "fout"}, {"value": "fouten"}],
                "rule": {
                    "id": "MORFOLOGIK_RULE_NL_NL",
                    "issueType": "misspelling",
                    "category": {"name": "Typographical"},
                },
            }],
        }


def _report(issue_type="grammar", replacements=None):
    replacements = ["ga"] if replacements is None else replacements
    return {
        "ok": False,
        "available": True,
        "text": "Ik gaat naar huis.",
        "corrected_text": "Ik ga naar huis.",
        "issues": [{
            "original": "gaat",
            "message": "De persoonsvorm past niet bij het onderwerp.",
            "short_message": "Werkwoordsvorm",
            "replacements": replacements,
            "rule_id": "SUBJECT_VERB_AGREEMENT",
            "issue_type": issue_type,
        }],
    }


def test_public_api_check_normalizes_issues_and_correction(monkeypatch):
    captured = {}
    usage = []
    monkeypatch.setattr(language_tool.config, "LANGUAGETOOL_API_URL", "https://api.languagetool.org/v2")
    monkeypatch.setattr(language_tool.util, "ttl_get", lambda *_args: None)
    monkeypatch.setattr(language_tool.util, "ttl_set", lambda *_args: None)
    monkeypatch.setattr(
        language_tool.api_usage, "record_request",
        lambda service, **kwargs: usage.append((service, kwargs)),
    )

    def fake_post(url, data, timeout):
        captured.update({"url": url, "data": data, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(language_tool.requests, "post", fake_post)

    result = language_tool.check_text("Ik heb een foutt.")

    assert captured["url"] == "https://api.languagetool.org/v2/check"
    assert captured["data"] == {
        "text": "Ik heb een foutt.", "language": "nl-NL", "motherTongue": "ru",
    }
    assert captured["timeout"] == 8
    assert result["issues"][0]["original"] == "foutt"
    assert result["issues"][0]["replacements"] == ["fout", "fouten"]
    assert result["corrected_text"] == "Ik heb een fout."
    assert usage[-1][0] == "languagetool"
    assert usage[-1][1]["units"] == {"characters": 17}


def test_async_check_retries_once_after_temporary_outage(monkeypatch):
    calls = []

    def check(text, language):
        calls.append((text, language))
        if len(calls) == 1:
            return {"ok": False, "available": False, "text": text, "issues": []}
        return {"ok": True, "available": True, "text": text, "issues": []}

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(language_tool, "check_text", check)
    monkeypatch.setattr(language_tool.asyncio, "sleep", no_delay)
    report = asyncio.run(language_tool.check_text_retry("Ik ga.", "nl-NL", retries=1))

    assert report["available"] is True
    assert calls == [("Ik ga.", "nl-NL"), ("Ik ga.", "nl-NL")]


def test_disputed_dutch_error_uses_groq_then_gemini(monkeypatch):
    captured = {}
    monkeypatch.setattr(trainer.language_tool, "check_text", lambda *_args: _report())

    async def fake_ai(prompt, *_args, **kwargs):
        captured.update({"prompt": prompt, "kwargs": kwargs})
        return {
            "acceptable": False,
            "explanation": "После ik нужна форма ga, а не gaat.",
        }

    monkeypatch.setattr(trainer.ai, "allm_json", fake_ai)
    grade, report = asyncio.run(trainer._grade_dutch_written({
        "lang": "nl",
        "exercise_type": EXERCISE_TRANSLATE_CONTEXT,
        "correct": "Ik ga naar huis.",
        "alt": [],
        "ru": "Я иду домой.",
        "hint_shown": False,
    }, "Ik gaat naar huis."))

    assert grade.correct is False
    assert captured["kwargs"]["order"] == ("groq", "gemini")
    assert report["explanation"] == "После ik нужна форма ga, а не gaat."


def test_simple_spelling_suggestion_does_not_call_llm(monkeypatch):
    report = _report(issue_type="misspelling", replacements=["fout"])
    monkeypatch.setattr(trainer.language_tool, "check_text", lambda *_args: report)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("LLM must not be called for a simple spelling correction")

    monkeypatch.setattr(trainer.ai, "allm_json", forbidden)
    grade, checked = asyncio.run(trainer._grade_dutch_written({
        "lang": "nl",
        "exercise_type": "recall_free",
        "correct": "Ik ga naar huis.",
        "alt": [],
        "hint_shown": False,
    }, "Ik gaat naar huis."))

    assert grade.correct is False
    assert checked["issues"]


def test_language_tool_card_is_formatted_by_code():
    report = {**_report(), "explanation": "После ik используется форма ga."}
    message = exercise_result(
        {
            "exercise_type": EXERCISE_TRANSLATE_CONTEXT,
            "correct": "Ik ga naar huis.",
            "ru": "Я иду домой.",
        },
        False,
        chosen="Ik gaat naar huis.",
        language_report=report,
    )

    assert "Твой ответ: Ik gaat naar huis." in message.text
    assert "Лучше: Ik ga naar huis." in message.text
    assert "Почему: После ik используется форма ga." in message.text
    assert "SUBJECT_VERB_AGREEMENT" not in message.text


def test_style_recommendation_does_not_make_answer_wrong(monkeypatch):
    report = _report(issue_type="style", replacements=["Ik wandel naar huis."])
    monkeypatch.setattr(trainer.language_tool, "check_text", lambda *_args: report)

    grade, checked = asyncio.run(trainer._grade_dutch_written({
        "lang": "nl",
        "exercise_type": "recall_free",
        "correct": "Ik gaat naar huis.",
        "alt": [],
        "hint_shown": False,
    }, "Ik gaat naar huis."))

    assert grade.correct is True
    assert checked["issues"] == []


def test_english_written_answer_does_not_use_language_tool(monkeypatch):
    state = {
        "current": {
            "lang": "en",
            "exercise_type": EXERCISE_TRANSLATE_CONTEXT,
            "correct": "I am going home.",
            "alt": [],
            "ru": "Я иду домой.",
        },
    }
    monkeypatch.setattr(trainer.trainer_session, "get", lambda _cid: state)
    monkeypatch.setattr(
        trainer.language_tool, "check_text",
        lambda *_args: (_ for _ in ()).throw(AssertionError("English must not use LanguageTool")),
    )

    async def fake_grade(_data, _text):
        return trainer_grading.GradeResult(
            True, trainer_grading.AnswerQuality.RECALLED_FREE,
        )

    async def fake_apply(*_args, **_kwargs):
        return None

    monkeypatch.setattr(trainer, "_grade_context", fake_grade)
    monkeypatch.setattr(trainer, "_apply_result", fake_apply)

    assert asyncio.run(trainer.handle_text(object(), "english-user", "I am going home.")) is True
