import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import recipe_generation
import themealdb


class FakeResponse:
    status_code = 200
    headers = {}

    def json(self):
        return {
            "meals": [{
                "idMeal": "52772",
                "strMeal": "Teriyaki Chicken Casserole",
                "strCategory": "Chicken",
                "strArea": "Japanese",
                "strInstructions": "Cook the chicken. Add the sauce.",
                "strIngredient1": "Chicken Breast",
                "strMeasure1": "2",
                "strIngredient2": "Soy Sauce",
                "strMeasure2": "2 tbsp",
                "strMealThumb": "https://www.themealdb.com/meal.jpg",
            }],
        }


def _source():
    return {
        "id": "52772",
        "name": "Teriyaki Chicken Casserole",
        "category": "Chicken",
        "area": "Japanese",
        "instructions": "Cook the chicken. Add the sauce.",
        "ingredients": [
            {"name": "Chicken Breast", "measure": "2"},
            {"name": "Soy Sauce", "measure": "2 tbsp"},
        ],
        "thumbnail": "https://www.themealdb.com/meal.jpg",
    }


def test_public_key_one_is_used_in_api_path(monkeypatch):
    captured = {}
    usage = []
    monkeypatch.setattr(themealdb.config, "THEMEALDB_API_KEY", "1")
    monkeypatch.setattr(themealdb.util, "ttl_get", lambda *_args: None)
    monkeypatch.setattr(themealdb.util, "ttl_set", lambda *_args: None)
    monkeypatch.setattr(
        themealdb.api_usage, "record_request",
        lambda service, **kwargs: usage.append((service, kwargs)),
    )

    def fake_get(url, params, timeout):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(themealdb.requests, "get", fake_get)

    result = themealdb.lookup("52772")

    assert captured["url"] == "https://www.themealdb.com/api/json/v1/1/lookup.php"
    assert captured["params"] == {"i": "52772"}
    assert captured["timeout"] == 8
    assert result["name"] == "Teriyaki Chicken Casserole"
    assert result["ingredients"] == [
        {"name": "Chicken Breast", "measure": "2"},
        {"name": "Soy Sauce", "measure": "2 tbsp"},
    ]
    assert usage[-1][0] == "themealdb"
    assert usage[-1][1]["ok"] is True


def test_fridge_ingredients_select_themealdb_sources(monkeypatch):
    fields = []

    def fake_filter(field, value):
        fields.append((field, value))
        return [{"idMeal": "52772", "strMeal": "Chicken dish"}]

    monkeypatch.setattr(themealdb, "_filter", fake_filter)
    monkeypatch.setattr(themealdb, "lookup", lambda _meal_id: _source())

    result = themealdb.source_recipes(
        "рецепт из холодильника", ingredients="курица, рис, соевый соус", limit=3,
    )

    assert ("i", "chicken") in fields
    assert ("i", "rice") in fields
    assert result[0]["id"] == "52772"


def test_breakfast_uses_breakfast_category(monkeypatch):
    fields = []
    monkeypatch.setattr(
        themealdb, "_filter",
        lambda field, value: fields.append((field, value)) or [
            {"idMeal": "52772", "strMeal": "Breakfast meal"},
        ],
    )
    monkeypatch.setattr(themealdb, "lookup", lambda _meal_id: _source())

    themealdb.source_recipes("завтрак", limit=1)

    assert fields == [("c", "Breakfast")]


def test_gemini_prompt_selects_translates_and_adapts_themealdb(monkeypatch):
    captured = {}
    monkeypatch.setattr(recipe_generation, "_themealdb_sources", lambda *_args, **_kwargs: [_source()])
    monkeypatch.setattr(recipe_generation, "_cuisine_context", lambda _cid: "Любит японскую кухню")
    monkeypatch.setattr(recipe_generation, "_leftover_recent", lambda _cid: [])
    monkeypatch.setattr(recipe_generation, "_my_recipe_pref", lambda _cid: "")

    def fake_llm(prompt, *_args, **kwargs):
        captured.update({"prompt": prompt, "kwargs": kwargs})
        return {
            "source_meal_id": "52772",
            "name": "Курица терияки с рисом",
            "time": "30 мин",
            "servings": "1 порц.",
            "ingredients": "курица, рис, соевый соус",
            "steps": ["Обжарь курицу", "Добавь соус"],
        }

    monkeypatch.setattr(recipe_generation.ai, "llm_json", fake_llm)

    result = recipe_generation._gen_recipe("ужин", cid="42")

    assert "TheMealDB — единственный источник базовых рецептов" in captured["prompt"]
    assert "переведи на русский и адаптируй" in captured["prompt"]
    assert "Teriyaki Chicken Casserole" in captured["prompt"]
    assert captured["kwargs"]["module"] == "food"
    assert result["themealdb_id"] == "52772"
    assert result["themealdb_source_name"] == "Teriyaki Chicken Casserole"


def test_recipe_queue_keeps_themealdb_source_for_each_adaptation(monkeypatch):
    captured = {}
    sources = [_source(), {**_source(), "id": "52800", "name": "Second source"}]
    monkeypatch.setattr(recipe_generation, "_themealdb_sources", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(recipe_generation, "_cuisine_context", lambda _cid: "")
    monkeypatch.setattr(recipe_generation, "_my_recipe_pref", lambda _cid: "")

    def fake_llm(prompt, *_args, **kwargs):
        captured.update({"prompt": prompt, "kwargs": kwargs})
        return {"recipes": [
            {"source_meal_id": "52772", "name": "Первый рецепт"},
            {"source_meal_id": "52800", "name": "Второй рецепт"},
        ]}

    monkeypatch.setattr(recipe_generation.ai, "llm_json", fake_llm)

    result = recipe_generation._gen_recipe_batch("ужин", cid="42", n=2)

    assert [item["themealdb_id"] for item in result] == ["52772", "52800"]
    assert "Second source" in captured["prompt"]
    assert captured["kwargs"]["module"] == "food"


def test_recipe_generation_continues_without_themealdb(monkeypatch):
    monkeypatch.setattr(
        recipe_generation.themealdb, "source_recipes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("service down")),
    )

    assert recipe_generation._themealdb_sources("ужин") == []
