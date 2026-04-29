from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import os
import urllib.error
import urllib.request

from .parsers import parse_shortcuts_vdf, parse_vdf_file


@dataclass(frozen=True)
class PrefixEntry:
    prefix_id: str
    name: str
    source: str
    compatdata_path: Path
    drive_c_path: Path
    library_path: Path
    proton_version: str
    last_modified: datetime
    resolved: bool


@dataclass(frozen=True)
class SteamInstallation:
    label: str
    kind: str
    root: Path


@dataclass(frozen=True)
class LibraryAccessIssue:
    path: Path
    steam_root: Path


@dataclass(frozen=True)
class LibraryDiscovery:
    accessible: list[Path]
    inaccessible: list[LibraryAccessIssue]


@dataclass(frozen=True)
class ScanResult:
    entries: list[PrefixEntry]
    inaccessible_libraries: list[LibraryAccessIssue]


def default_steam_root() -> Path:
    return Path.home() / ".local/share/Steam"


def discover_steam_installations() -> list[SteamInstallation]:
    home = Path.home()
    candidates = [
        SteamInstallation("Native Steam", "native", home / ".local/share/Steam"),
        SteamInstallation("Native Steam", "native", home / ".steam/steam"),
        SteamInstallation("Flatpak Steam", "flatpak", home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"),
        SteamInstallation("Snap Steam", "snap", home / "snap/steam/common/.local/share/Steam"),
        SteamInstallation("Snap Steam", "snap", home / "snap/steam/common/.steam/steam"),
    ]

    seen_roots: set[Path] = set()
    seen_kinds: set[str] = set()
    installations: list[SteamInstallation] = []
    for candidate in candidates:
        root = candidate.root.expanduser()
        if not root.exists():
            continue
        real_root = root.resolve()
        if real_root in seen_roots or candidate.kind in seen_kinds:
            continue
        seen_roots.add(real_root)
        seen_kinds.add(candidate.kind)
        installations.append(SteamInstallation(candidate.label, candidate.kind, root))
    return installations


def discover_steam_roots() -> list[Path]:
    return [installation.root for installation in discover_steam_installations()]


def discover_library_paths(steam_root: Path | None = None) -> list[Path]:
    return discover_library_access(steam_root).accessible


def discover_library_access(
    steam_root: Path | None = None,
    granted_libraries: dict[Path, Path] | None = None,
) -> LibraryDiscovery:
    roots = [steam_root.expanduser()] if steam_root else discover_steam_roots()
    grants = {_path_key(original): granted.expanduser() for original, granted in (granted_libraries or {}).items()}
    accessible: list[Path] = []
    inaccessible: list[LibraryAccessIssue] = []
    for root in roots:
        for library in _discover_library_paths_for_root(root):
            grant = grants.get(_path_key(library))
            if _library_accessible(library):
                accessible.append(library)
            elif grant and _library_accessible(grant):
                accessible.append(grant)
            else:
                inaccessible.append(LibraryAccessIssue(library, root))
    return LibraryDiscovery(_dedupe_accessible(accessible), _dedupe_issues(inaccessible))


def _discover_library_paths_for_root(root: Path) -> list[Path]:
    libraryfolders = root / "steamapps/libraryfolders.vdf"
    paths: list[Path] = []
    if libraryfolders.exists():
        data = parse_vdf_file(libraryfolders)
        folders = data.get("libraryfolders", {})
        if isinstance(folders, dict):
            for key in sorted(folders, key=_natural_key):
                value = folders[key]
                if isinstance(value, dict) and isinstance(value.get("path"), str):
                    paths.append(Path(value["path"]).expanduser())
    if root not in paths:
        paths.insert(0, root)
    return paths


def scan_prefixes(
    steam_root: Path | None = None,
    compatdata_path: Path | None = None,
) -> list[PrefixEntry]:
    return scan_prefixes_with_access(steam_root, compatdata_path).entries


def scan_prefixes_with_access(
    steam_root: Path | None = None,
    compatdata_path: Path | None = None,
    granted_libraries: dict[Path, Path] | None = None,
) -> ScanResult:
    roots = [steam_root.expanduser()] if steam_root else discover_steam_roots()
    discovery = LibraryDiscovery([compatdata_path.parent.parent], []) if compatdata_path else discover_library_access(steam_root, granted_libraries)
    libraries = discovery.accessible
    manifest_names = _load_appmanifest_names(libraries)
    shortcut_names = _load_shortcut_names(roots)

    entries: list[PrefixEntry] = []
    compat_dirs = [compatdata_path] if compatdata_path else [
        library / "steamapps" / "compatdata" for library in libraries
    ]
    seen_prefix_paths: set[Path] = set()
    for compat_dir in compat_dirs:
        if not compat_dir.exists():
            continue
        library = compat_dir.parent.parent
        for prefix in sorted((path for path in compat_dir.iterdir() if path.is_dir()), key=lambda p: _natural_key(p.name)):
            real_prefix = prefix.resolve()
            if real_prefix in seen_prefix_paths:
                continue
            seen_prefix_paths.add(real_prefix)
            prefix_id = prefix.name
            name, source, resolved = _resolve_local(prefix_id, manifest_names, shortcut_names)
            entries.append(
                PrefixEntry(
                    prefix_id=prefix_id,
                    name=name,
                    source=source,
                    compatdata_path=prefix,
                    drive_c_path=prefix / "pfx" / "drive_c",
                    library_path=library,
                    proton_version=_read_first_line(prefix / "version"),
                    last_modified=datetime.fromtimestamp(prefix.stat().st_mtime),
                    resolved=resolved,
                )
            )
    return ScanResult(entries, discovery.inaccessible)


def lookup_unresolved_online(entries: list[PrefixEntry]) -> list[PrefixEntry]:
    updated: list[PrefixEntry] = []
    for entry in entries:
        if entry.resolved or not entry.prefix_id.isdigit() or entry.prefix_id == "0":
            updated.append(entry)
            continue
        name = lookup_steam_app_name(entry.prefix_id)
        if name:
            updated.append(replace(entry, name=name, source="Steam web", resolved=True))
        else:
            updated.append(entry)
    return updated


def lookup_steam_app_name(appid: str, timeout: float = 5.0) -> str | None:
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=basic"
    request = urllib.request.Request(url, headers={"User-Agent": "Steam-Identifier/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError):
        return None

    # The appdetails response is small for one app; avoid a dependency for a loose fallback.
    import json

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    app = data.get(appid, {})
    if not isinstance(app, dict) or not app.get("success"):
        return None
    details = app.get("data", {})
    name = details.get("name") if isinstance(details, dict) else None
    return name if isinstance(name, str) and name else None


def _load_appmanifest_names(libraries: list[Path]) -> dict[str, str]:
    names: dict[str, str] = {}
    for library in libraries:
        steamapps = library / "steamapps"
        for manifest in steamapps.glob("appmanifest_*.acf"):
            try:
                data = parse_vdf_file(manifest)
            except OSError:
                continue
            app_state = data.get("AppState", {})
            if not isinstance(app_state, dict):
                continue
            appid = app_state.get("appid")
            name = app_state.get("name")
            if isinstance(appid, str) and isinstance(name, str):
                names[appid] = name
    return names


def _load_shortcut_names(steam_roots: list[Path]) -> dict[str, str]:
    names: dict[str, str] = {}
    for steam_root in steam_roots:
        userdata = steam_root / "userdata"
        if not userdata.exists():
            continue
        for shortcut_file in userdata.glob("*/config/shortcuts.vdf"):
            try:
                shortcuts = parse_shortcuts_vdf(shortcut_file)
            except (OSError, ValueError):
                continue
            for shortcut in shortcuts:
                names[shortcut.appid] = shortcut.appname
    return names


def _resolve_local(
    prefix_id: str,
    manifest_names: dict[str, str],
    shortcut_names: dict[str, str],
) -> tuple[str, str, bool]:
    if prefix_id in manifest_names:
        return manifest_names[prefix_id], "appmanifest", True
    if prefix_id in shortcut_names:
        return shortcut_names[prefix_id], "shortcut", True
    if prefix_id == "0":
        return "Unknown Steam/Proton prefix", "unresolved", False
    return f"Unknown app {prefix_id}", "unresolved", False


def _read_first_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return ""


def _library_accessible(path: Path) -> bool:
    try:
        return (path.expanduser() / "steamapps").is_dir()
    except OSError:
        return False


def _dedupe_accessible(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        if not _library_accessible(expanded):
            continue
        key = expanded.resolve()
        if key in seen:
            continue
        seen.add(key)
        result.append(expanded)
    return result


def _dedupe_issues(issues: list[LibraryAccessIssue]) -> list[LibraryAccessIssue]:
    seen: set[str] = set()
    result: list[LibraryAccessIssue] = []
    for issue in issues:
        key = _path_key(issue.path)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _path_key(path: Path) -> str:
    return str(path.expanduser())


def _natural_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value.lower())
