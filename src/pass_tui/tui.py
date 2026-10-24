"""Curses rendering layer for pass-tui.

Thin: it renders :class:`~pass_tui.app.ListScreen` /
:class:`~pass_tui.app.FieldScreen` values and translates keystrokes into
the symbolic key names the App controller understands. All behavior lives
in app.py; this module only draws.

Key bindings (in addition to the arrow keys, page up/down, home/end):

- enter: ``Return``, ``Ctrl+m`` or ``Ctrl+j``
- back/quit: ``Esc`` or ``Ctrl+g``
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
    if ch == 7:
        return "esc"  # Ctrl+g
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
    status += "  \u00b7  enter: open  esc/ctrl-g: back/quit  ctrl-r: refresh"
    _put(stdscr, height - 1, 0, _truncate(status, width - 1), curses.A_DIM)
    stdscr.move(1, min(2 + len(screen.query), width - 1))
    stdscr.refresh()


def _draw_fields(stdscr, app: App) -> None:
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
    _put(stdscr, 0, 0, _truncate(title, width - 1), curses.A_BOLD)

    prompt = f"> {screen.query}"
    _put(stdscr, 1, 0, _truncate(prompt, width - 1), _pair(_PAIR_PROMPT))

    visible = screen.visible
    for row in range(list_height):
        idx = screen.offset + row
        if idx >= len(visible):
            break
        label = visible[idx]
        y = 2 + row
        attr = curses.A_REVERSE if idx == screen.selected else curses.A_NORMAL
        _put(stdscr, y, 0, _truncate(label, width - 1), attr)

    status = f"{len(visible)} fields"
    if screen.message:
        status = f"{screen.message}  \u2014  {status}"
    status += "  \u00b7  enter: copy  esc/ctrl-g: back"
    _put(stdscr, height - 1, 0, _truncate(status, width - 1), curses.A_DIM)
    stdscr.move(1, min(2 + len(screen.query), width - 1))
    stdscr.refresh()


def run(app: App) -> None:
    """Run the curses event loop until the user quits."""
    app.start()
    curses.wrapper(_loop, app)


def _loop(stdscr, app: App) -> None:
    curses.curs_set(1)
    stdscr.keypad(True)
    _init_colors()
    while True:
        if isinstance(app.screen, FieldScreen):
            _draw_fields(stdscr, app)
        else:
            _draw_list(stdscr, app)
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            return
        name = _key_name(ch)
        if not name:
            continue
        try:
            app.handle_key(name)
        except QuitRequested:
            return
        except KeyboardInterrupt:
            return
