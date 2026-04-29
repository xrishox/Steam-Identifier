from __future__ import annotations

import struct
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from steam_identifier.scanner import discover_steam_installations, discover_steam_roots, scan_prefixes


class ScannerTests(unittest.TestCase):
    def test_scan_prefixes_resolves_manifests_and_shortcuts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            home = tmp_path / "home"
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                steam = home / ".local/share/Steam"
                library = tmp_path / "steam-library"
                (steam / "steamapps").mkdir(parents=True)
                (steam / "userdata/test-user/config").mkdir(parents=True)
                (library / "steamapps/compatdata/123456/pfx/drive_c").mkdir(parents=True)
                (library / "steamapps/compatdata/2535346416/pfx/drive_c").mkdir(parents=True)
                (library / "steamapps/compatdata/0/pfx/drive_c").mkdir(parents=True)
                (library / "steamapps/compatdata/123456/version").write_text("GE-Proton\n", encoding="utf-8")

                (steam / "steamapps/libraryfolders.vdf").write_text(
                    f'"libraryfolders" {{ "0" {{ "path" "{steam}" }} "1" {{ "path" "{library}" }} }}',
                    encoding="utf-8",
                )
                (library / "steamapps/appmanifest_123456.acf").write_text(
                    '"AppState" { "appid" "123456" "name" "Example Game" }',
                    encoding="utf-8",
                )
                _write_shortcuts(steam / "userdata/test-user/config/shortcuts.vdf")

                entries = {entry.prefix_id: entry for entry in scan_prefixes()}
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

        self.assertEqual(entries["123456"].name, "Example Game")
        self.assertEqual(entries["123456"].source, "appmanifest")
        self.assertEqual(entries["123456"].proton_version, "GE-Proton")
        self.assertEqual(entries["2535346416"].name, "CustomGame.exe")
        self.assertEqual(entries["2535346416"].source, "shortcut")
        self.assertFalse(entries["0"].resolved)

    def test_discovers_native_flatpak_and_snap_roots(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            home = tmp_path / "home"
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                native = home / ".local/share/Steam"
                flatpak = home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
                snap = home / "snap/steam/common/.local/share/Steam"
                native.mkdir(parents=True)
                flatpak.mkdir(parents=True)
                snap.mkdir(parents=True)

                installations = discover_steam_installations()
                roots = discover_steam_roots()
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

        self.assertEqual([installation.kind for installation in installations], ["native", "flatpak", "snap"])
        self.assertEqual({str(path) for path in roots}, {str(native), str(flatpak), str(snap)})

    def test_install_detection_collapses_duplicate_native_aliases(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            home = tmp_path / "home"
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                native = home / ".local/share/Steam"
                alias_parent = home / ".steam"
                native.mkdir(parents=True)
                alias_parent.mkdir(parents=True)
                (alias_parent / "steam").symlink_to(native, target_is_directory=True)

                installations = discover_steam_installations()
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

        self.assertEqual(len(installations), 1)
        self.assertEqual(installations[0].kind, "native")


def _write_shortcuts(path: Path) -> None:
    path.write_bytes(
        b"\x00shortcuts\x00"
        b"\x000\x00"
        b"\x02appid\x00"
        + struct.pack("<i", -1759620880)
        + b"\x01appname\x00CustomGame.exe\x00"
        + b"\x01exe\x00/tmp/example-game/CustomGame.exe\x00"
        + b"\x01StartDir\x00/tmp/example-game/\x00"
        + b"\x08"
        + b"\x08"
        + b"\x08"
    )


if __name__ == "__main__":
    unittest.main()
