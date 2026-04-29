from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from steam_identifier.library_grants import LibraryGrantStore


class LibraryGrantTests(unittest.TestCase):
    def test_persists_granted_libraries(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config = Path(tmpdir) / "libraries.json"
            store = LibraryGrantStore(config)
            store.add(Path("/mnt/games/SteamLibrary"), Path("/run/flatpak/doc/abc/SteamLibrary"), "file:///run/flatpak/doc/abc/SteamLibrary")

            reloaded = LibraryGrantStore(config)

        grants = reloaded.list()
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].original_path, "/mnt/games/SteamLibrary")
        self.assertEqual(grants[0].granted_path, "/run/flatpak/doc/abc/SteamLibrary")

    def test_mapping_returns_paths(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = LibraryGrantStore(Path(tmpdir) / "libraries.json")
            store.add(Path("/mnt/games/SteamLibrary"), Path("/run/flatpak/doc/abc/SteamLibrary"), "file:///run/flatpak/doc/abc/SteamLibrary")

            mapping = store.mapping()

        self.assertEqual(mapping, {Path("/mnt/games/SteamLibrary"): Path("/run/flatpak/doc/abc/SteamLibrary")})


if __name__ == "__main__":
    unittest.main()
