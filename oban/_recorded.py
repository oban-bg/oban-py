from __future__ import annotations

import base64
from typing import Any

from . import etf


def encode_recorded(value: Any) -> str:
    encoded = base64.b64encode(etf.encode(value)).decode("ascii")

    return encoded.rstrip("=")


def decode_recorded(encoded: str) -> Any:
    padding = 4 - (len(encoded) % 4)

    if padding != 4:
        encoded = encoded + ("=" * padding)

    return etf.decode(base64.b64decode(encoded))
