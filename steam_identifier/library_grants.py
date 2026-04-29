from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class LibraryGrant:
    original_path: str
    granted_path: str
    granted_uri: str


class LibraryGrantStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_library_grant_path()
        self._grants: dict[str, LibraryGrant] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._grants = {}
            return

        values = data.get("grants") if isinstance(data, dict) else None
        if not isinstance(values, list):
            self._grants = {}
            return

        grants: dict[str, LibraryGrant] = {}
        for value in values:
            if not isinstance(value, dict):
                continue
            original_path = value.get("original_path")
            granted_path = value.get("granted_path")
            granted_uri = value.get("granted_uri")
            if isinstance(original_path, str) and isinstance(granted_path, str) and isinstance(granted_uri, str):
                grants[original_path] = LibraryGrant(original_path, granted_path, granted_uri)
        self._grants = grants

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "grants": [
                {
                    "original_path": grant.original_path,
                    "granted_path": grant.granted_path,
                    "granted_uri": grant.granted_uri,
                }
                for grant in sorted(self._grants.values(), key=lambda item: item.original_path)
            ]
        }
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def list(self) -> list[LibraryGrant]:
        return list(self._grants.values())

    def add(self, original_path: Path, granted_path: Path, granted_uri: str) -> LibraryGrant:
        grant = LibraryGrant(
            original_path=str(original_path.expanduser()),
            granted_path=str(granted_path.expanduser()),
            granted_uri=granted_uri,
        )
        self._grants[grant.original_path] = grant
        self.save()
        return grant

    def mapping(self) -> dict[Path, Path]:
        return {
            Path(grant.original_path): Path(grant.granted_path)
            for grant in self._grants.values()
        }


def default_library_grant_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser() / "steam-identifier/libraries.json"
    return Path.home() / ".config/steam-identifier/libraries.json"
