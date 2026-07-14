"""Структурный (AST-based) резолвер маршрутизации callback_data.

Не исполняет ни один handler и не импортирует telegram/config/store — читает
исходный текст bot.py и под-роутеров, строит дерево реальных if/elif-условий
маршрутизации в том порядке, в котором их видит `answer_callback`, и отвечает
на вопрос "к какому handler'у (файл + функция) уйдёт этот конкретный
callback_data", либо "ни к какому" (orphan).

Это НЕ замена ручному чтению кода — это дешёвая, воспроизводимая проверка,
которую можно гонять в тестах и CI. Используется вместо (не вместе с)
verify.audit_callbacks(), у которой было структурное слепое пятно: она
собирала все "data ==" / "data.startswith" условия по всем файлам в одно
плоское множество, из-за чего верхнеуровневый `data.startswith(("set_", ...))`
в bot.py засчитывался как "обработано", даже если внутри settings.handle_callback
ветки для конкретного callback_data не было.
"""
import ast
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

# Файл верхнего роутера и под-роутеры, куда он передаёт управление.
# Ключ - имя под-роутера, которое встречается в вызове (X.handle_callback и т.п.),
# значение - (файл, имя_функции).
_SUBROUTERS = {
    "onboard": ("onboard.py", "handle_callback"),
    "settings": ("settings.py", "handle_callback"),
    "wardrobe": ("wardrobe.py", "handle_callback"),
    "myday": ("myday.py", "handle_callback"),
    "balance": ("balance.py", "handle_callback"),
    "learning_router": ("learning_router.py", "handle_callback"),
    "cleanup": ("cleanup.py", "handle_cleanup"),
}
# handle_notes_callback - отдельная функция в settings.py, с другим именем.
_NOTES_ROUTER = ("settings.py", "handle_notes_callback")


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


def _walk_if_chain(node, subject_name):
    """Генератор (kind, values, body, orelse) для if/elif-цепочки, начиная с node
    (ast.If). Каждый elif в Python AST - это вложенный If в orelse одного элемента."""
    cur = node
    while isinstance(cur, ast.If):
        m = _match_condition(cur.test, subject_name)
        if m is not None:
            kind, values = m
            yield kind, values, cur.body
        if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
            cur = cur.orelse[0]
        else:
            break


def _direct_subrouter_call(stmts):
    """Ищет вызов <module>.handle_callback(...)/handle_notes_callback(...) в
    операторах, не спускаясь во вложенные if (вложенность разбирает вызывающая
    сторона — _body_calls_subrouter)."""
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            continue
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if n.func.attr in ("handle_callback", "handle_notes_callback", "handle_action") and isinstance(n.func.value, ast.Name):
                    return n.func.value.id
    return None


def _body_calls_subrouter(body, callback_data, subject_name="data"):
    """Определяет, какой под-роутер реально получит `callback_data`, учитывая
    возможный вложенный if/else внутри тела ветки (например `as_*` дальше делится
    на balance.py и settings.py по более узкому префиксу).

    Возвращает имя модуля (например "settings") или None, если тело не делегирует
    ни одному под-роутеру для этого конкретного callback_data."""
    # Сначала: есть ли вложенный if/else, разбивающий этот же subject дальше?
    for stmt in body:
        if isinstance(stmt, ast.If):
            m = _match_condition(stmt.test, subject_name)
            if m is not None:
                kind, values = m
                matched = (
                    (kind == "exact" and callback_data in values)
                    or (kind == "prefix" and any(callback_data.startswith(p) for p in values))
                )
                if matched:
                    sub = _direct_subrouter_call(stmt.body)
                    if sub is not None:
                        return sub
                    # совпало, но внутри нет прямого вызова — рекурсия на случай
                    # более глубокой вложенности
                    nested = _body_calls_subrouter(stmt.body, callback_data, subject_name)
                    if nested is not None:
                        return nested
                    continue
                else:
                    # не совпало с if-веткой — переходим в else
                    sub = _direct_subrouter_call(stmt.orelse)
                    if sub is not None:
                        return sub
                    nested = _body_calls_subrouter(stmt.orelse, callback_data, subject_name)
                    if nested is not None:
                        return nested
                    continue
    # Нет вложенного if по subject — просто ищем прямой вызов на верхнем уровне.
    return _direct_subrouter_call(body)


def _extract_act_prefix_rules(body):
    """Внутри `if data.startswith("a_"): act = data[2:]; try: if act == "x": ...`
    вытаскивает elif-цепочку по имени `act` и переводит её обратно в правила по `data`
    (с восстановленным префиксом "a_")."""
    rules = []
    # Ищем `act = data[2:]` чтобы подтвердить срез, затем ищем if/elif-цепочку по act
    # внутри try/except или напрямую в теле.
    for stmt in body:
        if isinstance(stmt, ast.Try):
            inner = stmt.body
        else:
            inner = [stmt]
        for s in inner:
            if isinstance(s, ast.If):
                for kind, values, _sub_body in _walk_if_chain(s, "act"):
                    prefixed = {"a_" + v for v in values}
                    rules.append((kind, prefixed))
    return rules


def _handled_by_toplevel(callback_data, tree):
    """Проходит по всем if/elif верхнего уровня функции answer_callback в порядке
    объявления. Возвращает (True, subrouter_module_or_None) на первом совпадении,
    либо (False, None), если ни одна ветка не совпала."""
    fn = _find_function(tree, "_answer_callback_impl")
    if fn is None:
        raise RuntimeError("_answer_callback_impl не найдена в bot.py — резолвер рассинхронизирован с кодом")

    for stmt in fn.body:
        if not isinstance(stmt, ast.If):
            continue
        m = _match_condition(stmt.test, "data")
        if m is None:
            continue
        kind, values = m
        matched = (
            (kind == "exact" and callback_data in values)
            or (kind == "prefix" and any(callback_data.startswith(p) for p in values))
        )
        if not matched:
            continue
        # Особый случай a_<act>: часть действий делегирована
        # локальному роутеру learning, остальные остаются в bot.py.
        if kind == "prefix" and "a_" in values:
            learning_tree = ast.parse(_read_source("learning_router.py"))
            learning_action = _find_function(learning_tree, "handle_action")
            if (learning_action is not None
                    and _sub_router_handles(callback_data[2:], learning_action, "act")):
                return True, "learning_router"
            act_rules = _extract_act_prefix_rules(stmt.body)
            for act_kind, act_values in act_rules:
                act_matched = (
                    (act_kind == "exact" and callback_data in act_values)
                    or (act_kind == "prefix" and any(callback_data.startswith(p) for p in act_values))
                )
                if act_matched:
                    return True, None
            # data.startswith("a_") совпал, но конкретного act-правила нет —
            # это НЕ обработано (тело падает в try/except без действия для этого act).
            return False, None
        sub = _body_calls_subrouter(stmt.body, callback_data)
        if sub is not None:
            return True, sub
        return True, None
    return False, None


def resolve_callback_handler(callback_data: str):
    """Определяет, какой handler реально обработает данный callback_data.

    Возвращает dict {"handled": bool, "module": str|None, "detail": str} —
    "module" - под-роутер (settings/wardrobe/myday/balance/learning_router/cleanup/onboard),
    None если обработка целиком в bot.py, либо None+handled=False, если callback
    не совпал ни с одной веткой ни на одном уровне.

    Не исполняет ни один handler - только структурно проходит AST bot.py и,
    при необходимости, под-роутера, куда bot.py передаёт управление.
    """
    bot_src = _read_source("bot.py")
    bot_tree = ast.parse(bot_src)

    handled_top, sub_module = _handled_by_toplevel(callback_data, bot_tree)
    if not handled_top:
        return {"handled": False, "module": None, "detail": "no matching branch in bot.py answer_callback"}
    if sub_module is None:
        return {"handled": True, "module": "bot.py", "detail": "handled directly in answer_callback"}

    # Определяем, какая функция под-роутера вызывается: handle_notes_callback у
    # settings.py используется для fav_/ls_/as_(не food/fridge/...) веток, а
    # handle_callback - для set_/setadd_/setdel_.
    file_name, func_name = _resolve_subrouter_target(sub_module, callback_data, bot_tree)
    sub_src = _read_source(file_name)
    sub_tree = ast.parse(sub_src)
    fn = _find_function(sub_tree, func_name)
    if fn is None:
        return {"handled": False, "module": sub_module,
                "detail": f"{file_name}:{func_name} not found — resolver out of sync"}

    routed_value = callback_data[2:] if func_name == "handle_action" and callback_data.startswith("a_") else callback_data
    subject_name = "act" if func_name == "handle_action" else "data"
    if _sub_router_handles(routed_value, fn, subject_name):
        return {"handled": True, "module": f"{file_name}:{func_name}", "detail": "matched inside sub-router"}
    return {"handled": False, "module": f"{file_name}:{func_name}",
            "detail": "reached sub-router but no matching branch inside it"}


def _resolve_subrouter_target(sub_module, callback_data, bot_tree):
    if sub_module == "learning_router" and callback_data.startswith("a_"):
        return "learning_router.py", "handle_action"
    if sub_module != "settings":
        return _SUBROUTERS[sub_module]
    # settings.py has two entrypoints; bot.py's own branching decides which one.
    if callback_data.startswith(("fav_", "ls_")):
        return _NOTES_ROUTER
    if callback_data.startswith("as_") and not callback_data.startswith(
        ("as_food", "as_fridge", "as_recipe", "as_my_recipe", "as_daycheck", "as_motiv", "as_doctor")
    ):
        return _NOTES_ROUTER
    return _SUBROUTERS["settings"]


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
