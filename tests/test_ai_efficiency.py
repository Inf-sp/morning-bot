import os
import threading
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

import ai
import tracking


def test_cache_key_does_not_change_when_provider_order_changes():
    first = ai._cache_key(
        ("gemini", "cf"), "same prompt", 300, 0.2, "travel", "json",
    )
    fallback = ai._cache_key(
        ("cf", "groq"), "same prompt", 300, 0.2, "travel", "json",
    )

    assert first == fallback


def test_cache_key_ignores_action_id_and_prompt_whitespace():
    compact = ai._cache_key(
        ("gemini", "cf"), "Выбери страну", 300, 0.2, "travel", "json",
    )
    formatted = ai._cache_key(
        ("cf", "groq"), "  Выбери\n\n страну  ", 300, 0.2, "travel", "json",
    )

    first = tracking.start_action("42", "Поездка", "first")
    try:
        first_action_key = ai._cache_key(
            ("gemini",), "Выбери страну", 300, 0.2, "travel", "json",
        )
    finally:
        tracking.finish_action(first)
    second = tracking.start_action("42", "Поездка", "second")
    try:
        second_action_key = ai._cache_key(
            ("gemini",), "Выбери страну", 300, 0.2, "travel", "json",
        )
    finally:
        tracking.finish_action(second)

    assert compact == formatted == first_action_key == second_action_key


def test_structured_cache_key_uses_scenario_not_prompt_text():
    context = {
        "scenario": "travel_country",
        "country": "IS",
        "language": "ru",
        "profile_version": 4,
        "schema_version": 2,
    }

    direct = ai._cache_key(
        ("gemini",), "Исландия", 300, 0.2, "travel", "json",
        cache_context=context,
    )
    requested = ai._cache_key(
        ("cf",), "Расскажи про Исландию", 300, 0.2, "travel", "json",
        cache_context=dict(reversed(list(context.items()))),
    )
    other_country = ai._cache_key(
        ("gemini",), "Исландия", 300, 0.2, "travel", "json",
        cache_context={**context, "country": "FI"},
    )

    assert direct == requested
    assert direct != other_country


def test_utility_routes_do_not_start_with_gemini():
    for module in (
        "learning", "learning_trainer", "learning_dict_add", "trainer",
        "dictionary_import", "wardrobe_utility", "travel_utility",
    ):
        assert "gemini" not in ai._resolve(None, None, module=module)


def test_wardrobe_item_parsing_uses_the_simple_groq_route():
    assert ai._resolve(None, None, module="wardrobe_utility")[:2] == (
        ai.GROQ_SIMPLE, "cf",
    )
    assert ai._resolve(None, (ai.GROQ_SIMPLE, "cf")) == (ai.GROQ_SIMPLE, "cf")


def test_invalid_json_from_primary_uses_the_next_provider(monkeypatch):
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_gen_groq", lambda *_args, **_kwargs: '{"items": [}')
    monkeypatch.setattr(ai, "_gen_cf", lambda *_args, **_kwargs: '{"items": [{"name": "готово"}]}')

    result = ai.llm_json(
        "Верни JSON", order=(ai.GROQ_STANDARD, "cf"), module="test_json_fallback",
    )

    assert result == {"items": [{"name": "готово"}]}


def test_semantically_invalid_json_uses_the_next_provider(monkeypatch):
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_gen_groq", lambda *_args, **_kwargs: '{"ok":false}')
    monkeypatch.setattr(ai, "_gen_cf", lambda *_args, **_kwargs: '{"ok":true}')

    result = ai.llm_json(
        "Верни JSON", order=(ai.GROQ_STANDARD, "cf"),
        module="test_json_fallback", result_validator=lambda value: value.get("ok") is True,
    )

    assert result == {"ok": True}


def test_public_learning_modules_use_the_last_ai_reserve_by_default(monkeypatch):
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_gen_groq", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("groq down")))
    monkeypatch.setattr(
        ai, "_openrouter_plain_text_fallback",
        lambda *_args, **_kwargs: "резервный ответ",
    )

    result = ai.llm(
        "Верни короткий ответ", order=("groq_standard", "openrouter"),
        module="learning_game",
    )

    assert result == "резервный ответ"


def test_mistral_json_reserve_uses_direct_api(monkeypatch):
    captured = {}

    class Response:
        @staticmethod
        def json():
            return {"choices": [{"message": {"content": '{"ok":true}'}}]}

    def fake_post(url, headers, payload, *_args, **_kwargs):
        captured.update(url=url, headers=headers, payload=payload)
        return Response()

    monkeypatch.setattr(ai.config, "MISTRAL_API_KEY", "secret")
    monkeypatch.setattr(ai.config, "MISTRAL_MODEL", "mistral-small-2603")
    monkeypatch.setattr(ai, "_post", fake_post)

    result = ai._gen_mistral("Верни JSON", 120, 0.0, "json")

    assert result == '{"ok":true}'
    assert captured["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert captured["payload"]["model"] == "mistral-small-2603"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_cloudflare_accepts_openai_compatible_choices(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {
                "result": {
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 3},
                },
            }

    monkeypatch.setattr(ai.config, "CF_API_TOKEN", "token")
    monkeypatch.setattr(ai.config, "CF_ACCOUNT_ID", "account")
    monkeypatch.setattr(ai, "_post", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(ai.api_usage, "record_request", lambda *_args, **_kwargs: None)

    assert ai._gen_cf("Верни JSON", 50) == '{"ok":true}'


def test_cloudflare_accepts_short_railway_variable_names(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("CF_API_TOKEN", "token")
    monkeypatch.setenv("CF_ACCOUNT_ID", "account")

    assert ai.config._env_first("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN") == "token"
    assert ai.config._env_first("CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID") == "account"


def test_openrouter_uses_ordered_model_fallbacks(monkeypatch):
    monkeypatch.setattr(
        ai.config, "OPENROUTER_MODELS",
        (
            "openai/gpt-oss-120b",
            "google/gemini-2.5-flash-lite",
            "deepseek/deepseek-v4-flash-20260423",
        ),
    )

    assert ai._openrouter_routing_payload() == {
        "models": [
            "openai/gpt-oss-120b",
            "google/gemini-2.5-flash-lite",
            "deepseek/deepseek-v4-flash-20260423",
        ],
        "provider": {"allow_fallbacks": True},
    }


def test_all_central_routes_try_direct_mistral_before_openrouter():
    for order in {ai.SIMPLE_ORDER, ai.STANDARD_ORDER, ai.COMPLEX_ORDER}:
        assert "mistral" in order
        assert order.index("mistral") < order.index("openrouter")


def test_final_card_routes_keep_gemini_as_the_single_premium_primary():
    for module in ("travel", "food", "wardrobe"):
        assert ai._resolve(None, None, module=module)[0] == "gemini"


def test_every_central_text_ai_route_has_a_reserve_provider():
    routes = [
        *ai.TIERS.values(),
        *((order, None) for order in ai.MODULE_POLICY.values()),
    ]

    for order, _unused in routes:
        assert len([provider for provider in order if provider != "openrouter"]) >= 2


def test_all_premium_recommendations_have_a_cache_ttl():
    for module in ("travel", "food", "wardrobe"):
        assert ai._cache_ttl(module, "json") > 0


def test_cache_hit_does_not_check_gemini_cooldown(monkeypatch):
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: '{"ok":true}')
    monkeypatch.setattr(
        ai, "_gemini_cooldown_error",
        lambda: (_ for _ in ()).throw(AssertionError("cooldown checked before cache")),
    )

    assert ai.llm_json("same", module="travel") == {"ok": True}


def test_gemini_does_not_retry_a_rate_limit(monkeypatch):
    calls = []

    def limited(*_args, **_kwargs):
        calls.append("gemini")
        raise ai.LLMProviderError(
            "gemini", "limited", status_code=429, temporary=True,
            error_type="rate_limit", retry_after=1,
        )

    monkeypatch.setattr(ai, "_post", limited)
    monkeypatch.setattr(ai.api_usage, "gemini_requests", lambda **_kwargs: {"allowed": True})

    with pytest.raises(ai.LLMProviderError):
        ai._gen_gemini("prompt", 20, 0.0, "plain_text")

    assert calls == ["gemini"]


def test_gemini_limit_error_uses_current_product_section(monkeypatch):
    logged = []
    monkeypatch.setattr(
        ai.api_usage, "gemini_state",
        lambda *_args: {"cooldown_scope": "RPM", "cooldown_seconds": 60},
    )
    monkeypatch.setattr(ai.api_usage, "should_log_gemini_limit", lambda _token: True)
    monkeypatch.setattr(
        tracking, "log_error",
        lambda *args, **kwargs: logged.append((args, kwargs)),
    )

    trace = tracking.start_action("42", "Словарь", "добавление слова")
    try:
        ai._log_gemini_limit("gemini_rate_limit")
    finally:
        tracking.finish_action(trace)

    assert logged[0][1]["section"] == "Словарь"
    assert logged[0][1]["action"] == "сработал лимит провайдера"


def test_one_action_can_use_gemini_only_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_reorder_for_cooldown", lambda order: order)
    monkeypatch.setattr(ai, "_gen_gemini", lambda *_args: calls.append("gemini") or "first")
    monkeypatch.setattr(ai, "_gen_groq", lambda *_args: calls.append("groq") or "second")

    trace = tracking.start_action("42", "Поездка", "travel", budget_seconds=10)
    try:
        assert ai.llm("one", order=("gemini", "groq")) == "first"
        assert ai.llm("two", order=("gemini", "groq")) == "second"
    finally:
        tracking.finish_action(trace)

    assert calls == ["gemini", "groq"]


def test_parallel_calls_reserve_one_gemini_slot_atomically():
    class RaceDict(dict):
        def __init__(self):
            super().__init__()
            self.barrier = threading.Barrier(2)

        def get(self, key, default=None):
            value = super().get(key, default)
            try:
                self.barrier.wait(timeout=0.05)
            except threading.BrokenBarrierError:
                pass
            return value

    trace = tracking.start_action("42", "Поездка", "parallel", budget_seconds=10)
    trace.provider_calls = RaceDict()
    def reserve(_i):
        token = tracking._current_action.set(trace)
        try:
            return tracking.consume_provider_budget("gemini", limit=1)
        finally:
            tracking._current_action.reset(token)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
    finally:
        tracking.finish_action(trace)

    assert results.count(True) == 1
    assert results.count(False) == 1


def test_premium_fallback_keeps_action_statistics(monkeypatch):
    memory = {}
    calls = []
    monkeypatch.setattr(tracking.store, "_load", lambda key: memory.get(key, {}))
    monkeypatch.setattr(tracking.store, "_save", lambda key, value: memory.__setitem__(key, value))
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_reorder_for_cooldown", lambda order: order)
    monkeypatch.setattr(ai, "_mark_cooldown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)

    def unavailable(*_args, **_kwargs):
        calls.append("gemini")
        raise ai.LLMProviderError("gemini", "temporary", status_code=503, temporary=True)

    monkeypatch.setattr(ai, "_gen_gemini", unavailable)
    monkeypatch.setattr(ai, "_gen_groq", lambda *_args, **_kwargs: calls.append("groq") or '{"ok":true}')

    trace = tracking.start_action("42", "Поездка", "country", budget_seconds=10)
    try:
        assert ai.llm_json("country", module="travel") == {"ok": True}
    finally:
        tracking.finish_action(trace)

    row = memory[tracking.config.ACTION_LATENCY_KEY]["log"][0]
    assert calls == ["gemini", "groq"]
    assert row["requested_tier"] == "complex"
    assert row["primary"] == "gemini"
    assert row["primary_status"] == "503"
    assert row["served_by"] == "groq_complex"
    assert row["gemini_calls"] == 1


def test_second_action_uses_cached_premium_answer_without_gemini(monkeypatch):
    memory = {}
    calls = []
    monkeypatch.setattr(ai.store, "_load", lambda key: memory.get(key, {}))

    def mutate(key, change):
        updated, result = change(memory.get(key, {}))
        memory[key] = updated
        return result

    monkeypatch.setattr(ai.store, "mutate_kv", mutate)
    monkeypatch.setattr(ai.store, "_save", lambda key, value: memory.__setitem__(key, value))
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_reorder_for_cooldown", lambda order: order)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_gen_gemini", lambda *_args: calls.append("gemini") or '{"ok":true}')

    first = tracking.start_action("42", "Поездка", "first", budget_seconds=10)
    try:
        assert ai.llm_json("country", module="travel") == {"ok": True}
    finally:
        tracking.finish_action(first)
    second = tracking.start_action("42", "Поездка", "second", budget_seconds=10)
    try:
        assert ai.llm_json("country", module="travel") == {"ok": True}
    finally:
        tracking.finish_action(second)

    assert calls == ["gemini"]
    rows = memory[tracking.config.ACTION_LATENCY_KEY]["log"]
    assert rows[-1]["cache_hit"] is True
    assert rows[-1]["gemini_calls"] == 0
