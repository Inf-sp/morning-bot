"""Bind extracted function code to its original controller namespace."""

from types import FunctionType


def bind_functions(namespace, module, names):
    for name in names:
        extracted = getattr(module, name)
        rebound = FunctionType(
            extracted.__code__, namespace, name,
            extracted.__defaults__, extracted.__closure__,
        )
        rebound.__kwdefaults__ = extracted.__kwdefaults__
        rebound.__annotations__ = extracted.__annotations__
        rebound.__doc__ = extracted.__doc__
        namespace[name] = rebound

