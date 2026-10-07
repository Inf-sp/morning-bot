"""Статические инварианты архитектуры: раньше гонялись на каждом старте бота."""
import ast
import glob
import os
import random
import re

import srs
import trainer_engine as engine
import trainer_exercises as exercises
import trainer_grading as grading
import trainer_session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PURE = {"telegram", "store", "ai", "config", "repositories"}
FORBIDDEN_IMPORTS = {
    "trainer_engine.py": {"telegram", "store", "ai"},
    "trainer_exercises.py": {"telegram", "store", "ai"},
    "trainer_grading.py": {"telegram", "store", "ai"},
    "dictionary_model.py": _PURE,
    "dictionary_normalize.py": {"telegram", "store", "ai"},
    "dictionary_repository.py": {"telegram", "ai"},
    "dictionary_seed_state.py": {"telegram", "ai"},
    "dictionary_seed_catalog.py": _PURE,
    "wardrobe_model.py": _PURE,
    "fridge_model.py": _PURE,
    "recommendation_rotation.py": _PURE,
    "wardrobe_outfit.py": {"telegram", "ai"},
    "wardrobe_purchase.py": _PURE,
    "weather_provider.py": {"telegram", "ai"},
    "response_delivery.py": {"ai"},
    "provider_runtime.py": {"ai", "api_usage", "requests", "service_monitor", "telegram"},
}


def _source(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as file:
        return file.read()


def _imports(name):
    found = set()
    for node in ast.walk(ast.parse(_source(name))):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_pure_layers_do_not_import_runtime_modules():
    violations = {
        name: sorted(_imports(name) & denied)
        for name, denied in FORBIDDEN_IMPORTS.items()
        if _imports(name) & denied
    }
    assert violations == {}


def test_trainer_builds_every_exercise_and_round_trips_session():
    base = {
        "term": "ik vergelijk deze boeken", "translation": "я сравниваю эти книги", "lang": "nl",
        "examples": [{"text": "Ik vergelijk deze boeken.", "translation": "Я сравниваю эти книги."}],
    }
    error_entry = {
        "term": "nieuw", "translation": "новый", "lang": "nl",
        "pos": "adjective", "verified_error_rule": "een_de_adjective",
        "examples": [{"text": "Ik koop een nieuwe fiets.", "translation": "Я покупаю новый велосипед."}],
    }
    gap_entry = {
        "term": "binnenzetten", "translation": "заносить внутрь", "lang": "nl", "pos": "глагол",
        "examples": [{"text": "Ik moet de bloemen binnenzetten.",
                      "translation": "Мне нужно занести цветы внутрь."}],
    }
    others = [
        base,
        {"term": "de tafel", "translation": "стол", "lang": "nl"},
        {"term": "het huis", "translation": "дом", "lang": "nl"},
        {"term": "goedemorgen", "translation": "доброе утро", "lang": "nl"},
        {"term": "tot straks", "translation": "до скорого", "lang": "nl"},
        {"term": "meenemen", "translation": "брать с собой", "lang": "nl", "pos": "глагол"},
        {"term": "vervangen", "translation": "заменять", "lang": "nl", "pos": "глагол"},
    ]
    situation = {"line": "Welke boeken kies je?", "line_ru": "Какие книги ты выбираешь?"}
    special = {engine.EXERCISE_FIND_ERROR: error_entry, engine.EXERCISE_FILL_GAP: gap_entry}
    for kind in engine.ALL_EXERCISES:
        entry = special.get(kind, base)
        assert exercises.build_exercise(entry, others, kind, situation=situation, rng=random.Random(7)), kind

    queue = engine.build_training_queue([{**e, "srs_level": 0} for e in others], rng=random.Random(4))
    assert queue and all("exercise_type" in item for item in queue)

    grade = grading.grade_free_text({"correct": "goedemorgen"}, "goedemorgen")
    assert grade.correct
    state = srs.record_answer(srs.default_srs_state(), engine.EXERCISE_RECALL, grade.quality)
    assert state["srs_history"][-1]["result"] == "recalled_free"

    cid = "__architecture_session_test__"
    trainer_session.start(cid, "nl", queue[:1])
    assert trainer_session.get(cid)["queue_idx"] == 0
    trainer_session.finish(cid)
    assert trainer_session.get(cid) is None


def test_main_menu_opens_as_new_transient_message():
    branch = _source("bot_callbacks.py").partition("async def _main_menu(c):")[2].partition("\n\n\n")[0]
    assert branch and 'R("m_menu", _main_menu)' in _source("bot_callbacks.py")
    assert "bot.send_message(" in branch and "transient=True" in branch
    assert "edit_text(" not in branch and "delete(" not in branch


def test_trainer_answer_navigation_is_complete():
    trainer, router = _source("trainer.py"), _source("learning_router.py")
    assert "ex_next_" in trainer and '"m_menu"' in trainer
    assert 'data.startswith("ex_next_")' in router and "trainer.next_exercise" in router
    delivery = _source("response_delivery.py")
    assert '"m_close"' in delivery and '"m_menu"' in delivery


def test_no_hardcoded_secrets():
    key_assign = re.compile(r"""(?i)(api[_-]?key|token|secret|password|passwd)\s*=\s*["']([^"']{12,})["']""")
    literal = re.compile(r"""["'](sk-[A-Za-z0-9]{12,}|AIza[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{12,})["']""")
    findings = []
    for path in glob.glob(os.path.join(ROOT, "*.py")):
        src = open(path, encoding="utf-8").read()
        name = os.path.basename(path)
        findings += [f"{name}: {m.group(1)}" for m in key_assign.finditer(src)]
        findings += [f"{name}: {m.group(1)[:8]}" for m in literal.finditer(src)]
    assert findings == []
