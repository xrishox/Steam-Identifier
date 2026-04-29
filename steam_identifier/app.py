from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk  # noqa: E402

from .bookmarks import Bookmark, BookmarkStore, resolve_bookmark_path
from .library_grants import LibraryGrantStore
from .scanner import (
    LibraryAccessIssue,
    PrefixEntry,
    ScanResult,
    SteamInstallation,
    discover_steam_installations,
    lookup_unresolved_online,
    scan_prefixes_with_access,
)


DEFAULT_WINDOW_WIDTH = 1440
DEFAULT_WINDOW_HEIGHT = 760
FIXED_COLUMN_WIDTHS = {
    "prefix_id": 100,
    "source": 120,
    "proton": 130,
    "modified": 150,
    "bookmarks": 150,
    "open": 96,
}


@dataclass(frozen=True)
class PickerCommand:
    command: list[str]
    cwd: Path


class PrefixObject(GObject.Object):
    prefix_id = GObject.Property(type=str)
    name = GObject.Property(type=str)
    source = GObject.Property(type=str)
    library = GObject.Property(type=str)
    proton = GObject.Property(type=str)
    modified = GObject.Property(type=str)
    compatdata_path = GObject.Property(type=str)
    drive_c_path = GObject.Property(type=str)
    resolved = GObject.Property(type=bool, default=False)

    def __init__(self, entry: PrefixEntry):
        super().__init__(
            prefix_id=entry.prefix_id,
            name=entry.name,
            source=entry.source,
            library=str(entry.library_path),
            proton=entry.proton_version,
            modified=entry.last_modified.strftime("%Y-%m-%d %H:%M"),
            compatdata_path=str(entry.compatdata_path),
            drive_c_path=str(entry.drive_c_path),
            resolved=entry.resolved,
        )


class SteamIdentifierWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, args: argparse.Namespace):
        super().__init__(application=app, title="Steam Compatdata Identifier")
        self.set_default_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        self.args = args
        self.entries: list[PrefixEntry] = []
        self.store = Gio.ListStore(item_type=PrefixObject)
        self.filter_text = ""
        self.bookmarked_only = False
        self.installations: list[SteamInstallation] = []
        self.inaccessible_libraries: list[LibraryAccessIssue] = []
        self.library_access_window: LibraryAccessWindow | None = None
        self.active_library_dialog: Gtk.FileDialog | None = None
        self.active_library_issue: LibraryAccessIssue | None = None
        self.selected_steam_root: Path | None = Path(args.steam_root).expanduser() if args.steam_root else None
        self.bookmarks = BookmarkStore()
        self.library_grants = LibraryGrantStore()

        self._build_ui()
        self.scan()

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(root)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_top(10)
        toolbar.set_margin_bottom(10)
        toolbar.set_margin_start(10)
        toolbar.set_margin_end(10)
        root.append(toolbar)

        self.search = Gtk.SearchEntry()
        self.search.set_hexpand(True)
        self.search.set_placeholder_text("Search by name, ID, source, or path")
        self.search.connect("search-changed", self._on_search_changed)
        toolbar.append(self.search)

        self.source_dropdown = Gtk.DropDown()
        self.source_dropdown.set_tooltip_text("Steam installation source")
        self._load_source_dropdown()
        self.source_dropdown.connect("notify::selected", self._on_source_changed)
        toolbar.append(self.source_dropdown)

        self.bookmarked_only_button = Gtk.ToggleButton(label="Bookmarked")
        self.bookmarked_only_button.set_tooltip_text("Show prefixes with bookmarks only")
        self.bookmarked_only_button.connect("toggled", self._on_bookmarked_only_toggled)
        toolbar.append(self.bookmarked_only_button)

        self.grant_button = Gtk.Button(label="Grant")
        self.grant_button.set_sensitive(False)
        self.grant_button.set_tooltip_text("Grant access to Steam libraries on other drives")
        self.grant_button.connect("clicked", self._on_grant_clicked)
        toolbar.append(self.grant_button)

        scan_button = Gtk.Button(label="Scan")
        scan_button.connect("clicked", lambda *_: self.scan())
        toolbar.append(scan_button)

        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("dim-label")
        self.status.set_margin_start(10)
        root.append(self.status)

        self.filter = Gtk.CustomFilter.new(self._filter_row)
        self.filter_model = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.sort_model = Gtk.SortListModel(model=self.filter_model)
        selection = Gtk.SingleSelection(model=self.sort_model)
        self.view = Gtk.ColumnView(model=selection)
        self.sort_model.set_sorter(self.view.get_sorter())
        self.view.set_vexpand(True)
        self.view.set_show_column_separators(True)
        self.view.set_show_row_separators(True)

        self._add_text_column("ID", "prefix_id", FIXED_COLUMN_WIDTHS["prefix_id"])
        self._add_text_column("Name", "name", 260, expand=True)
        self._add_text_column("Source", "source", FIXED_COLUMN_WIDTHS["source"])
        self._add_text_column("Proton", "proton", FIXED_COLUMN_WIDTHS["proton"])
        self._add_text_column("Modified", "modified", FIXED_COLUMN_WIDTHS["modified"])
        self._add_text_column("Library", "library", 300, expand=True)
        self._add_bookmarks_column()
        self._add_open_column()

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.view)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        root.append(scroller)

    def _load_source_dropdown(self) -> None:
        labels = ["All detected"]
        if self.args.steam_root:
            labels = [f"Custom: {Path(self.args.steam_root).expanduser()}"]
        elif self.args.compatdata:
            labels = [f"Compatdata: {Path(self.args.compatdata).expanduser()}"]
        else:
            self.installations = discover_steam_installations()
            labels.extend(f"{installation.label} ({installation.root})" for installation in self.installations)
            if len(labels) == 1:
                labels = ["No Steam installs found"]
        self.source_dropdown.set_model(Gtk.StringList.new(labels))
        self.source_dropdown.set_selected(0)
        self.source_dropdown.set_sensitive(not self.args.steam_root and not self.args.compatdata and bool(self.installations))

    def _add_text_column(self, title: str, attr: str, width: int, expand: bool = False) -> None:
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_label)
        factory.connect("bind", lambda _factory, item: self._bind_label(item, attr))
        column = Gtk.ColumnViewColumn(title=title, factory=factory)
        column.set_resizable(True)
        column.set_fixed_width(width)
        column.set_expand(expand)
        column.set_sorter(Gtk.CustomSorter.new(lambda a, b, _user_data=None, _attr=attr: self._compare(a, b, _attr)))
        self.view.append_column(column)

    def _add_open_column(self) -> None:
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_open_button)
        factory.connect("bind", self._bind_open_button)
        column = Gtk.ColumnViewColumn(title="Open", factory=factory)
        column.set_fixed_width(FIXED_COLUMN_WIDTHS["open"])
        self.view.append_column(column)

    def _add_bookmarks_column(self) -> None:
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_bookmark_button)
        factory.connect("bind", self._bind_bookmark_button)
        column = Gtk.ColumnViewColumn(title="Bookmarks", factory=factory)
        column.set_fixed_width(FIXED_COLUMN_WIDTHS["bookmarks"])
        self.view.append_column(column)

    def _setup_label(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        label = Gtk.Label(xalign=0)
        label.set_ellipsize(3)
        label.set_margin_start(8)
        label.set_margin_end(8)
        item.set_child(label)

    def _bind_label(self, item: Gtk.ListItem, attr: str) -> None:
        label = item.get_child()
        obj = item.get_item()
        if isinstance(label, Gtk.Label) and isinstance(obj, PrefixObject):
            value = getattr(obj, attr)
            label.set_text(value)
            label.set_tooltip_text(value)

    def _setup_open_button(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        button = Gtk.Button(label="Open")
        button.set_margin_top(4)
        button.set_margin_bottom(4)
        button.set_margin_start(8)
        button.set_margin_end(8)
        item.set_child(button)

    def _bind_open_button(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        button = item.get_child()
        obj = item.get_item()
        if isinstance(button, Gtk.Button) and isinstance(obj, PrefixObject):
            try:
                button.disconnect_by_func(self._on_open_clicked)
            except TypeError:
                pass
            button.connect("clicked", self._on_open_clicked, obj)
            button.set_tooltip_text(obj.drive_c_path)

    def _setup_bookmark_button(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        button = Gtk.Button(label="Bookmarks")
        button.set_margin_top(4)
        button.set_margin_bottom(4)
        button.set_margin_start(8)
        button.set_margin_end(8)
        button.set_hexpand(True)
        item.set_child(button)

    def _bind_bookmark_button(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        button = item.get_child()
        obj = item.get_item()
        if isinstance(button, Gtk.Button) and isinstance(obj, PrefixObject):
            try:
                button.disconnect_by_func(self._on_bookmarks_clicked)
            except TypeError:
                pass
            button.connect("clicked", self._on_bookmarks_clicked, obj)
            count = len(self.bookmarks.list(obj.prefix_id))
            button.set_label(f"Bookmarks ({count})" if count else "Bookmarks")
            button.set_tooltip_text(f"Manage bookmarks for {obj.name}")

    def scan(self) -> None:
        self.status.set_text("Scanning Steam libraries...")
        self.store.remove_all()
        thread = threading.Thread(target=self._scan_worker, daemon=True)
        thread.start()

    def _scan_worker(self) -> None:
        try:
            result = scan_prefixes_with_access(
                steam_root=self.selected_steam_root,
                compatdata_path=Path(self.args.compatdata).expanduser() if self.args.compatdata else None,
                granted_libraries=self.library_grants.mapping(),
            )
            GLib.idle_add(self._scan_finished, result, None)
        except Exception as exc:
            GLib.idle_add(self._scan_finished, ScanResult([], []), str(exc))

    def _scan_finished(self, result: ScanResult, error: str | None) -> bool:
        if error:
            self.status.set_text(f"Scan failed: {error}")
            return GLib.SOURCE_REMOVE
        entries = result.entries
        self.inaccessible_libraries = result.inaccessible_libraries
        self.entries = entries
        self._load_entries(entries)
        unresolved = [entry for entry in entries if not entry.resolved and entry.prefix_id.isdigit() and entry.prefix_id != "0"]
        self._update_grant_button()
        self.status.set_text(self._status_text(entries, self.inaccessible_libraries))
        if unresolved and not self.args.no_online and not self.inaccessible_libraries:
            self._prompt_online_lookup(len(unresolved))
        return GLib.SOURCE_REMOVE

    def _load_entries(self, entries: list[PrefixEntry]) -> None:
        self.store.remove_all()
        for entry in entries:
            self.store.append(PrefixObject(entry))

    def _prompt_online_lookup(self, count: int) -> None:
        dialog = Gtk.AlertDialog(
            message=f"{count} unresolved numeric app ID(s) found.",
            detail="Look them up using Steam's public store metadata?",
            buttons=["Not Now", "Look Up"],
            cancel_button=0,
            default_button=1,
        )
        dialog.choose(self, None, self._online_dialog_finished)

    def _online_dialog_finished(self, dialog: Gtk.AlertDialog, result: Gio.AsyncResult) -> None:
        try:
            choice = dialog.choose_finish(result)
        except GLib.Error:
            return
        if choice == 1:
            self.status.set_text("Looking up unresolved IDs...")
            threading.Thread(target=self._online_worker, daemon=True).start()

    def _online_worker(self) -> None:
        entries = lookup_unresolved_online(self.entries)
        GLib.idle_add(self._online_finished, entries)

    def _online_finished(self, entries: list[PrefixEntry]) -> bool:
        self.entries = entries
        self._load_entries(entries)
        self.status.set_text(self._status_text(entries, self.inaccessible_libraries))
        return GLib.SOURCE_REMOVE

    def _status_text(self, entries: list[PrefixEntry], inaccessible_libraries: list[LibraryAccessIssue]) -> str:
        resolved = sum(1 for entry in entries if entry.resolved)
        unresolved = len(entries) - resolved
        text = f"{len(entries)} prefixes, {resolved} resolved, {unresolved} unresolved"
        if inaccessible_libraries:
            text += f", {len(inaccessible_libraries)} library path(s) need access"
        return text

    def _on_search_changed(self, search: Gtk.SearchEntry) -> None:
        self.filter_text = search.get_text().lower()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def _on_bookmarked_only_toggled(self, button: Gtk.ToggleButton) -> None:
        self.bookmarked_only = button.get_active()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def _on_grant_clicked(self, _button: Gtk.Button) -> None:
        if self.inaccessible_libraries:
            self.show_library_access_window(self.inaccessible_libraries)

    def _on_source_changed(self, dropdown: Gtk.DropDown, _param: GObject.ParamSpec) -> None:
        if self.args.steam_root or self.args.compatdata or not self.installations:
            return
        selected = dropdown.get_selected()
        self.selected_steam_root = None if selected == 0 else self.installations[selected - 1].root
        self.scan()

    def _filter_row(self, obj: PrefixObject) -> bool:
        return prefix_matches_filters(
            obj,
            self.filter_text,
            self.bookmarked_only,
            bool(self.bookmarks.list(obj.prefix_id)),
        )

    def _on_open_clicked(self, _button: Gtk.Button, obj: PrefixObject) -> None:
        drive_c = Path(obj.drive_c_path)
        target = drive_c if drive_c.exists() else Path(obj.compatdata_path)
        if not drive_c.exists():
            self.status.set_text(f"`pfx/drive_c` missing for {obj.prefix_id}; opened prefix root instead.")
        open_path(target, lambda message: self.status.set_text(message))

    def _on_bookmarks_clicked(self, _button: Gtk.Button, obj: PrefixObject) -> None:
        BookmarkWindow(self, obj).present()

    def refresh_bookmark_buttons(self) -> None:
        self._load_entries(self.entries)

    def grant_library_access(self, issue: LibraryAccessIssue, selected_path: Path, selected_uri: str) -> str | None:
        if not (selected_path / "steamapps").is_dir():
            return f"Select the Steam library folder that contains steamapps: {issue.path}"
        self.library_grants.add(issue.path, selected_path, selected_uri)
        self.scan()
        return None

    def _update_grant_button(self) -> None:
        count = len(self.inaccessible_libraries)
        self.grant_button.set_sensitive(bool(count))
        self.grant_button.set_label(f"Grant ({count})" if count else "Grant")

    def show_library_access_window(self, issues: list[LibraryAccessIssue]) -> None:
        if self.library_access_window and self.library_access_window.is_visible():
            self.library_access_window.present()
            return
        self.library_access_window = LibraryAccessWindow(self, issues)
        self.library_access_window.connect("destroy", self._library_access_window_destroyed)
        self.library_access_window.present()

    def _library_access_window_destroyed(self, _window: Gtk.Window) -> None:
        self.library_access_window = None

    def start_library_grant(self, issue: LibraryAccessIssue) -> None:
        if self.library_access_window:
            self.library_access_window.close()
        GLib.idle_add(self._show_library_grant_dialog, issue)

    def _show_library_grant_dialog(self, issue: LibraryAccessIssue) -> bool:
        dialog = Gtk.FileDialog(title="Grant Steam Library Access", accept_label="Grant")
        self.active_library_dialog = dialog
        self.active_library_issue = issue
        dialog.select_folder(self, None, self._library_grant_dialog_finished)
        return GLib.SOURCE_REMOVE

    def _library_grant_dialog_finished(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        issue = self.active_library_issue
        try:
            if not issue:
                return
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            path = Path(folder.get_path()) if folder and folder.get_path() else None
            uri = folder.get_uri() if folder else ""
            if not path:
                self.status.set_text("Could not read selected folder path.")
                return
            error = self.grant_library_access(issue, path, uri)
            if error:
                self.status.set_text(error)
        finally:
            self.active_library_dialog = None
            self.active_library_issue = None

    def _compare(self, left: PrefixObject, right: PrefixObject, attr: str) -> int:
        a = getattr(left, attr)
        b = getattr(right, attr)
        if attr == "prefix_id" and a.isdigit() and b.isdigit():
            return (int(a) > int(b)) - (int(a) < int(b))
        return (a.lower() > b.lower()) - (a.lower() < b.lower())


def open_path(path: Path, report: Callable[[str], None]) -> None:
    try:
        Gio.AppInfo.launch_default_for_uri(path.resolve().as_uri())
        return
    except GLib.Error:
        pass
    for command in (["gio", "open", str(path)], ["xdg-open", str(path)]):
        try:
            subprocess.Popen(command)
            return
        except OSError:
            continue
    report(f"Could not find gio or xdg-open to open {path}")


def prefix_matches_filters(obj: PrefixObject, filter_text: str, bookmarked_only: bool, has_bookmarks: bool) -> bool:
    if bookmarked_only and not has_bookmarks:
        return False
    if not filter_text:
        return True
    haystack = " ".join(
        [obj.prefix_id, obj.name, obj.source, obj.library, obj.proton, obj.compatdata_path]
    ).lower()
    return filter_text in haystack


class LibraryAccessWindow(Gtk.Window):
    def __init__(self, parent: SteamIdentifierWindow, issues: list[LibraryAccessIssue]):
        super().__init__(title="Grant Library Access", transient_for=parent, modal=False)
        self.set_default_size(760, 320)
        self.set_destroy_with_parent(True)
        self.parent_window = parent
        self.issues = issues

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self.set_child(root)

        self.message = Gtk.Label(xalign=0)
        self.message.add_css_class("dim-label")
        self.message.set_text("Grant access by selecting the Steam library folder that contains steamapps.")
        root.append(self.message)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        for issue in issues:
            listbox.append(self._issue_row(issue))

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(listbox)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        root.append(scroller)

        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        root.append(close)

    def _issue_row(self, issue: LibraryAccessIssue) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        label = Gtk.Label(label=str(issue.path), xalign=0)
        label.set_ellipsize(3)
        label.set_hexpand(True)
        label.set_tooltip_text(str(issue.path))
        box.append(label)

        copy_button = Gtk.Button(label="Copy Path")
        copy_button.connect("clicked", self._on_copy_path_clicked, issue)
        box.append(copy_button)

        button = Gtk.Button(label="Grant")
        button.connect("clicked", self._on_grant_issue_clicked, issue)
        box.append(button)

        row.set_child(box)
        return row

    def _on_grant_issue_clicked(self, _button: Gtk.Button, issue: LibraryAccessIssue) -> None:
        self.parent_window.start_library_grant(issue)

    def _on_copy_path_clicked(self, _button: Gtk.Button, issue: LibraryAccessIssue) -> None:
        self.get_clipboard().set(str(issue.path))
        self.message.set_text("Path copied. Paste it into the folder picker location field if needed.")


class BookmarkWindow(Gtk.Window):
    def __init__(self, parent: SteamIdentifierWindow, obj: PrefixObject):
        super().__init__(title=f"Bookmarks - {obj.name}", transient_for=parent, modal=True)
        self.set_default_size(680, 420)
        self.parent_window = parent
        self.obj = obj
        self.compatdata_path = Path(obj.compatdata_path)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self.set_child(root)

        title = Gtk.Label(label=obj.name, xalign=0)
        title.add_css_class("title-3")
        root.append(title)

        path_label = Gtk.Label(label=obj.compatdata_path, xalign=0)
        path_label.set_ellipsize(3)
        path_label.add_css_class("dim-label")
        root.append(path_label)

        self.bookmark_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.append(self.bookmark_list)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        root.append(separator)

        form = Gtk.Grid(column_spacing=8, row_spacing=8)
        root.append(form)

        form.attach(Gtk.Label(label="Label", xalign=0), 0, 0, 1, 1)
        self.label_entry = Gtk.Entry()
        self.label_entry.set_placeholder_text("Addons")
        self.label_entry.set_hexpand(True)
        form.attach(self.label_entry, 1, 0, 2, 1)

        form.attach(Gtk.Label(label="Path", xalign=0), 0, 1, 1, 1)
        self.path_entry = Gtk.Entry()
        default_path = Path(obj.drive_c_path) if Path(obj.drive_c_path).exists() else self.compatdata_path
        self.path_entry.set_text(str(default_path))
        self.path_entry.set_hexpand(True)
        form.attach(self.path_entry, 1, 1, 1, 1)

        browse = Gtk.Button(label="Browse")
        browse.connect("clicked", self._on_browse_clicked)
        form.attach(browse, 2, 1, 1, 1)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.append(buttons)

        add = Gtk.Button(label="Add Bookmark")
        add.connect("clicked", self._on_add_clicked)
        buttons.append(add)

        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons.append(close)

        self.message = Gtk.Label(xalign=0)
        self.message.add_css_class("dim-label")
        root.append(self.message)

        self._reload_list()

    def _reload_list(self) -> None:
        while child := self.bookmark_list.get_first_child():
            self.bookmark_list.remove(child)

        bookmarks = self.parent_window.bookmarks.list(self.obj.prefix_id)
        if not bookmarks:
            self.bookmark_list.append(Gtk.Label(label="No bookmarks for this prefix yet.", xalign=0))
            return

        for bookmark in bookmarks:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label=f"{bookmark.label}  -  {bookmark.relative_path}", xalign=0)
            label.set_ellipsize(3)
            label.set_hexpand(True)
            row.append(label)

            open_button = Gtk.Button(label="Open")
            open_button.connect("clicked", self._on_open_bookmark_clicked, bookmark)
            row.append(open_button)

            remove_button = Gtk.Button(label="Remove")
            remove_button.connect("clicked", self._on_remove_bookmark_clicked, bookmark)
            row.append(remove_button)

            self.bookmark_list.append(row)

    def _on_add_clicked(self, _button: Gtk.Button) -> None:
        selected_path = Path(self.path_entry.get_text()).expanduser()
        try:
            bookmark = self.parent_window.bookmarks.add(
                self.obj.prefix_id,
                self.label_entry.get_text(),
                selected_path,
                self.compatdata_path,
            )
        except (OSError, ValueError) as exc:
            self.message.set_text(str(exc))
            return
        self.message.set_text(f"Added bookmark: {bookmark.label}")
        self.label_entry.set_text("")
        self._reload_list()
        self.parent_window.refresh_bookmark_buttons()

    def _on_open_bookmark_clicked(self, _button: Gtk.Button, bookmark: Bookmark) -> None:
        target = resolve_bookmark_path(bookmark, self.compatdata_path)
        if not target.exists():
            self.message.set_text(f"Bookmark path does not exist: {target}")
            return
        open_path(target, lambda message: self.message.set_text(message))

    def _on_remove_bookmark_clicked(self, _button: Gtk.Button, bookmark: Bookmark) -> None:
        self.parent_window.bookmarks.remove(self.obj.prefix_id, bookmark.relative_path)
        self.message.set_text(f"Removed bookmark: {bookmark.label}")
        self._reload_list()
        self.parent_window.refresh_bookmark_buttons()

    def _on_browse_clicked(self, _button: Gtk.Button) -> None:
        start_path = bookmark_browse_start_path(
            self.path_entry.get_text(),
            Path(self.obj.drive_c_path),
            self.compatdata_path,
        )

        if os.environ.get("FLATPAK_ID"):
            if self.parent_window.args.debug_picker:
                print(f"Picker debug: using internal Flatpak folder picker; start={start_path}; exists={start_path.exists()}", flush=True)
            PrefixFolderChooserWindow(self, start_path, self._set_bookmark_path).present()
            return

        external_picker = folder_picker_command(start_path)
        if self.parent_window.args.debug_picker:
            message = picker_debug_message(start_path, external_picker)
            print(message, flush=True)
        if external_picker:
            threading.Thread(target=self._external_browse_worker, args=(external_picker,), daemon=True).start()
            return

        dialog = Gtk.FileChooserNative(
            title="Choose Bookmark Folder",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            accept_label="Choose",
            cancel_label="Cancel",
        )
        dialog.set_current_folder(Gio.File.new_for_path(str(start_path)))
        dialog.connect("response", self._browse_finished)
        dialog.show()

    def _set_bookmark_path(self, path: Path) -> None:
        self.path_entry.set_text(str(path))

    def _external_browse_worker(self, picker: PickerCommand) -> None:
        try:
            result = subprocess.run(
                picker.command,
                check=False,
                text=True,
                capture_output=True,
                cwd=picker.cwd,
            )
        except OSError as exc:
            GLib.idle_add(self.message.set_text, str(exc))
            if self.parent_window.args.debug_picker:
                print(f"Picker debug: launch failed: {exc}", flush=True)
            return
        if self.parent_window.args.debug_picker:
            print(
                "Picker debug: "
                f"returncode={result.returncode}; stdout={result.stdout.strip()!r}; stderr={result.stderr.strip()!r}",
                flush=True,
            )
        selected = result.stdout.strip()
        if result.returncode == 0 and selected:
            if selected.startswith("file://"):
                selected = Gio.File.new_for_uri(selected).get_path() or selected
            GLib.idle_add(self.path_entry.set_text, selected)
        elif self.parent_window.args.debug_picker and result.stderr.strip():
            GLib.idle_add(self.message.set_text, result.stderr.strip())

    def _browse_finished(self, dialog: Gtk.FileChooserNative, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            folder = dialog.get_file()
            path = folder.get_path() if folder else None
            if path:
                self.path_entry.set_text(path)
        dialog.destroy()


class PrefixFolderChooserWindow(Gtk.Window):
    def __init__(self, parent: BookmarkWindow, start_path: Path, on_selected: Callable[[Path], None]):
        super().__init__(title="Choose Bookmark Folder", transient_for=parent, modal=True)
        self.set_default_size(820, 540)
        self.on_selected = on_selected
        self.current_path = start_path
        self.row_paths: dict[Gtk.ListBoxRow, Path] = {}

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self.set_child(root)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.append(controls)

        up = Gtk.Button(label="Up")
        up.connect("clicked", self._on_up_clicked)
        controls.append(up)

        self.path_entry = Gtk.Entry()
        self.path_entry.set_hexpand(True)
        self.path_entry.connect("activate", self._on_path_activated)
        controls.append(self.path_entry)

        go = Gtk.Button(label="Go")
        go.connect("clicked", self._on_go_clicked)
        controls.append(go)

        self.message = Gtk.Label(xalign=0)
        self.message.add_css_class("dim-label")
        root.append(self.message)

        self.listbox = Gtk.ListBox()
        self.listbox.set_activate_on_single_click(False)
        self.listbox.connect("row-activated", self._on_row_activated)

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.listbox)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        root.append(scroller)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.append(buttons)

        choose = Gtk.Button(label="Choose This Folder")
        choose.connect("clicked", self._on_choose_clicked)
        buttons.append(choose)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        buttons.append(cancel)

        self._load_path(start_path)

    def _load_path(self, path: Path) -> None:
        path = path.expanduser()
        if not path.exists() or not path.is_dir():
            self.message.set_text(f"Folder does not exist: {path}")
            return

        self.current_path = path
        self.path_entry.set_text(str(path))
        self.message.set_text("")
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)
        self.row_paths.clear()

        try:
            folders = sorted((child for child in path.iterdir() if child.is_dir()), key=lambda child: child.name.casefold())
        except OSError as exc:
            self.message.set_text(str(exc))
            return

        if not folders:
            self.listbox.append(Gtk.Label(label="No folders here.", xalign=0))
            return

        for folder in folders:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=folder.name, xalign=0)
            label.set_margin_top(8)
            label.set_margin_bottom(8)
            label.set_margin_start(8)
            label.set_margin_end(8)
            label.set_ellipsize(3)
            row.set_child(label)
            self.row_paths[row] = folder
            self.listbox.append(row)

    def _on_up_clicked(self, _button: Gtk.Button) -> None:
        parent = self.current_path.parent
        if parent != self.current_path:
            self._load_path(parent)

    def _on_path_activated(self, _entry: Gtk.Entry) -> None:
        self._load_path(Path(self.path_entry.get_text()))

    def _on_go_clicked(self, _button: Gtk.Button) -> None:
        self._load_path(Path(self.path_entry.get_text()))

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        folder = self.row_paths.get(row)
        if folder:
            self._load_path(folder)

    def _on_choose_clicked(self, _button: Gtk.Button) -> None:
        self.on_selected(self.current_path)
        self.close()


def bookmark_browse_start_path(path_text: str, drive_c_path: Path, compatdata_path: Path) -> Path:
    start_path = Path(path_text).expanduser()
    if start_path.exists():
        return start_path
    if drive_c_path.exists():
        return drive_c_path
    return compatdata_path


def folder_picker_command(start_path: Path) -> PickerCommand | None:
    if os.environ.get("FLATPAK_ID"):
        return None
    start_path = start_path.resolve()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in desktop and shutil.which("kdialog"):
        return PickerCommand(["kdialog", "--title", "Choose Bookmark Folder", "--getexistingdirectory", start_path.as_uri()], start_path)
    if "gnome" in desktop and shutil.which("zenity"):
        return PickerCommand(["zenity", "--file-selection", "--directory", f"--filename={start_path}/"], start_path)
    if any(name in desktop for name in ("xfce", "x-cinnamon", "cinnamon", "mate", "lxde", "lxqt")):
        if shutil.which("zenity"):
            return PickerCommand(["zenity", "--file-selection", "--directory", f"--filename={start_path}/"], start_path)
        if shutil.which("kdialog"):
            return PickerCommand(["kdialog", "--title", "Choose Bookmark Folder", "--getexistingdirectory", start_path.as_uri()], start_path)
    if shutil.which("zenity"):
        return PickerCommand(["zenity", "--file-selection", "--directory", f"--filename={start_path}/"], start_path)
    if shutil.which("kdialog"):
        return PickerCommand(["kdialog", "--title", "Choose Bookmark Folder", "--getexistingdirectory", start_path.as_uri()], start_path)
    return None


def picker_debug_message(start_path: Path, picker: PickerCommand | None) -> str:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if not picker:
        return f"Picker debug: desktop={desktop!r}; start={start_path}; external picker unavailable; using GTK fallback"
    return (
        "Picker debug: "
        f"desktop={desktop!r}; start={start_path}; cwd={picker.cwd}; "
        f"command={' '.join(picker.command)}"
    )


class SteamIdentifierApp(Gtk.Application):
    def __init__(self, args: argparse.Namespace):
        super().__init__(application_id="io.github.xrishox.SteamIdentifier")
        self.args = args

    def do_activate(self) -> None:
        window = SteamIdentifierWindow(self, self.args)
        window.present()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Identify Steam compatdata prefixes.")
    parser.add_argument("--steam-root", help="Steam root directory, default: ~/.local/share/Steam")
    parser.add_argument("--compatdata", help="Scan one compatdata directory instead of all libraries")
    parser.add_argument("--no-online", action="store_true", help="Do not prompt for online lookup")
    parser.add_argument("--debug-picker", action="store_true", help="Show folder picker diagnostics in bookmark dialogs")
    args = parser.parse_args(argv)
    if args.debug_picker:
        print(
            "Picker debug: "
            f"desktop={os.environ.get('XDG_CURRENT_DESKTOP', '')!r}; "
            f"kdialog={shutil.which('kdialog')!r}; zenity={shutil.which('zenity')!r}",
            flush=True,
        )
    app = SteamIdentifierApp(args)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
