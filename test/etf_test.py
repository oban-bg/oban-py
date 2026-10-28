import base64
import struct

import pytest

from oban import etf
from oban.etf import Atom, Pid, Reference


class TestEncodeAndDecode:
    @pytest.mark.parametrize(
        "values",
        [
            [0, 255, 256, -1],
            [2**31 - 1, -(2**31)],
            [2**31, -(2**53 - 1), 2**64, -(2**64), 2**2100],
            [1.5, -0.25, 1e300],
            ["", "héllo ✓"],
            [True, False, None, Atom("ok"), Atom("ü" * 200)],
            [[], [1, "two", [3]]],
            [(), (1, "two"), tuple(range(300))],
            [{}, {"nested": {"list": [1, 2]}, 1: "one", Atom("key"): None}],
            ["oban" * 100, [{"key": "value"}] * 100],
            [Pid("oban@host", 1, 0, 2)],
        ],
        ids=[
            "small integers",
            "32-bit integers",
            "big integers",
            "floats",
            "strings",
            "atoms",
            "lists",
            "tuples",
            "maps",
            "compressed terms",
            "pids",
        ],
    )
    def test_round_trips(self, values):
        for value in values:
            assert etf.decode(etf.encode(value)) == value

    def test_encodes_bytes_as_binaries(self):
        assert etf.decode(etf.encode(b"oban")) == "oban"
        assert etf.decode(etf.encode(b"\xff\xfe")) == b"\xff\xfe"

    def test_compresses_only_when_smaller(self):
        assert etf.encode("oban")[:2] == bytes([131, 109])
        assert etf.encode("oban" * 100)[:2] == bytes([131, 80])
        assert etf.encode("oban" * 100, compressed=False)[:2] == bytes([131, 109])

    def test_rejects_values_without_an_erlang_term(self):
        with pytest.raises(TypeError, match="Cannot encode nan as an Erlang term"):
            etf.encode(float("nan"))

        with pytest.raises(TypeError, match="Cannot encode inf"):
            etf.encode([float("inf")])

        with pytest.raises(TypeError, match="Cannot encode set"):
            etf.encode({"set": {1}})


class TestDecode:
    def test_decodes_terms_written_by_elixir(self):
        # :erlang.term_to_binary(term, [:compressed]) for the term matched below
        encoded = (
            "g1AAAAC8eJw1jsENwjAQBO3EQSBaoQd40cc5OhKTiw/Zh050QRvUQT+8eQFOIvaxmsdIu2KM2WodAy3lQHjUigetfejixvxz"
            "UEchCxWswULljbG7szYnYpDj/r1Y6ga8ZSlkwY6TyxHVeWYqKlBGbeR6IewrbTAlTpO0SgiZo67bHtK0MpQN35ZH2Pnv53Wf"
            "pSwpxG7G/vkg4h/9FTYE"
        )

        assert etf.decode(base64.b64decode(encoded)) == {
            "string": "héllo",
            "atom": "ok",
            "list": [1, 2, 300],
            "charlist": [97, 98, 99],
            "tuple": ("error", "reason"),
            None: None,
            "bool": False,
            "float": 1.5,
            "neg": -70_000,
            "big": 2**70,
            "keys": {1: "one"},
        }

    def test_decodes_latin1_atoms(self):
        assert etf.decode(bytes([131, 115, 1, 0xE9])) == Atom("é")
        assert etf.decode(bytes([131, 100, 0, 1, 0xE9])) == Atom("é")

    def test_decodes_references(self):
        node = bytes([etf.SMALL_ATOM_UTF8, 3]) + b"a@b"
        blob = (
            bytes([131, etf.NEWER_REFERENCE])
            + struct.pack(">H", 2)
            + node
            + struct.pack(">III", 9, 11, 22)
        )

        assert etf.decode(blob) == Reference("a@b", (11, 22), 9)

    def test_decoding_packed_terms_from_an_offset(self):
        first = etf.encode(Atom("control"), compressed=False)
        second = etf.encode("oban" * 100)
        packed = first + second + b"rest"

        assert etf.decode_from(packed) == (Atom("control"), len(first))
        assert etf.decode_from(packed, len(first)) == ("oban" * 100, len(packed) - 4)

    def test_rejects_malformed_terms(self):
        with pytest.raises(ValueError, match="starting with 131"):
            etf.decode(bytes([130, 106]))

        with pytest.raises(ValueError, match="Unexpected end"):
            etf.decode(bytes([131, 109, 0, 0, 0, 5, 1]))

        with pytest.raises(ValueError, match="Unexpected end"):
            etf.decode(bytes([131, 109, 0, 0]))

        with pytest.raises(ValueError, match="Cannot decode Erlang term with tag 77"):
            etf.decode(bytes([131, 77]))

        with pytest.raises(ValueError, match="improper list"):
            etf.decode(bytes([131, 108, 0, 0, 0, 1, 97, 1, 97, 2]))

        with pytest.raises(ValueError, match="wrong size"):
            etf.decode(etf.encode("oban" * 100)[:-4])
