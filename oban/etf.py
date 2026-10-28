"""Erlang External Term Format codec shared by Oban packages.

This module is for internal use by Oban and Oban Pro, and isn't part of the documented API.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from typing import Any

VERSION = 131

# Tags we encode or decode. See erts/emulator/beam/external.h
COMPRESSED = 80
NEW_FLOAT = 70
NEW_PID = 88
NEWER_REFERENCE = 90
SMALL_INTEGER = 97
INTEGER = 98
ATOM = 100
SMALL_TUPLE = 104
LARGE_TUPLE = 105
NIL = 106
STRING = 107
LIST = 108
BINARY = 109
SMALL_BIG = 110
LARGE_BIG = 111
NEW_REFERENCE = 114
SMALL_ATOM = 115
MAP = 116
ATOM_UTF8 = 118
SMALL_ATOM_UTF8 = 119

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


class Atom(str):
    """An Erlang atom, distinct from a string so that it isn't encoded as a binary."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Atom({str.__repr__(self)})"


@dataclass(frozen=True, slots=True)
class Pid:
    """An Erlang process identifier."""

    node: str
    id: int
    serial: int
    creation: int


@dataclass(frozen=True, slots=True)
class Reference:
    """An Erlang reference."""

    node: str
    ids: tuple[int, ...]
    creation: int


def encode(value: Any, *, compressed: bool = True) -> bytes:
    """Encode a value in Erlang term format.

    With `compressed`, the term is compressed when that's smaller, the same as
    `:erlang.term_to_binary(value, [:compressed])`.

    Strings and bytes become binaries, `None` becomes `nil`, booleans and `Atom` become atoms,
    tuples become tuples, lists become lists, and dicts become maps.

    Raises:
        TypeError: If the value, or anything within it, has no Erlang term form.
    """
    term = bytearray()

    _encode(term, value)

    if compressed:
        deflated = zlib.compress(term)

        if len(deflated) + 5 < len(term):
            return struct.pack(">BBI", VERSION, COMPRESSED, len(term)) + deflated

    return bytes([VERSION]) + term


def decode(data: bytes) -> Any:
    """Decode a value from Erlang term format, compressed or not.

    Binaries decode as strings, or as bytes when they aren't valid UTF-8. The atoms `true`,
    `false`, and `nil` decode as booleans and `None`, while other atoms decode as `Atom`.

    Raises:
        ValueError: If the data isn't a complete, well-formed term.
    """
    value, _offset = decode_from(data)

    return value


def decode_from(data: bytes, offset: int = 0) -> tuple[Any, int]:
    """Decode one term starting at offset, returning the value and the offset after it.

    This allows decoding terms that are packed together, such as distribution messages.
    """
    reader = _Reader(data, offset)

    if reader.uint8() != VERSION:
        raise ValueError("Expected an Erlang term starting with 131")

    if reader.peek() != COMPRESSED:
        return reader.term(), reader.offset

    reader.uint8()

    size = reader.uint32()
    inflater = zlib.decompressobj()

    try:
        inflated = inflater.decompress(reader.rest())
    except zlib.error as error:
        raise ValueError("Compressed Erlang term is invalid") from error

    if len(inflated) != size or not inflater.eof:
        raise ValueError("Compressed Erlang term has the wrong size")

    end = len(data) - len(inflater.unused_data)

    return _Reader(inflated).term(), end


# Encoding


def _encode(term: bytearray, value: Any) -> None:
    if value is None:
        _encode_atom(term, "nil")
    elif isinstance(value, Atom):
        _encode_atom(term, value)
    elif isinstance(value, bool):
        _encode_atom(term, "true" if value else "false")
    elif isinstance(value, int):
        _encode_int(term, value)
    elif isinstance(value, float) and math.isfinite(value):
        term.append(NEW_FLOAT)
        term += struct.pack(">d", value)
    elif isinstance(value, str):
        _encode_binary(term, value.encode("utf-8"))
    elif isinstance(value, (bytes, bytearray)):
        _encode_binary(term, value)
    elif isinstance(value, tuple):
        _encode_tuple(term, value)
    elif isinstance(value, list):
        _encode_list(term, value)
    elif isinstance(value, dict):
        _encode_map(term, value)
    elif isinstance(value, Pid):
        term.append(NEW_PID)
        _encode_atom(term, value.node)
        term += struct.pack(">III", value.id, value.serial, value.creation)
    else:
        raise TypeError(f"Cannot encode {_describe(value)} as an Erlang term")


def _encode_atom(term: bytearray, name: str) -> None:
    encoded = name.encode("utf-8")

    if len(encoded) <= 255:
        term += struct.pack(">BB", SMALL_ATOM_UTF8, len(encoded))
    else:
        term += struct.pack(">BH", ATOM_UTF8, len(encoded))

    term += encoded


def _encode_int(term: bytearray, value: int) -> None:
    if 0 <= value <= 255:
        term += struct.pack(">BB", SMALL_INTEGER, value)
    elif INT32_MIN <= value <= INT32_MAX:
        term += struct.pack(">Bi", INTEGER, value)
    else:
        magnitude = abs(value)
        digits = magnitude.to_bytes((magnitude.bit_length() + 7) // 8, "little")
        sign = 1 if value < 0 else 0

        if len(digits) <= 255:
            term += struct.pack(">BBB", SMALL_BIG, len(digits), sign)
        else:
            term += struct.pack(">BIB", LARGE_BIG, len(digits), sign)

        term += digits


def _encode_binary(term: bytearray, value: bytes | bytearray) -> None:
    term += struct.pack(">BI", BINARY, len(value))
    term += value


def _encode_tuple(term: bytearray, value: tuple) -> None:
    if len(value) <= 255:
        term += struct.pack(">BB", SMALL_TUPLE, len(value))
    else:
        term += struct.pack(">BI", LARGE_TUPLE, len(value))

    for item in value:
        _encode(term, item)


def _encode_list(term: bytearray, value: list) -> None:
    if value:
        term += struct.pack(">BI", LIST, len(value))

        for item in value:
            _encode(term, item)

    term.append(NIL)


def _encode_map(term: bytearray, value: dict) -> None:
    term += struct.pack(">BI", MAP, len(value))

    for key, item in value.items():
        _encode(term, key)
        _encode(term, item)


def _describe(value: Any) -> str:
    if isinstance(value, float):
        return str(value)

    return type(value).__name__


# Decoding


class _Reader:
    def __init__(self, data: bytes, offset: int = 0) -> None:
        self.data = memoryview(data)
        self.offset = offset

    def peek(self) -> int | None:
        if self.offset < len(self.data):
            return self.data[self.offset]

        return None

    def uint8(self) -> int:
        return self.take(1)[0]

    def uint16(self) -> int:
        return self.unpack(">H")

    def uint32(self) -> int:
        return self.unpack(">I")

    def unpack(self, fmt: str) -> Any:
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def rest(self) -> bytes:
        return bytes(self.take(len(self.data) - self.offset))

    def take(self, length: int) -> memoryview:
        if self.offset + length > len(self.data):
            raise ValueError("Unexpected end of Erlang term")

        chunk = self.data[self.offset : self.offset + length]

        self.offset += length

        return chunk

    def term(self) -> Any:
        tag = self.uint8()

        if tag == SMALL_INTEGER:
            return self.uint8()
        if tag == INTEGER:
            return self.unpack(">i")
        if tag == SMALL_BIG:
            return self.big(self.uint8())
        if tag == LARGE_BIG:
            return self.big(self.uint32())
        if tag == NEW_FLOAT:
            return self.unpack(">d")
        if tag in (ATOM, ATOM_UTF8):
            return self.atom(self.uint16(), tag == ATOM)
        if tag in (SMALL_ATOM, SMALL_ATOM_UTF8):
            return self.atom(self.uint8(), tag == SMALL_ATOM)
        if tag == BINARY:
            return self.binary(self.uint32())
        # Lists of bytes, such as charlists, are packed as strings
        if tag == STRING:
            return list(self.take(self.uint16()))
        if tag == NIL:
            return []
        if tag == LIST:
            return self.list_term()
        if tag == SMALL_TUPLE:
            return tuple(self.items(self.uint8()))
        if tag == LARGE_TUPLE:
            return tuple(self.items(self.uint32()))
        if tag == MAP:
            return self.map_term()
        if tag == NEW_PID:
            return self.pid()
        if tag == NEWER_REFERENCE:
            return self.reference(">I")
        if tag == NEW_REFERENCE:
            return self.reference(">B")

        raise ValueError(f"Cannot decode Erlang term with tag {tag}")

    def big(self, length: int) -> int:
        sign = self.uint8()
        value = int.from_bytes(self.take(length), "little")

        return -value if sign else value

    def atom(self, length: int, latin1: bool) -> Any:
        name = str(self.take(length), "latin1" if latin1 else "utf-8")

        match name:
            case "true":
                return True
            case "false":
                return False
            case "nil":
                return None
            case _:
                return Atom(name)

    def binary(self, length: int) -> str | bytes:
        chunk = bytes(self.take(length))

        try:
            return chunk.decode("utf-8")
        except UnicodeDecodeError:
            return chunk

    def items(self, length: int) -> list[Any]:
        return [self.term() for _ in range(length)]

    def list_term(self) -> list[Any]:
        items = self.items(self.uint32())

        if self.uint8() != NIL:
            raise ValueError("Cannot decode an improper list")

        return items

    def map_term(self) -> dict[Any, Any]:
        length = self.uint32()

        return {self.term(): self.term() for _ in range(length)}

    def pid(self) -> Pid:
        node = self.term()
        number, serial, creation = struct.unpack(">III", self.take(12))

        return Pid(str(node), number, serial, creation)

    def reference(self, creation_format: str) -> Reference:
        length = self.uint16()
        node = self.term()
        creation = self.unpack(creation_format)
        ids = struct.unpack(f">{length}I", self.take(length * 4))

        return Reference(str(node), ids, creation)
