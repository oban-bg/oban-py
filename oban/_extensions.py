from typing import Any, Callable

_extensions: dict[str, Callable] = {}


def get_ext(name: str, default: Callable) -> Callable:
    return _extensions.get(name, default)


def put_ext(name: str, func: Callable) -> None:
    _extensions[name] = func


def use_ext(name: str, default: Callable, *args, **kwargs) -> Any:
    func = _extensions.get(name, default)
    return func(*args, **kwargs)
