import os

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import ai


def test_llm_json_repairs_unparseable_response(monkeypatch):
    calls = []

    def fake_llm(prompt, *args, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return '{"name": "Тост", "ingredients": "хлеб, "сыр""'
        return '{"name": "Тост", "ingredients": "хлеб, сыр"}'

    monkeypatch.setattr(ai, "llm", fake_llm)

    assert ai.llm_json("Верни рецепт", module="test") == {
        "name": "Тост",
        "ingredients": "хлеб, сыр",
    }
    assert len(calls) == 2
    assert "Преобразуй ответ ИИ" in calls[1]
