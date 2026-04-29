from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from steam_identifier.bookmarks import BookmarkStore, path_relative_to_prefix, resolve_bookmark_path


class BookmarkTests(unittest.TestCase):
    def test_stores_paths_relative_to_prefix(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prefix = root / "compatdata/123"
            target = prefix / "pfx/drive_c/addons"
            target.mkdir(parents=True)

            relative = path_relative_to_prefix(target, prefix)

        self.assertEqual(relative, "pfx/drive_c/addons")

    def test_rejects_paths_outside_prefix(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prefix = root / "compatdata/123"
            outside = root / "elsewhere"
            prefix.mkdir(parents=True)
            outside.mkdir()

            with self.assertRaises(ValueError):
                path_relative_to_prefix(outside, prefix)

    def test_persists_bookmarks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "bookmarks.json"
            prefix = root / "compatdata/123"
            target = prefix / "pfx/drive_c/addons"
            target.mkdir(parents=True)

            store = BookmarkStore(config)
            bookmark = store.add("123", "Addons", target, prefix)
            reloaded = BookmarkStore(config)

            self.assertEqual(reloaded.list("123"), [bookmark])
            self.assertEqual(resolve_bookmark_path(bookmark, prefix), target)


if __name__ == "__main__":
    unittest.main()
