from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Bookmark:
    label: str
    relative_path: str


class BookmarkStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_bookmark_path()
        self._bookmarks: dict[str, list[Bookmark]] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._bookmarks = {}
            return

        bookmarks: dict[str, list[Bookmark]] = {}
        if not isinstance(data, dict):
            self._bookmarks = {}
            return
        for prefix_id, values in data.items():
            if not isinstance(prefix_id, str) or not isinstance(values, list):
                continue
            parsed: list[Bookmark] = []
            for value in values:
                if not isinstance(value, dict):
                    continue
                label = value.get("label")
                relative_path = value.get("relative_path")
                if isinstance(label, str) and isinstance(relative_path, str):
                    parsed.append(Bookmark(label, relative_path))
            bookmarks[prefix_id] = parsed
        self._bookmarks = bookmarks

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, list[dict[str, str]]] = {
            prefix_id: [
                {"label": bookmark.label, "relative_path": bookmark.relative_path}
                for bookmark in bookmarks
            ]
            for prefix_id, bookmarks in sorted(self._bookmarks.items())
        }
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def list(self, prefix_id: str) -> list[Bookmark]:
        return list(self._bookmarks.get(prefix_id, []))

    def add(self, prefix_id: str, label: str, selected_path: Path, compatdata_path: Path) -> Bookmark:
        label = label.strip()
        if not label:
            label = selected_path.name or "Bookmark"
        relative_path = path_relative_to_prefix(selected_path, compatdata_path)
        bookmark = Bookmark(label=label, relative_path=relative_path)

        current = [
            existing for existing in self._bookmarks.get(prefix_id, [])
            if existing.relative_path != bookmark.relative_path
        ]
        current.append(bookmark)
        self._bookmarks[prefix_id] = current
        self.save()
        return bookmark

    def remove(self, prefix_id: str, relative_path: str) -> None:
        current = [
            bookmark for bookmark in self._bookmarks.get(prefix_id, [])
            if bookmark.relative_path != relative_path
        ]
        if current:
            self._bookmarks[prefix_id] = current
        else:
            self._bookmarks.pop(prefix_id, None)
        self.save()


def default_bookmark_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser() / "steam-identifier/bookmarks.json"
    return Path.home() / ".config/steam-identifier/bookmarks.json"


def path_relative_to_prefix(selected_path: Path, compatdata_path: Path) -> str:
    selected = selected_path.expanduser().resolve()
    prefix = compatdata_path.expanduser().resolve()
    try:
        relative = selected.relative_to(prefix)
    except ValueError as exc:
        raise ValueError(f"Bookmark path must be inside prefix: {prefix}") from exc
    return relative.as_posix() or "."


def resolve_bookmark_path(bookmark: Bookmark, compatdata_path: Path) -> Path:
    return compatdata_path / Path(bookmark.relative_path)
