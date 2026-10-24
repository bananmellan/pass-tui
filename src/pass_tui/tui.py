"""Curses rendering layer for pass-tui.

Thin: it renders :class:`~pass_tui.app.ListScreen` /
:class:`~pass_tui.app.FieldScreen` values and translates keystrokes into
the symbolic key names the App controller understands. All behavior lives
in app.py; this module only draws.

Key bindings (in addition to the arrow keys, page up/down, home/end):

- enter: ``Return``, ``Ctrl+m`` or ``Ctrl+j``
- back/quit: ``Esc`` or ``Ctrl+l``
- up/down: arrow keys, ``Ctrl+p`` / ``Ctrl+n`` (emacs style)
- refresh: ``Ctrl+r``; quit: ``Ctrl+c``

The list renders fixed-width columns (title, type badge, url, vault) so
rows line up; columns are colored when the terminal supports colors.
"""

from __future__ import annotations

import curses

from .app import App, FieldScreen, ListScreen, QuitRequested
from .types import type_badge

MAX_ROWS_FALLBACK = 20

# Column layout caps for the item list.
_MAX_URL_COL = 40
_MAX_VAULT_COL = 20
_MIN_TITLE_COL = 8
_COL_SEP = "  \u00b7  "

# Color pairs (initialized in _loop when the terminal supports colors).
_PAIR_TITLE = 1
_PAIR_BADGE = 2
_PAIR_URL = 3
_PAIR_VAULT = 4
_PAIR_PROMPT = 5
_COLORS_READY = False


def _init_colors() -> None:
    global _COLORS_READY
    if _COLORS_READY or not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    if curses.COLORS < 8:
        return
    curses.init_pair(_PAIR_TITLE, curses.COLOR_WHITE, bg)
    curses.init_pair(_PAIR_BADGE, curses.COLOR_CYAN, bg)
    curses.init_pair(_PAIR_URL, curses.COLOR_BLUE, bg)
    curses.init_pair(_PAIR_VAULT, curses.COLOR_MAGENTA, bg)
    curses.init_pair(_PAIR_PROMPT, curses.COLOR_YELLOW, bg)
    _COLORS_READY = True


def _pair(n: int) -> int:
    """Color-pair attribute, or 0 when colors are unavailable (tests)."""
    return curses.color_pair(n) if _COLORS_READY else 0


def _key_name(ch: int) -> str:
    if ch == curses.KEY_UP:
        return "up"
    if ch == curses.KEY_DOWN:
        return "down"
    if ch == curses.KEY_PPAGE:
        return "pageup"
    if ch == curses.KEY_NPAGE:
        return "pagedown"
    if ch == curses.KEY_HOME:
        return "home"
    if ch == curses.KEY_END:
        return "end"
    if ch in (10, 13, curses.KEY_ENTER):
        # 10 = Ctrl+j (line feed), 13 = Ctrl+m (carriage return)
        return "enter"
    if ch == 27:
        return "esc"
    if ch == 12:
        return "esc"  # Ctrl+l
    if ch in (curses.KEY_BACKSPACE, 127, 8):
        return "backspace"
    if ch == 9:
        return "tab"
    if ch == 3:
        return "ctrl_c"
    if ch == 14:
        return "down"  # Ctrl+n
    if ch == 16:
        return "up"  # Ctrl+p
    if ch == 18:
        return "ctrl_r"
    if 32 <= ch < 127:
        return chr(ch)
    if 0 < ch < 32:
        return f"ctrl_{chr(ch + 96)}"
    return ""


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "\u2026"


def _put(stdscr, y: int, x: int, text: str, attr: int) -> None:
    """Write text at (y, x), clipped to the screen edge."""
    height, width = stdscr.getmaxyx()
    if y < 0 or y >= height or x >= width - 1:
        return
    stdscr.addnstr(y, x, text, max(0, min(len(text), width - 1 - x)), attr)


def _list_columns(refs: list, width: int) -> tuple[int, int, int, int]:
    """Fixed column widths for the list: (title, badge, url, vault).

    Badge/url/vault columns are as wide as their widest cell (capped);
    the title column takes the remaining space.
    """
    avail = max(1, width - 1)
    badge_w = max((len(type_badge(r.item_type)) for r in refs), default=0)
    url_w = min(max((len(r.urls[0]) for r in refs if r.urls), default=0), _MAX_URL_COL)
    vault_w = min(
        max((len(r.vault_name) + 2 for r in refs if r.vault_name), default=0),
        _MAX_VAULT_COL,
    )
    fixed = 0
    columns = 1  # title
    if badge_w:
        fixed += badge_w + len(_COL_SEP)
        columns += 1
    if url_w:
        fixed += url_w + len(_COL_SEP)
        columns += 1
    if vault_w:
        fixed += vault_w
        if columns > 1:
            fixed += len(_COL_SEP)
        columns += 1
    title_w = max(_MIN_TITLE_COL, avail - fixed - 1)
    return title_w, badge_w, url_w, vault_w


def _draw_list(stdscr, app: App) -> None:
    screen = app.screen
    assert isinstance(screen, ListScreen)
    height, width = stdscr.getmaxyx()
    list_height = max(1, height - 3)
    app._clamp(screen, list_height)

    stdscr.erase()
    header = "Proton Pass"
    if screen.host_filter:
        header += f"  ({screen.host_filter})"
    _put(stdscr, 0, 0, _truncate(header, width - 1), curses.A_BOLD)

    prompt = f"> {screen.query}"
    _put(stdscr, 1, 0, _truncate(prompt, width - 1), _pair(_PAIR_PROMPT))

    visible = screen.visible
    title_w, badge_w, url_w, vault_w = _list_columns(visible, width)

    title_attr = _pair(_PAIR_TITLE)
    badge_attr = curses.A_BOLD | _pair(_PAIR_BADGE)
    url_attr = _pair(_PAIR_URL)
    vault_attr = _pair(_PAIR_VAULT)

    for row in range(list_height):
        idx = screen.offset + row
        if idx >= len(visible):
            break
        ref = visible[idx]
        y = 2 + row
        selected = idx == screen.selected
        base = curses.A_REVERSE if selected else curses.A_NORMAL

        x = 0
        _put(stdscr, y, x, _truncate(ref.title, title_w).ljust(title_w), base | title_attr)
        x += title_w
        badge = type_badge(ref.item_type)
        if badge_w:
            _put(stdscr, y, x, _COL_SEP, base | curses.A_DIM)
            x += len(_COL_SEP)
            _put(stdscr, y, x, _truncate(badge, badge_w).ljust(badge_w), base | badge_attr)
            x += badge_w
        if url_w:
            _put(stdscr, y, x, _COL_SEP, base | curses.A_DIM)
            x += len(_COL_SEP)
            url = _truncate(ref.urls[0], url_w) if ref.urls else ""
            _put(stdscr, y, x, url.ljust(url_w), base | url_attr)
            x += url_w
        if vault_w:
            _put(stdscr, y, x, _COL_SEP, base | curses.A_DIM)
            x += len(_COL_SEP)
            vault = f"[{ref.vault_name}]" if ref.vault_name else ""
            _put(stdscr, y, x, _truncate(vault, vault_w).rjust(vault_w), base | vault_attr)

    status = f"{len(visible)}/{len(screen.refs)} items"
    if screen.message:
        status = f"{screen.message}  \u2014  {status}"
    status += "  \u00b7  enter: open  esc/ctrl-l: back/quit  ctrl-r: refresh"
    _put(stdscr, height - 1, 0, _truncate(status, width - 1), curses.A_DIM)
    if app.busy:
        _put(stdscr, 0, max(0, width - len(app.busy) - 1), app.busy,
             curses.A_BOLD | _pair(_PAIR_PROMPT))
    stdscr.move(1, min(2 + len(screen.query), width - 1))
    stdscr.refresh()


_MAX_KIND_COL = 24
_MAX_HINT_COL = 40

# Field-label classification for the two-column field view.
_KIND_SPECIAL = {
    "note": "note",
    "totp": "totp",
}


def _split_field_label(label: str) -> tuple[str, str]:
    """Split a field label into (kind, name) for the two-column view.

    "note" -> ("note", "note"); "totp" -> ("totp", "totp");
    "url: https://x" -> ("url", "https://x"); "email: a@b" ->
    ("email", "a@b"); "field: PIN" -> ("field", "PIN");
    "Admin: user" -> ("Admin", "user"); "username" -> ("field", "username").
    """
    kind = _KIND_SPECIAL.get(label)
    if kind:
        return kind, kind
    for prefix in ("url: ", "email: ", "field: "):
        if label.startswith(prefix):
            return prefix[:-2], label[len(prefix):]
    if ": " in label:
        kind, _, name = label.partition(": ")
        return kind, name
    return "field", label


def _field_columns(labels: list[str], width: int) -> tuple[int, int]:
    """Fixed column widths for the field view: (kind, value).

    The kind column is as wide as the widest kind (capped); the value
    column takes the remaining space.
    """
    avail = max(1, width - 1)
    kind_w = min(
        max((len(_split_field_label(label)[0]) for label in labels), default=0),
        _MAX_KIND_COL,
    )
    value_w = max(_MIN_TITLE_COL, avail - kind_w - len(_COL_SEP) - 1)
    return kind_w, value_w


def _field_hint(label: str, value: str) -> str:
    """A short preview of a field's value, shown after the value column
    so rows are distinguishable without printing secrets in full. Long
    values are truncated; multi-line values show only their first line."""
    if not value:
        return ""
    if "\n" in value:
        first = value.split("\n", 1)[0]
        return _truncate(first, _MAX_HINT_COL) + " \u2026"
    return _truncate(value, _MAX_HINT_COL)


def _draw_fields(stdscr, app: App, field_values: dict[str, str] | None = None) -> None:
    """Render the field menu as fixed-width (kind, value) columns with
    per-column colors, matching the list view's style.

    `field_values` maps label -> copyable value for masked previews;
    when absent (e.g. headless tests) no preview column is drawn.
    """
    screen = app.screen
    assert isinstance(screen, FieldScreen)
    height, width = stdscr.getmaxyx()
    list_height = max(1, height - 3)
    app._clamp(screen, list_height)

    stdscr.erase()
    if screen.ref is not None:
        badge = type_badge(screen.ref.item_type)
        title = f"{screen.ref.title}  \u00b7  {badge}" if badge else screen.ref.title
    else:
        title = ""
    _put(stdscr, 0, 0, _truncate(title, width - 1), curses.A_BOLD | _pair(_PAIR_TITLE))

    prompt = f"> {screen.query}"
    _put(stdscr, 1, 0, _truncate(prompt, width - 1), _pair(_PAIR_PROMPT))

    visible = screen.visible
    kind_w, value_w = _field_columns(visible, width)

    kind_attr = curses.A_BOLD | _pair(_PAIR_BADGE)
    value_attr = _pair(_PAIR_URL)

    for row in range(list_height):
        idx = screen.offset + row
        if idx >= len(visible):
            break
        label = visible[idx]
        y = 2 + row
        selected = idx == screen.selected
        base = curses.A_REVERSE if selected else curses.A_NORMAL

        kind, name = _split_field_label(label)
        x = 0
        _put(stdscr, y, x, _truncate(kind, kind_w).ljust(kind_w), base | kind_attr)
        x += kind_w
        _put(stdscr, y, x, _COL_SEP, base | curses.A_DIM)
        x += len(_COL_SEP)
        value = _truncate(name, value_w)
        _put(stdscr, y, x, value, base | value_attr)
        x += len(value)
        if field_values is not None:
            hint = _field_hint(label, field_values.get(label, ""))
            if hint and x + len(_COL_SEP) + len(hint) < width - 1:
                _put(stdscr, y, x, _COL_SEP, base | curses.A_DIM)
                _put(stdscr, y, x + len(_COL_SEP), hint, base | curses.A_DIM)

    status = f"{len(visible)} fields"
    if screen.message:
        status = f"{screen.message}  \u2014  {status}"
    status += "  \u00b7  enter: copy  esc/ctrl-l: back"
    _put(stdscr, height - 1, 0, _truncate(status, width - 1), curses.A_DIM)
    if app.busy:
        _put(stdscr, 0, max(0, width - len(app.busy) - 1), app.busy,
             curses.A_BOLD | _pair(_PAIR_PROMPT))
    stdscr.move(1, min(2 + len(screen.query), width - 1))
    stdscr.refresh()


def run(app: App) -> None:
    """Run the curses event loop until the user quits.

    The UI is brought up before the initial load so a slow cache
    rebuild shows a loading indicator instead of an empty screen.
    """
    curses.wrapper(_loop, app)


def _draw(stdscr, app: App) -> None:
    if isinstance(app.screen, FieldScreen):
        _draw_fields(stdscr, app, app.field_values())
    else:
        _draw_list(stdscr, app)


# Keys that may trigger a slow pass-cli call; while one runs, the busy
# indicator is shown immediately (before the handler returns).
_BUSY_PREVIEW = {
    "ctrl_r": "Reloading items\u2026",
    "enter": "Loading\u2026",
}


def _loop(stdscr, app: App) -> None:
    curses.curs_set(1)
    stdscr.keypad(True)
    _init_colors()
    if not app._started:
        app.start()
    while True:
        # A background cache refresh (stale/missing cache at launch)
        # re-renders the list as soon as it lands — even while the
        # loop is blocked in getch().
        refreshed = app.poll_refresh()
        if refreshed is not None:
            _draw(stdscr, app)
            continue
        _draw(stdscr, app)
        stdscr.timeout(100)
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            return
        finally:
            stdscr.timeout(-1)
        name = _key_name(ch)
        if not name:
            continue
        # If this key may block, show the indicator before handling it
        # so the user gets feedback during the wait. The app sets its
        # own busy text when a slow operation actually starts; when the
        # operation turns out to be instant (cache hit) the preview is
        # reclaimed here so the indicator never sticks.
        preview = name in _BUSY_PREVIEW and not app.busy
        if preview:
            app.busy = _BUSY_PREVIEW[name]
            _draw(stdscr, app)
        try:
            app.handle_key(name)
        except QuitRequested:
            return
        except KeyboardInterrupt:
            return
        finally:
            if preview and app.busy == _BUSY_PREVIEW[name]:
                app.busy = ""


class Splash:
    """Boot screen shown while the auth gate and initial load run.

    The UI comes up before any pass-cli call so a slow session probe
    never leaves a blank terminal: the splash draws a title, a status
    line ("Checking session…", "Loading items…", gate messages)
    and, when the gate asks for input, a prompt line. Prompts (lock
    code, access token, lock setup) read hidden input here — the
    typed text is never drawn. Esc/Ctrl-c at a prompt aborts it.
    """

    def __init__(self, stdscr) -> None:
        self._stdscr = stdscr
        self.status = "Starting…"
        self._prompt_label = ""
        self._input = ""
        stdscr.keypad(True)

    def note(self, text: str) -> None:
        """Update the status line (also used for gate messages)."""
        self.status = text
        self._prompt_label = ""  # a status update ends any prompt display
        self._redraw()

    def redraw(self) -> None:
        self._redraw()

    def prompt(self, label: str = "") -> str:
        """Prompt for hidden input on the splash's prompt line.

        Echo is disabled; the typed text is never drawn. Backspace
        edits, Enter accepts. Raises AuthAborted on Esc/Ctrl-c/EOF.
        """
        from .auth import AuthAborted

        self._prompt_label = label
        self._input = ""
        curses.curs_set(1)
        self._redraw()
        while True:
            try:
                ch = self._stdscr.getch()
            except KeyboardInterrupt:
                raise AuthAborted() from None
            if ch == 27 or ch == 3:  # Esc / Ctrl-c
                curses.curs_set(0)
                raise AuthAborted() from None
            if ch in (10, 13, curses.KEY_ENTER):
                curses.curs_set(0)
                return self._input
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                self._input = self._input[:-1]
                continue
            if 32 <= ch < 127:
                self._input += chr(ch)

    def _redraw(self) -> None:
        stdscr = self._stdscr
        _height, width = stdscr.getmaxyx()
        stdscr.erase()
        _put(stdscr, 0, 0, "Proton Pass", curses.A_BOLD)
        _put(stdscr, 1, 0, _truncate(self.status, width - 1), _pair(_PAIR_PROMPT))
        if self._prompt_label:
            _put(stdscr, 3, 0, _truncate(self._prompt_label, width - 1), curses.A_DIM)
            # The input line shows a cursor only; the input stays hidden.
            _put(stdscr, 4, 0, "> ", _pair(_PAIR_PROMPT))
            stdscr.move(4, 2)
        stdscr.refresh()


def run_splash(stdscr, config, client):
    """Boot the app behind a splash screen, inside a curses session.

    Brings up the splash, probes the pass-cli session in a worker
    thread (Esc/Ctrl-c during the probe cancels the boot), runs the
    auth gate (lock-code / access-token prompts render on the splash),
    builds the App and runs its non-blocking launch flow, then returns
    (True, app). The caller continues with the main event loop on the
    same stdscr.

    Returns (False, None) when the user cancels during the boot or
    authentication fails; the caller prints the outcome and exits.
    The splash is torn down by the caller's draw (the list/field
    screens erase before drawing).
    """
    import threading

    from . import cli as _cli
    from .auth import AuthAborted, ensure_authenticated, setup_session_locks
    from .passcli import SESSION_LOGGED_OUT

    _init_colors()
    curses.curs_set(0)
    splash = Splash(stdscr)

    def gate_log(message: str) -> None:
        if config.verbose:
            splash.note(message)

    # Probe the session in a worker thread so the splash stays live
    # while pass-cli answers: Esc/Ctrl-c during the probe cancels.
    splash.note("Checking session…")
    probe: dict = {"state": None}
    probe_done = threading.Event()

    def do_probe() -> None:
        try:
            probe["state"] = client.session_state()
        except Exception:
            probe["state"] = SESSION_LOGGED_OUT
        probe_done.set()

    threading.Thread(target=do_probe, daemon=True).start()
    stdscr.timeout(100)
    try:
        while not probe_done.is_set():
            splash.redraw()
            try:
                ch = stdscr.getch()
            except KeyboardInterrupt:
                ch = 3
            if ch in (27, 3):  # Esc / Ctrl-c
                return False, None
    finally:
        stdscr.timeout(-1)

    try:
        authenticated = ensure_authenticated(
            client,
            state=probe["state"],
            prompt_token=lambda: splash.prompt("Personal access token (input hidden): "),
            prompt_lock=lambda: splash.prompt("Lock code (input hidden): "),
            stream=splash.note,
            log=gate_log,
        )
    except AuthAborted:
        return False, None
    if not authenticated:
        return False, None
    splash.note("Loading items…")
    setup_session_locks(
        client,
        config,
        prompt_lock=lambda: splash.prompt("New lock code (input hidden): "),
        stream=splash.note,
        log=gate_log,
    )

    app = _cli.build_app(config, client=client)
    # The launch flow is non-blocking: it renders from the cache and
    # refreshes stale/missing caches in the background, so the splash
    # hands over to the list immediately.
    app.start()
    return True, app
