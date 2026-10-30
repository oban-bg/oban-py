from __future__ import annotations

import base64
from typing import Any

from . import etf


def encode_recorded(value: Any) -> str:
    encoded = base64.b64encode(etf.encode(_stripped(value))).decode("ascii")

    return encoded.rstrip("=")


def decode_recorded(encoded: str) -> Any:
    padding = 4 - (len(encoded) % 4)

    if padding != 4:
        encoded = encoded + ("=" * padding)

    return etf.decode(base64.b64decode(encoded))


def _stripped(value: Any) -> Any:
    if isinstance(value, etf.Atom):
        return str(value)
    if isinstance(value, dict):
        return {_stripped(key): _stripped(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_stripped(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_stripped(item) for item in value)

    return value
