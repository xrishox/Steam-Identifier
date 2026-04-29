from __future__ import annotations

import struct
import unittest

from steam_identifier.parsers import parse_shortcuts_vdf, parse_vdf_text


def make_shortcuts_blob(appid: int, appname: str) -> bytes:
    return (
        b"\x00shortcuts\x00"
        b"\x000\x00"
        b"\x02appid\x00"
        + struct.pack("<i", appid)
        + b"\x01appname\x00"
        + appname.encode("utf-8")
        + b"\x00"
        + b"\x01exe\x00/tmp/example-game/CustomGame.exe\x00"
        + b"\x01StartDir\x00/tmp/example-game/\x00"
        + b"\x08"
        + b"\x08"
        + b"\x08"
    )


class ParserTests(unittest.TestCase):
    def test_parse_vdf_text_nested_values(self) -> None:
        parsed = parse_vdf_text(
            '"AppState" { "appid" "123456" "name" "Example Game" "UserConfig" { "language" "english" } }'
        )

        self.assertEqual(parsed["AppState"]["appid"], "123456")
        self.assertEqual(parsed["AppState"]["name"], "Example Game")
        self.assertEqual(parsed["AppState"]["UserConfig"]["language"], "english")

    def test_parse_shortcuts_vdf_reads_unsigned_appid(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            from pathlib import Path

            path = Path(tmpdir) / "shortcuts.vdf"
            path.write_bytes(make_shortcuts_blob(-1759620880, "CustomGame.exe"))

            shortcuts = parse_shortcuts_vdf(path)

        self.assertEqual(len(shortcuts), 1)
        self.assertEqual(shortcuts[0].appid, "2535346416")
        self.assertEqual(shortcuts[0].appname, "CustomGame.exe")


if __name__ == "__main__":
    unittest.main()
