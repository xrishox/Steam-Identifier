from __future__ import annotations

from dataclasses import dataclass
import struct
from pathlib import Path
from typing import Any


def parse_vdf_text(text: str) -> dict[str, Any]:
    """Parse Valve's simple text VDF format into nested dictionaries."""
    tokens = _tokenize_vdf(text)
    index = 0

    def parse_object() -> dict[str, Any]:
        nonlocal index
        result: dict[str, Any] = {}
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == "}":
                return result
            if token == "{":
                continue
            key = token
            if index >= len(tokens):
                result[key] = ""
                return result
            value = tokens[index]
            index += 1
            if value == "{":
                result[key] = parse_object()
            else:
                result[key] = value
        return result

    return parse_object()


def _tokenize_vdf(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "{}":
            tokens.append(char)
            index += 1
            continue
        if char == '"':
            index += 1
            value: list[str] = []
            while index < length:
                char = text[index]
                if char == "\\" and index + 1 < length:
                    value.append(text[index + 1])
                    index += 2
                    continue
                if char == '"':
                    index += 1
                    break
                value.append(char)
                index += 1
            tokens.append("".join(value))
            continue

        start = index
        while index < length and not text[index].isspace() and text[index] not in "{}":
            index += 1
        tokens.append(text[start:index])
    return tokens


def parse_vdf_file(path: Path) -> dict[str, Any]:
    return parse_vdf_text(path.read_text(encoding="utf-8", errors="replace"))


@dataclass(frozen=True)
class Shortcut:
    appid: str
    appname: str
    exe: str
    start_dir: str


def parse_shortcuts_vdf(path: Path) -> list[Shortcut]:
    data = path.read_bytes()
    root, position = _parse_binary_vdf_object(data, 0)
    if position > len(data):
        raise ValueError(f"shortcuts.vdf parse overran file: {path}")
    shortcuts = root.get("shortcuts", {})
    if not isinstance(shortcuts, dict):
        return []

    parsed: list[Shortcut] = []
    for value in shortcuts.values():
        if not isinstance(value, dict):
            continue
        appid = value.get("appid")
        appname = value.get("appname")
        exe = value.get("exe", "")
        start_dir = value.get("StartDir", "")
        if not isinstance(appid, int) or not isinstance(appname, str):
            continue
        parsed.append(
            Shortcut(
                appid=str(appid & 0xFFFFFFFF),
                appname=appname,
                exe=exe if isinstance(exe, str) else "",
                start_dir=start_dir if isinstance(start_dir, str) else "",
            )
        )
    return parsed


def _parse_binary_vdf_object(data: bytes, position: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while position < len(data):
        value_type = data[position]
        position += 1
        if value_type == 8:
            return result, position

        key, position = _read_c_string(data, position)
        if value_type == 0:
            value, position = _parse_binary_vdf_object(data, position)
        elif value_type == 1:
            value, position = _read_c_string(data, position)
        elif value_type == 2:
            if position + 4 > len(data):
                raise ValueError("truncated int32 in binary VDF")
            value = struct.unpack_from("<i", data, position)[0]
            position += 4
        elif value_type == 7:
            if position + 8 > len(data):
                raise ValueError("truncated uint64 in binary VDF")
            value = struct.unpack_from("<Q", data, position)[0]
            position += 8
        else:
            raise ValueError(f"unsupported binary VDF field type: {value_type}")
        result[key] = value
    return result, position


def _read_c_string(data: bytes, position: int) -> tuple[str, int]:
    end = data.find(b"\x00", position)
    if end == -1:
        raise ValueError("unterminated string in binary VDF")
    return data[position:end].decode("utf-8", errors="replace"), end + 1
