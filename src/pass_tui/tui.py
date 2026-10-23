"""Curses rendering layer for pass-tui.

Thin: it renders :class:`~pass_tui.app.ListScreen` /
:class:`~pass_tui.app.FieldScreen` values and translates keystrokes into
the symbolic key names the App controller understands. All behavior lives
in app.py; this module only draws.
"""

from __future__ import annotations

import curses

from .app import App, FieldScreen, ListScreen, QuitRequested

MAX_ROWS_FALLBACK = 20


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
        return "enter"
    if ch == 27:
        return "esc"
    if ch in (curses.KEY_BACKSPACE, 127, 8):
        return "backspace"
    if ch == 9:
        return "tab"
    if ch == 3:
        return "ctrl_c"
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
    return text[: width - 1] + "…"


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
    stdscr.addnstr(0, 0, _truncate(header, width - 1), width - 1, curses.A_BOLD)

    prompt = f"> {screen.query}"
    stdscr.addnstr(1, 0, _truncate(prompt, width - 1), width - 1)

    visible = screen.visible
    for row in range(list_height):
        idx = screen.offset + row
        if idx >= len(visible):
            break
        ref = visible[idx]
        y = 2 + row
        if idx == screen.selected:
            attr = curses.A_REVERSE
        else:
            attr = curses.A_NORMAL
        line = ref.title
        if ref.urls:
            line += f"  ·  {ref.urls[0]}"
        if ref.vault_name:
            line += f"  ·  [{ref.vault_name}]"
        stdscr.addnstr(y, 0, _truncate(line, width - 1), width - 1, attr)

    status = f"{len(visible)}/{len(screen.refs)} items"
    if screen.message:
        status = f"{screen.message}  —  {status}"
    status += "  ·  enter: open  esc: back/quit  ctrl-r: refresh"
    stdscr.addnstr(
        height - 1, 0, _truncate(status, width - 1), width - 1, curses.A_DIM
    )
    stdscr.move(1, min(2 + len(screen.query), width - 1))
    stdscr.refresh()


def _draw_fields(stdscr, app: App) -> None:
    screen = app.screen
    assert isinstance(screen, FieldScreen)
    height, width = stdscr.getmaxyx()
    list_height = max(1, height - 3)
    app._clamp(screen, list_height)

    stdscr.erase()
    title = screen.ref.title if screen.ref else ""
    stdscr.addnstr(0, 0, _truncate(title, width - 1), width - 1, curses.A_BOLD)

    prompt = f"> {screen.query}"
    stdscr.addnstr(1, 0, _truncate(prompt, width - 1), width - 1)

    visible = screen.visible
    for row in range(list_height):
        idx = screen.offset + row
        if idx >= len(visible):
            break
        label = visible[idx]
        y = 2 + row
        attr = curses.A_REVERSE if idx == screen.selected else curses.A_NORMAL
        stdscr.addnstr(y, 0, _truncate(label, width - 1), width - 1, attr)

    status = f"{len(visible)} fields"
    if screen.message:
        status = f"{screen.message}  —  {status}"
    status += "  ·  enter: copy  esc: back"
    stdscr.addnstr(
        height - 1, 0, _truncate(status, width - 1), width - 1, curses.A_DIM
    )
    stdscr.move(1, min(2 + len(screen.query), width - 1))
    stdscr.refresh()


def run(app: App) -> None:
    """Run the curses event loop until the user quits."""
    app.start()
    curses.wrapper(_loop, app)


def _loop(stdscr, app: App) -> None:
    curses.curs_set(1)
    stdscr.keypad(True)
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
