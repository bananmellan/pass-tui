"""UI-agnostic controller for the pass-tui application.

Owns the whole interaction state machine: the item list (with live
filter), the currently open item and its field menu, clipboard-driven
host matching at launch, and the one-shot reopen-after-copy behavior.

The controller never touches curses: it exposes the current screen as a
plain :class:`Screen` value plus an :meth:`App.handle_key` method that
returns the next screen. The curses layer (tui.py) renders screens and
feeds keystrokes; tests drive the controller directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .cache import Cache, match_host, sort_refs
from .clipboard import Clipboard, ClipboardError, extract_host
from .config import Config
from .fields import field_labels, field_value
from .models import ItemRef
from .passcli import PassCli, PassCliError
from .search import filter_items
from .state import StateStore

STAGE_LIST = "list"
STAGE_FIELDS = "fields"


@dataclass
class ListScreen:
    stage: str = STAGE_LIST
    refs: list[ItemRef] = field(default_factory=list)
    query: str = ""
    selected: int = 0
    offset: int = 0
    host_filter: str = ""
    message: str = ""
    matches: list[ItemRef] = field(default_factory=list)

    @property
    def visible(self) -> list[ItemRef]:
        return filter_items(self.refs, self.query)

    @property
    def selected_ref(self) -> ItemRef | None:
        visible = self.visible
        if 0 <= self.selected < len(visible):
            return visible[self.selected]
        return None


@dataclass
class FieldScreen:
    stage: str = STAGE_FIELDS
    ref: ItemRef | None = None
    labels: list[str] = field(default_factory=list)
    query: str = ""
    selected: int = 0
    offset: int = 0
    message: str = ""

    @property
    def visible(self) -> list[str]:
        labels = self.labels
        query = self.query.strip().lower()
        if not query:
            return labels
        return [label for label in labels if query in label.lower()]

    @property
    def selected_label(self) -> str | None:
        visible = self.visible
        if 0 <= self.selected < len(visible):
            return visible[self.selected]
        return None


Screen = ListScreen | FieldScreen


class App:
    def __init__(
        self,
        config: Config,
        cache: Cache,
        state: StateStore,
        client: PassCli,
        clipboard: Clipboard,
        now: Callable[[], float] = time.time,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.cache = cache
        self.state = state
        self.client = client
        self.clipboard = clipboard
        self._now = now
        self._log = log or (lambda msg: None)
        self.screen: Screen = ListScreen()
        self._item_view: dict = {}

    # ------------------------------------------------------------ helpers

    def _refresh_list(self, host_filter: str = "", matches: list[ItemRef] | None = None,
                      message: str = "") -> ListScreen:
        refs = self.cache.load()
        if matches is not None:
            matched_ids = {ref.item_id for ref in matches}
            rest = [ref for ref in sort_refs(refs) if ref.item_id not in matched_ids]
            ordered = matches + rest
        else:
            ordered = sort_refs(refs)
        self._log(
            f"list: {len(refs)} cached items, query={self.screen.query!r}"
            if isinstance(self.screen, ListScreen)
            else f"list: {len(refs)} cached items"
        )
        screen = ListScreen(
            refs=ordered,
            query=host_filter,
            host_filter=host_filter,
            message=message,
        )
        self.screen = screen
        self.state.set_stage(STAGE_LIST)
        self.state.set_open_item(None)
        return screen

    def _open_item(self, ref: ItemRef, message: str = "") -> Screen:
        try:
            data = self.client.view_item_with_retry(ref)
        except PassCliError as exc:
            self._log(f"view failed for {ref.title!r}: {exc}")
            return self._refresh_list(message=f"Could not open {ref.title!r}")
        labels = field_labels(data)
        if not labels:
            self._log(f"no readable fields for {ref.title!r}")
            return self._refresh_list(message=f"No readable fields in {ref.title!r}")
        self._item_view = data
        screen = FieldScreen(ref=ref, labels=labels, message=message)
        self.screen = screen
        self.state.set_stage(STAGE_FIELDS)
        self.state.set_open_item(ref)
        return screen

    def _copy_label(self, label: str) -> str:
        screen = self.screen
        if not isinstance(screen, FieldScreen) or screen.ref is None:
            return ""
        ref = screen.ref
        totp = ""
        if label == "totp":
            try:
                totp = self.client.totp(ref)
            except PassCliError as exc:
                self._log(f"totp failed: {exc}")
                return ""
        value = field_value(self._item_view, label, totp=totp)
        if not value:
            return ""
        try:
            self.clipboard.write(value)
        except ClipboardError as exc:
            self._log(f"copy failed: {exc}")
            return ""
        self.state.set_last_copied(ref, timestamp=self._now())
        return label

    # ------------------------------------------------------------ launch

    def start(self) -> Screen:
        """Initial launch flow.

        1. Reopen-after-copy: if a field was copied within
           reopen_seconds, open that item directly. The memory is
           cleared on every start, so it only ever applies to the
           launch immediately after a copy.
        2. Clipboard URL matching: if the clipboard holds a URL,
           match items from the cache by host. One match opens
           directly; several matches list them best-first with the
           host prefilled as the query.
        3. Otherwise show the full list.
        """
        now = self._now()
        last = self.state.get_last_copied()
        self.state.clear_last_copied()
        if last is not None:
            last_ref, copied_at = last
            age = now - copied_at
            self._log(f"last copied item {last_ref.title!r} age={age}")
            if age < self.config.reopen_seconds:
                screen = self._open_item(last_ref)
                if isinstance(screen, FieldScreen):
                    return screen
        else:
            self._log("no last-copied item")

        clip = self.clipboard.read()[:2048]
        self._log(f"clipboard read: {clip[:200]!r}")
        host = extract_host(clip)
        self._log(f"extracted host: {host!r}")
        if host:
            refs = self.cache.load()
            if not refs:
                try:
                    refs, raw_count = self.cache.rebuild()
                    self._log(
                        f"initial rebuild: {len(refs)} unique items from {raw_count} raw entries"
                    )
                except PassCliError as exc:
                    self._log(f"initial rebuild failed: {exc}")
                    refs = []
            with_urls = sum(1 for ref in refs if ref.urls)
            self._log(f"cache: {len(refs)} items, {with_urls} with urls")
            matches = match_host(refs, host)
            self._log(
                f"host {host!r} matched {len(matches)} items: "
                + ", ".join(f"{m.title} {list(m.urls)}" for m in matches[:10])
            )
            if len(matches) == 1:
                return self._open_item(matches[0])
            if len(matches) > 1:
                return self._refresh_list(host_filter=host, matches=matches)
            self._log("clipboard matching did not handle the launch")

        try:
            refs = self.cache.get()
        except PassCliError as exc:
            self._log(f"initial rebuild failed: {exc}")
            refs = []
        if not refs:
            try:
                refs, raw_count = self.cache.rebuild()
                self._log(
                    f"initial rebuild: {len(refs)} unique items from {raw_count} raw entries"
                )
            except PassCliError as exc:
                self._log(f"initial rebuild failed: {exc}")
        return self._refresh_list()

    # ------------------------------------------------------------ keys

    def handle_key(self, key: str) -> Screen:
        """Handle one key and return the (possibly new) screen.

        Keys are single characters (printable input) or one of the
        symbolic names: "up", "down", "pageup", "pagedown", "home",
        "end", "enter", "esc", "backspace", "tab", "ctrl_r", "ctrl_c".
        """
        if key == "ctrl_c":
            self.state.clear_last_copied()
            raise QuitRequested
        if key == "ctrl_r":
            return self._refresh_action()
        if isinstance(self.screen, FieldScreen):
            return self._handle_fields_key(key)
        return self._handle_list_key(key)

    def _clamp(self, screen: ListScreen | FieldScreen, height: int) -> None:
        visible = screen.visible
        if screen.selected >= len(visible):
            screen.selected = max(0, len(visible) - 1)
        if screen.selected < 0:
            screen.selected = 0
        if height > 0:
            if screen.selected < screen.offset:
                screen.offset = screen.selected
            elif screen.selected >= screen.offset + height:
                screen.offset = screen.selected - height + 1

    def _handle_list_key(self, key: str) -> Screen:
        screen = self.screen
        assert isinstance(screen, ListScreen)
        visible = screen.visible
        if key == "enter":
            ref = screen.selected_ref
            if ref is None:
                return screen
            return self._open_item(ref)
        if key == "esc":
            if screen.query:
                screen.query = ""
                screen.selected = 0
                screen.offset = 0
                return screen
            raise QuitRequested
        if key == "up":
            screen.selected = max(0, screen.selected - 1)
            return screen
        if key == "down":
            screen.selected = min(max(0, len(visible) - 1), screen.selected + 1)
            return screen
        if key == "pageup":
            screen.selected = max(0, screen.selected - 10)
            return screen
        if key == "pagedown":
            screen.selected = min(max(0, len(visible) - 1), screen.selected + 10)
            return screen
        if key == "home":
            screen.selected = 0
            return screen
        if key == "end":
            screen.selected = max(0, len(visible) - 1)
            return screen
        if key == "backspace":
            if screen.query:
                screen.query = screen.query[:-1]
                screen.selected = 0
                screen.offset = 0
            return screen
        if len(key) == 1 and key.isprintable():
            screen.query += key
            screen.selected = 0
            screen.offset = 0
            return screen
        return screen

    def _handle_fields_key(self, key: str) -> Screen:
        screen = self.screen
        assert isinstance(screen, FieldScreen)
        visible = screen.visible
        if key == "enter":
            label = screen.selected_label
            if label is None:
                return screen
            copied = self._copy_label(label)
            if copied:
                # Like rofi: copying a field ends the program. The
                # reopen-after-copy memory is armed inside _copy_label
                # before we quit, so the next launch reopens this item.
                raise QuitRequested
            screen.message = "Nothing to copy"
            return screen
        if key == "esc":
            if screen.query:
                screen.query = ""
                screen.selected = 0
                screen.offset = 0
                return screen
            return self._refresh_list()
        if key == "up":
            screen.selected = max(0, screen.selected - 1)
            return screen
        if key == "down":
            screen.selected = min(max(0, len(visible) - 1), screen.selected + 1)
            return screen
        if key == "pageup":
            screen.selected = max(0, screen.selected - 10)
            return screen
        if key == "pagedown":
            screen.selected = min(max(0, len(visible) - 1), screen.selected + 10)
            return screen
        if key == "home":
            screen.selected = 0
            return screen
        if key == "end":
            screen.selected = max(0, len(visible) - 1)
            return screen
        if key == "backspace":
            if screen.query:
                screen.query = screen.query[:-1]
                screen.selected = 0
                screen.offset = 0
            return screen
        if len(key) == 1 and key.isprintable():
            screen.query += key
            screen.selected = 0
            screen.offset = 0
            return screen
        return screen

    def _refresh_action(self) -> Screen:
        try:
            refs, raw_count = self.cache.rebuild()
            self._log(f"refresh: {len(refs)} unique items from {raw_count} raw entries")
            return self._refresh_list()
        except PassCliError as exc:
            self._log(f"refresh failed: {exc}")
            if isinstance(self.screen, FieldScreen):
                return self._refresh_list(message=f"Refresh failed: {exc}")
            screen = self._refresh_list(message=f"Refresh failed: {exc}")
            return screen


class QuitRequested(Exception):
    """Raised by App.handle_key when the user asks to quit."""
