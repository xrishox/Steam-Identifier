from __future__ import annotations

from datetime import datetime
import inspect
from pathlib import Path
import unittest

from steam_identifier.app import (
    DEFAULT_WINDOW_WIDTH,
    FIXED_COLUMN_WIDTHS,
    PrefixObject,
    SteamIdentifierWindow,
    bookmark_browse_start_path,
    folder_picker_command,
    picker_debug_message,
    prefix_matches_filters,
)
from steam_identifier.scanner import PrefixEntry


class SorterCallbackTests(unittest.TestCase):
    def test_sorter_lambda_ignores_gtk_user_data(self) -> None:
        left = PrefixObject(_entry("2", "B"))
        right = PrefixObject(_entry("10", "A"))

        sorter = lambda a, b, _user_data=None, _attr="prefix_id": _compare_for_test(a, b, _attr)

        self.assertLess(sorter(left, right, None), 0)

    def test_button_bind_handlers_accept_gtk_factory_and_item(self) -> None:
        open_signature = inspect.signature(SteamIdentifierWindow._bind_open_button)
        bookmark_signature = inspect.signature(SteamIdentifierWindow._bind_bookmark_button)

        self.assertEqual(list(open_signature.parameters), ["self", "_factory", "item"])
        self.assertEqual(list(bookmark_signature.parameters), ["self", "_factory", "item"])

    def test_default_window_width_fits_action_columns(self) -> None:
        fixed_width = sum(FIXED_COLUMN_WIDTHS.values())
        estimated_flexible_width = 260 + 300
        gutter = 120

        self.assertGreaterEqual(DEFAULT_WINDOW_WIDTH, fixed_width + estimated_flexible_width + gutter)

    def test_bookmarked_filter_requires_bookmarks(self) -> None:
        obj = PrefixObject(_entry("123", "Game"))

        self.assertTrue(prefix_matches_filters(obj, "", True, True))
        self.assertFalse(prefix_matches_filters(obj, "", True, False))

    def test_bookmarked_filter_combines_with_search(self) -> None:
        obj = PrefixObject(_entry("123", "Game"))

        self.assertTrue(prefix_matches_filters(obj, "game", True, True))
        self.assertFalse(prefix_matches_filters(obj, "missing", True, True))

    def test_bookmark_browse_starts_at_drive_c_when_entry_path_is_missing(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            compatdata = root / "compatdata/123"
            drive_c = compatdata / "pfx/drive_c"
            drive_c.mkdir(parents=True)

            start = bookmark_browse_start_path(str(root / "missing"), drive_c, compatdata)

        self.assertEqual(start, drive_c)

    def test_bookmark_browse_uses_current_entry_when_it_exists(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            compatdata = root / "compatdata/123"
            drive_c = compatdata / "pfx/drive_c"
            addons = drive_c / "addons"
            addons.mkdir(parents=True)

            start = bookmark_browse_start_path(str(addons), drive_c, compatdata)

        self.assertEqual(start, addons)

    def test_folder_picker_prefers_kdialog_on_kde(self) -> None:
        from unittest.mock import patch

        with patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "KDE"}), patch(
            "steam_identifier.app.shutil.which",
            side_effect=lambda command: f"/usr/bin/{command}" if command == "kdialog" else None,
        ):
            command = folder_picker_command(Path("/tmp/prefix/pfx/drive_c"))

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(
            command.command,
            ["kdialog", "--title", "Choose Bookmark Folder", "--getexistingdirectory", "file:///tmp/prefix/pfx/drive_c"],
        )
        self.assertEqual(command.cwd, Path("/tmp/prefix/pfx/drive_c"))

    def test_folder_picker_uses_zenity_when_kdialog_is_unavailable(self) -> None:
        from unittest.mock import patch

        with patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "GNOME"}), patch(
            "steam_identifier.app.shutil.which",
            side_effect=lambda command: f"/usr/bin/{command}" if command == "zenity" else None,
        ):
            command = folder_picker_command(Path("/tmp/prefix/pfx/drive_c"))

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.command, ["zenity", "--file-selection", "--directory", "--filename=/tmp/prefix/pfx/drive_c/"])
        self.assertEqual(command.cwd, Path("/tmp/prefix/pfx/drive_c"))

    def test_folder_picker_uses_zenity_on_xfce(self) -> None:
        from unittest.mock import patch

        with patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "XFCE"}), patch(
            "steam_identifier.app.shutil.which",
            side_effect=lambda command: f"/usr/bin/{command}" if command == "zenity" else None,
        ):
            command = folder_picker_command(Path("/tmp/prefix/pfx/drive_c"))

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.command, ["zenity", "--file-selection", "--directory", "--filename=/tmp/prefix/pfx/drive_c/"])
        self.assertEqual(command.cwd, Path("/tmp/prefix/pfx/drive_c"))

    def test_folder_picker_falls_back_to_kdialog_when_zenity_is_missing(self) -> None:
        from unittest.mock import patch

        with patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "XFCE"}), patch(
            "steam_identifier.app.shutil.which",
            side_effect=lambda command: f"/usr/bin/{command}" if command == "kdialog" else None,
        ):
            command = folder_picker_command(Path("/tmp/prefix/pfx/drive_c"))

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(
            command.command,
            ["kdialog", "--title", "Choose Bookmark Folder", "--getexistingdirectory", "file:///tmp/prefix/pfx/drive_c"],
        )
        self.assertEqual(command.cwd, Path("/tmp/prefix/pfx/drive_c"))

    def test_flatpak_folder_picker_uses_internal_browser(self) -> None:
        from unittest.mock import patch

        with patch.dict("os.environ", {"FLATPAK_ID": "io.github.xrishox.SteamIdentifier", "XDG_CURRENT_DESKTOP": "KDE"}), patch(
            "steam_identifier.app.shutil.which",
            return_value="/usr/bin/flatpak-spawn",
        ):
            command = folder_picker_command(Path("/tmp/prefix/pfx/drive_c"))

        self.assertIsNone(command)

    def test_picker_debug_message_includes_command_and_start_path(self) -> None:
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)
            with patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "KDE"}), patch(
                "steam_identifier.app.shutil.which",
                side_effect=lambda command: f"/usr/bin/{command}" if command == "kdialog" else None,
            ):
                command = folder_picker_command(path)
                message = picker_debug_message(path, command)

        self.assertIn("desktop='KDE'", message)
        self.assertIn("kdialog", message)
        self.assertIn("file://", message)


def _entry(prefix_id: str, name: str) -> PrefixEntry:
    return PrefixEntry(
        prefix_id=prefix_id,
        name=name,
        source="test",
        compatdata_path=Path("/tmp") / prefix_id,
        drive_c_path=Path("/tmp") / prefix_id / "pfx/drive_c",
        library_path=Path("/tmp"),
        proton_version="",
        last_modified=datetime.fromtimestamp(0),
        resolved=True,
    )


def _compare_for_test(left: PrefixObject, right: PrefixObject, attr: str) -> int:
    a = getattr(left, attr)
    b = getattr(right, attr)
    if attr == "prefix_id" and a.isdigit() and b.isdigit():
        return (int(a) > int(b)) - (int(a) < int(b))
    return (a.lower() > b.lower()) - (a.lower() < b.lower())


if __name__ == "__main__":
    unittest.main()
