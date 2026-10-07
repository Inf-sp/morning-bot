"""Структурный (AST-based) резолвер маршрутизации callback_data.

Не исполняет ни один handler и не импортирует telegram/config/store — читает
литеральную таблицу ROUTES в bot_callbacks.py и if-ветки под-роутеров и отвечает
на вопрос "к какому handler'у (файл + функция) уйдёт этот конкретный
callback_data", либо "ни к какому" (orphan).

Это НЕ замена ручному чтению кода — это дешёвая, воспроизводимая проверка,
которую можно гонять в тестах и CI. В отличие от плоского поиска всех
"data ==" / "data.startswith" по файлам, верхнеуровневый
`data.startswith(("set_", ...))` не засчитывается как "обработано", если внутри
settings.handle_callback ветки для конкретного callback_data нет.
"""
import ast
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

# Под-роутеры, которым таблица ROUTES передаёт callback (поле ``sub``):
# имя -> (файл, функция).
_SUBROUTERS = {
    "onboard": ("onboard.py", "handle_callback"),
    "settings": ("settings.py", "handle_callback"),
    "wardrobe": ("wardrobe_router.py", "handle_callback"),
    "cooking": ("cooking.py", "handle_callback"),
    "learning_router": ("learning_router.py", "handle_callback"),
    "personal_collections": ("personal_collections.py", "handle_collection_callback"),
}


def _read_source(filename):
    path = os.path.join(_HERE, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _const_str(node):
    """Достаёт строковый литерал из AST-узла, если это возможно."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _str_tuple(node):
    """Достаёт множество строковых литералов из tuple/list constant или call."""
    out = set()
    if isinstance(node, (ast.Tuple, ast.List)):
        for el in node.elts:
            s = _const_str(el)
            if s is not None:
                out.add(s)
    return out


def _match_condition(test, subject_name):
    """Разбирает условие if/elif на предмет `<subject> == "x"`, `<subject> in (...)`,
    `<subject>.startswith("x")`/`<subject>.startswith((...))`.

    Возвращает ("exact", {str,...}) | ("prefix", {str,...}) | None, если условие
    не распознано (например, сложное выражение, объединяющее что-то ещё)."""
    # data.startswith("one") or data.startswith("two")
    #
    # Callback routers often group neighbouring dynamic routes this way.  The
    # audit only needs the union when every branch has the same match type;
    # mixed expressions stay deliberately unsupported instead of producing a
    # false positive.
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        parts = [_match_condition(value, subject_name) for value in test.values]
        if parts and all(part is not None for part in parts):
            kinds = {part[0] for part in parts}
            if len(kinds) == 1:
                return (parts[0][0], set().union(*(part[1] for part in parts)))
        return None
    # data == "x"  /  "x" == data
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
        left, right = test.left, test.comparators[0]
        for a, b in ((left, right), (right, left)):
            if isinstance(a, ast.Name) and a.id == subject_name:
                s = _const_str(b)
                if s is not None:
                    return ("exact", {s})
        return None
    # data in (...)
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.In):
        left = test.left
        if isinstance(left, ast.Name) and left.id == subject_name:
            vals = _str_tuple(test.comparators[0])
            if vals:
                return ("exact", vals)
        return None
    # data.startswith("x") / data.startswith(("x","y"))
    if isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute) and test.func.attr == "startswith":
        obj = test.func.value
        if isinstance(obj, ast.Name) and obj.id == subject_name and test.args:
            arg = test.args[0]
            s = _const_str(arg)
            if s is not None:
                return ("prefix", {s})
            vals = _str_tuple(arg)
            if vals:
                return ("prefix", vals)
        return None
    return None


def _literal_keys(node):
    keys = _const_str(node)
    return (keys,) if keys is not None else tuple(sorted(_str_tuple(node)))


def _matches(keys, data):
    return any(data.startswith(k[:-1]) if k.endswith("*") else data == k for k in keys)


def _table(tree, name):
    """Читает литеральную таблицу ``NAME = (R(keys, handler, sub=...), ...)``."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            rows = []
            for call in node.value.elts:
                sub = next((_const_str(k.value) for k in call.keywords if k.arg == "sub"), None)
                rows.append((_literal_keys(call.args[0]), sub))
            return rows
    raise RuntimeError(f"{name} не найдена в bot_callbacks.py — резолвер рассинхронизирован с кодом")


def _legacy_alias(tree, data):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "LEGACY_ALIASES" for t in node.targets):
            for pair in node.value.elts:
                keys, target = pair.elts
                if _matches(_literal_keys(keys), data):
                    return _const_str(target)
    return data


def resolve_callback_handler(callback_data: str):
    """Определяет, какой handler реально обработает данный callback_data.

    Возвращает dict {"handled": bool, "module": str|None, "detail": str} —
    "module" — "bot_callbacks.py" или "файл:функция" под-роутера.

    Не исполняет ни один handler: читает литеральные таблицы ROUTES/ACTIONS
    bot_callbacks.py и, при необходимости, if-ветки под-роутера.
    """
    tree = ast.parse(_read_source("bot_callbacks.py"))
    data = _legacy_alias(tree, callback_data)
    sub = next((sub for keys, sub in _table(tree, "ROUTES") if _matches(keys, data)), False)
    if sub is False:
        return {"handled": False, "module": None, "detail": "no matching route in bot_callbacks.ROUTES"}
    if sub == "actions":
        act = data[2:]
        learning_action = _find_function(ast.parse(_read_source("learning_router.py")), "handle_action")
        if act != "plany" and _sub_router_handles(act, learning_action, "act"):
            return {"handled": True, "module": "learning_router.py:handle_action",
                    "detail": "matched inside sub-router"}
        if any(_matches(keys, act) for keys, _sub in _table(tree, "ACTIONS")):
            return {"handled": True, "module": "bot_callbacks.py", "detail": "handled directly in callback router"}
        return {"handled": False, "module": None, "detail": "no matching action in bot_callbacks.ACTIONS"}
    if sub is None:
        return {"handled": True, "module": "bot_callbacks.py", "detail": "handled directly in callback router"}

    file_name, func_name = _SUBROUTERS[sub]
    fn = _find_function(ast.parse(_read_source(file_name)), func_name)
    if fn is None:
        return {"handled": False, "module": sub,
                "detail": f"{file_name}:{func_name} not found — resolver out of sync"}
    if _sub_router_handles(data, fn):
        return {"handled": True, "module": f"{file_name}:{func_name}", "detail": "matched inside sub-router"}
    return {"handled": False, "module": f"{file_name}:{func_name}",
            "detail": "reached sub-router but no matching branch inside it"}


def _sub_router_handles(callback_data, fn, subject_name="data"):
    """Ищет совпадение внутри тела под-роутера — плоская if/elif по `data`."""
    for stmt in fn.body:
        for kind, values, _body in _iter_all_ifs(stmt, subject_name):
            if kind == "exact" and callback_data in values:
                return True
            if kind == "prefix" and any(callback_data.startswith(p) for p in values):
                return True
    return False


def _iter_all_ifs(node, subject_name="data"):
    """Рекурсивно обходит все if (в т.ч. вложенные elif-цепочки и if внутри try)
    и для каждого условия по `data` возвращает (kind, values, body)."""
    for n in ast.walk(node):
        if isinstance(n, ast.If):
            m = _match_condition(n.test, subject_name)
            if m is not None:
                kind, values = m
                yield kind, values, n.body
