# Steam Compatdata Identifier

Native Linux GUI for identifying Steam Proton `compatdata` folders.

## Run

```bash
python3 steam_identifier.py
```

The app auto-detects native, Flatpak, and Snap Steam roots, scans each root's `steamapps/libraryfolders.vdf`, resolves names from installed app manifests and non-Steam shortcuts, then lists every prefix. The row action opens `pfx/drive_c` in the file manager.

Each row also has a Bookmarks action. Bookmarks are stored per Steam app/prefix ID and must point inside that prefix. They are saved at:

```text
${XDG_CONFIG_HOME:-~/.config}/steam-identifier/bookmarks.json
```

Optional flags:

```bash
python3 steam_identifier.py --no-online
python3 steam_identifier.py --steam-root ~/.local/share/Steam
python3 steam_identifier.py --compatdata /path/to/steam-library/steamapps/compatdata
```

The tool is read-only. It does not rename or modify Steam folders.

## Flatpak

Build and install locally:

```bash
flatpak-builder --user --install --force-clean build-dir packaging/flatpak/io.github.xrishox.SteamIdentifier.yml
flatpak run io.github.xrishox.SteamIdentifier
```

The Flatpak has read-only access to normal native, Flatpak, and Snap Steam locations. If Steam's `libraryfolders.vdf` points to a library on another drive that the sandbox cannot read, the app shows a `Grant` action. Use it to select that Steam library folder through the portal; the grant is stored at `${XDG_CONFIG_HOME:-~/.config}/steam-identifier/libraries.json` and reused on future scans.

Detected roots include:

- `~/.local/share/Steam`
- `~/.steam/steam`
- `~/.var/app/com.valvesoftware.Steam/.local/share/Steam`
- `~/snap/steam/common/.local/share/Steam`
- `~/snap/steam/common/.steam/steam`
