from collections.abc import Callable
from typing import Any

_extensions: dict[str, Callable] = {}
_replays: dict[str, Callable] = {}


def get_ext(name: str, default: Callable) -> Callable:
    return _extensions.get(name, default)


def put_ext(name: str, func: Callable) -> None:
    _extensions[name] = func

    if replay := _replays.get(name):
        replay(func)


def replay_on_put(name: str, replay: Callable) -> None:
    _replays[name] = replay


def use_ext(name: str, default: Callable, /, *args, **kwargs) -> Any:
    func = _extensions.get(name, default)
    return func(*args, **kwargs)
