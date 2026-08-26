import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import recipe_generation
import spoonacular
from ui.food import food_card
from ui.menu import food_menu


class FakeResponse:
    status_code = 200
    headers = {"x-api-quota-request": "1"}

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _source():
    return {
        "id": "716429",
        "name": "Pasta with Garlic, Scallions, Cauliflower & Breadcrumbs",
        "category": "main course",
        "area": "Italian",
        "instructions": ["Boil the pasta.", "Add the vegetables."],
        "ingredients": [{"name": "pasta", "amount": 100, "unit": "g"}],
        "ready_minutes": 25,
        "servings": 2,
        "missed_ingredients": ["parsley"],
        "thumbnail": "https://img.spoonacular.com/recipes/716429-556x370.jpg",
        "source_provider": "spoonacular",
    }


def test_fridge_search_fetches_ten_candidates_and_bulk_details(monkeypatch):
    calls = []
    usage = []
    monkeypatch.setattr(spoonacular.config, "SPOONACULAR_API_KEY", "secret-key")
    monkeypatch.setattr(spoonacular.util, "ttl_get", lambda *_args: None)
    monkeypatch.setattr(spoonacular.util, "ttl_set", lambda *_args: None)
    monkeypatch.setattr(
        spoonacular.api_usage, "record_request",
        lambda service, **kwargs: usage.append((service, kwargs)),
    )

    search = [{
        "id": 716429,
        "title": "Pasta with Garlic",
        "usedIngredientCount": 3,
        "missedIngredientCount": 1,
        "missedIngredients": [{"name": "parsley"}],
    }]
    details = [{
        "id": 716429,
        "title": "Pasta with Garlic",
        "readyInMinutes": 25,
        "servings": 2,
        "dishTypes": ["main course"],
        "cuisines": ["Italian"],
        "extendedIngredients": [{
            "name": "pasta", "amount": 200, "unit": "g", "original": "200 g pasta",
        }],
        "analyzedInstructions": [{"steps": [{"step": "Boil the pasta."}]}],
    }]

    def fake_get(url, params, timeout):
        calls.append((url, dict(params), timeout))
        return FakeResponse(search if url.endswith("findByIngredients") else details)

    monkeypatch.setattr(spoonacular.requests, "get", fake_get)

    result = spoonacular.source_recipes(
        "рецепт из холодильника", ingredients="курица, рис, помидоры", limit=10,
    )

    assert calls[0][0] == "https://api.spoonacular.com/recipes/findByIngredients"
    assert calls[0][1]["ingredients"] == "chicken,rice,tomato"
    assert calls[0][1]["number"] == 10
    assert calls[1][0] == "https://api.spoonacular.com/recipes/informationBulk"
    assert calls[1][1]["ids"] == "716429"
    assert result[0]["instructions"] == ["Boil the pasta."]
    assert all(service == "spoonacular" for service, _kwargs in usage)


def test_spoonacular_is_primary_and_gemini_selects_translates_adapts(monkeypatch):
    captured = {}
    monkeypatch.setattr(recipe_generation.config, "SPOONACULAR_API_KEY", "configured")
    monkeypatch.setattr(recipe_generation.spoonacular, "source_recipes", lambda *_args, **_kwargs: [_source()])
    monkeypatch.setattr(
        recipe_generation, "_themealdb_sources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )
    monkeypatch.setattr(recipe_generation, "_cuisine_context", lambda _cid: "Любит итальянскую кухню")
    monkeypatch.setattr(recipe_generation, "_leftover_recent", lambda _cid: [])

    def fake_llm(prompt, *_args, **kwargs):
        captured.update({"prompt": prompt, "kwargs": kwargs})
        return {
            "source_recipe_id": "716429",
            "name": "Паста с цветной капустой",
            "time": "25 мин",
            "servings": "1 порц.",
            "ingredients": "паста, цветная капуста, чеснок",
            "steps": ["Отвари пасту", "Добавь овощи"],
        }

    monkeypatch.setattr(recipe_generation.ai, "llm_json", fake_llm)

    result = recipe_generation._gen_recipe("ужин", cid="42")

    assert "Spoonacular — единственный источник базовых рецептов" in captured["prompt"]
    assert "переведи на русский и адаптируй" in captured["prompt"]
    assert result["spoonacular_id"] == "716429"
    assert result["spoonacular_source_name"].startswith("Pasta with Garlic")


def test_themealdb_is_used_when_spoonacular_returns_no_recipes(monkeypatch):
    fallback = [{"id": "52772", "name": "Fallback meal"}]
    monkeypatch.setattr(recipe_generation.config, "SPOONACULAR_API_KEY", "configured")
    monkeypatch.setattr(recipe_generation.spoonacular, "source_recipes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(recipe_generation, "_themealdb_sources", lambda *_args, **_kwargs: fallback)

    assert recipe_generation._recipe_sources("ужин") == fallback


def test_cooking_idea_card_has_no_extra_drink_block():
    message = food_menu({
        "name": "Паста с овощами",
        "ingredients": ["паста", "овощи"],
        "steps": ["Отвари пасту", "Добавь овощи"],
    })

    assert "🌍 Готовим сегодня · Международная кухня" in message.text
    assert "🥣 Питание · Идея на сегодня" not in message.text
    assert "Напиток" not in message.text


def test_cooking_home_shows_three_preparation_steps():
    message = food_menu({
        "name": "Паста с овощами",
        "ingredients": ["паста", "овощи"],
        "steps": ["Отвари пасту", "Нарежь овощи", "Смешай", "Добавь зелень"],
    })

    assert message.text.count("• ") == 3
    assert "Добавь зелень" not in message.text


def test_recipe_card_hides_time_and_image():
    message = food_card({
        "name": "Паста с овощами",
        "time": "25 мин",
        "servings": "2 порц.",
        "ingredients": "паста, овощи",
        "steps": ["Отвари пасту", "Добавь овощи"],
        "image": "https://img.spoonacular.com/recipe.jpg",
    })

    assert "порц." not in message.text
    assert "25 мин" not in message.text
    assert "⏱" not in message.text
    assert "Фото блюда" not in message.text
    assert "img.spoonacular.com" not in message.text


def test_selected_meal_card_uses_meal_and_cuisine_in_header():
    message = food_card({
        "name": "Тосты с томатами",
        "cuisine": "italian",
        "ingredients": "хлеб, томаты",
        "steps": ["Поджарь хлеб", "Добавь томаты"],
    }, label="Завтрак", meal="breakfast", show_cuisine_emoji=False)

    assert "🥐 Завтрак · Итальянская кухня" in message.text
    assert "Готовим сегодня" not in message.text
    assert "🇮🇹" not in message.text


def test_all_llms_down_still_returns_spoonacular_card(monkeypatch):
    monkeypatch.setattr(recipe_generation.config, "SPOONACULAR_API_KEY", "configured")
    monkeypatch.setattr(recipe_generation.spoonacular, "source_recipes", lambda *_args, **_kwargs: [_source()])
    monkeypatch.setattr(recipe_generation, "_cuisine_context", lambda _cid: "")
    monkeypatch.setattr(recipe_generation, "_leftover_recent", lambda _cid: [])
    monkeypatch.setattr(
        recipe_generation.ai, "llm_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("all LLMs unavailable")),
    )

    result = recipe_generation._gen_recipe("ужин", cid="42")

    assert result["name"] == "Быстрый омлет с овощами"
    assert recipe_generation._recipe_is_russian(result) is True
    assert "Pasta" not in str(result)


def test_code_card_formats_source_fields_without_ai():
    message = food_menu({
        "name": "Pasta with Garlic",
        "minutes": 25,
        "servings": "2 порц.",
        "ingredients": ["100 g pasta", "1 cauliflower"],
        "missing_ingredients": ["parsley"],
        "steps": ["Boil the pasta.", "Add the vegetables."],
        "image": "https://img.spoonacular.com/recipes/716429-556x370.jpg",
        "code_fallback": True,
    })

    assert "порц." not in message.text
    assert "⏱" not in message.text
    assert "25 мин" not in message.text
    assert "Не хватает:\nparsley" in message.text
    assert "Фото блюда" not in message.text
    assert "img.spoonacular.com" not in message.text


def test_home_idea_can_be_built_from_spoonacular_without_ai():
    idea = recipe_generation._source_home_idea(_source(), {
        "available": ["pasta"],
        "has_fridge": True,
    })

    assert idea == {}


def test_home_steps_merge_a_long_recipe_into_three_points():
    steps = recipe_generation._home_steps([
        "Нарежь овощи.", "Разогрей сковороду.", "Обжарь овощи.",
        "Добавь пасту.", "Перемешай и подавай.",
    ])

    assert len(steps) == 3
    assert all(step["text"] for step in steps)


def test_batch_returns_plain_template_when_sources_and_llms_are_down(monkeypatch):
    monkeypatch.setattr(recipe_generation, "_recipe_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(recipe_generation, "_cuisine_context", lambda _cid: "")
    monkeypatch.setattr(
        recipe_generation.ai, "llm_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("all unavailable")),
    )

    result = recipe_generation._gen_recipe_batch("ужин", cid="42")

    assert len(result) == 4
    assert len({item["name"] for item in result}) == 4
    assert all("омлет" not in item["name"].casefold() for item in result)
    assert result[0]["steps"]


def test_incomplete_recipe_batch_response_uses_presentable_local_recipe(monkeypatch):
    monkeypatch.setattr(recipe_generation, "_recipe_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        recipe_generation.ai, "llm_json",
        lambda *_args, **_kwargs: {"recipes": [{"name": "Незавершённый рецепт"}]},
    )

    result = recipe_generation._gen_recipe_batch("ужин", cid="42")

    assert len(result) == 4
    assert len({item["name"] for item in result}) == 4
    assert all("омлет" not in item["name"].casefold() for item in result)
    assert recipe_generation._queue_recipe_presentable(result[0])
