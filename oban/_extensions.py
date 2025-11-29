from typing import Callable

_extensions: dict[str, Callable] = {}


def get_ext(name: str, default: Callable) -> Callable:
    return _extensions.get(name, default)


def put_ext(name: str, func: Callable) -> None:
    _extensions[name] = func
