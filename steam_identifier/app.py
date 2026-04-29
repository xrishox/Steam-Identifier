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
from .scanner import PrefixEntry, SteamInstallation, discover_steam_installations, lookup_unresolved_online, scan_prefixes


DEFAULT_WINDOW_WIDTH = 1440
DEFAULT_WINDOW_HEIGHT = 760
FIXED_COLUMN_WIDTHS = {
    "prefix_id": 100,
    "source": 120,
    "proton": 130,
    "modified": 150,
    "bookmarks": 130,
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
        self.installations: list[SteamInstallation] = []
        self.selected_steam_root: Path | None = Path(args.steam_root).expanduser() if args.steam_root else None
        self.bookmarks = BookmarkStore()

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
            entries = scan_prefixes(
                steam_root=self.selected_steam_root,
                compatdata_path=Path(self.args.compatdata).expanduser() if self.args.compatdata else None,
            )
            GLib.idle_add(self._scan_finished, entries, None)
        except Exception as exc:
            GLib.idle_add(self._scan_finished, [], str(exc))

    def _scan_finished(self, entries: list[PrefixEntry], error: str | None) -> bool:
        if error:
            self.status.set_text(f"Scan failed: {error}")
            return GLib.SOURCE_REMOVE
        self.entries = entries
        self._load_entries(entries)
        unresolved = [entry for entry in entries if not entry.resolved and entry.prefix_id.isdigit() and entry.prefix_id != "0"]
        self.status.set_text(self._status_text(entries))
        if unresolved and not self.args.no_online:
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
        self.status.set_text(self._status_text(entries))
        return GLib.SOURCE_REMOVE

    def _status_text(self, entries: list[PrefixEntry]) -> str:
        resolved = sum(1 for entry in entries if entry.resolved)
        unresolved = len(entries) - resolved
        return f"{len(entries)} prefixes, {resolved} resolved, {unresolved} unresolved"

    def _on_search_changed(self, search: Gtk.SearchEntry) -> None:
        self.filter_text = search.get_text().lower()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def _on_source_changed(self, dropdown: Gtk.DropDown, _param: GObject.ParamSpec) -> None:
        if self.args.steam_root or self.args.compatdata or not self.installations:
            return
        selected = dropdown.get_selected()
        self.selected_steam_root = None if selected == 0 else self.installations[selected - 1].root
        self.scan()

    def _filter_row(self, obj: PrefixObject) -> bool:
        if not self.filter_text:
            return True
        haystack = " ".join(
            [obj.prefix_id, obj.name, obj.source, obj.library, obj.proton, obj.compatdata_path]
        ).lower()
        return self.filter_text in haystack

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

    def _compare(self, left: PrefixObject, right: PrefixObject, attr: str) -> int:
        a = getattr(left, attr)
        b = getattr(right, attr)
        if attr == "prefix_id" and a.isdigit() and b.isdigit():
            return (int(a) > int(b)) - (int(a) < int(b))
        return (a.lower() > b.lower()) - (a.lower() < b.lower())


def open_path(path: Path, report: Callable[[str], None]) -> None:
    for command in (["gio", "open", str(path)], ["xdg-open", str(path)]):
        try:
            subprocess.Popen(command)
            return
        except OSError:
            continue
    report(f"Could not find gio or xdg-open to open {path}")


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


def bookmark_browse_start_path(path_text: str, drive_c_path: Path, compatdata_path: Path) -> Path:
    start_path = Path(path_text).expanduser()
    if start_path.exists():
        return start_path
    if drive_c_path.exists():
        return drive_c_path
    return compatdata_path


def folder_picker_command(start_path: Path) -> PickerCommand | None:
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
        super().__init__(application_id="dev.local.SteamIdentifier")
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
