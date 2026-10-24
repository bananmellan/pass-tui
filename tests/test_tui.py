"""Headless tests for the curses layer.

curses needs a real terminal, so these tests run the app inside a pty
with a fake TERM and feed it keystrokes, asserting on the final screen
state and on what was drawn. A scripted-terminal variant exercises the
rendering functions directly against a stub window.
"""

import json
import os
import pty
import sys
import time
from pathlib import Path

import pytest

from pass_tui.app import App, FieldScreen, ListScreen
from pass_tui.cache import CACHE_NAME, dump_cache
from pass_tui.config import Config
from pass_tui.models import ItemRef
from pass_tui.state import StateStore
from pass_tui import tui


def ref(share="s1", item="i1", title="GitHub", urls=("https://github.com",), vault="Personal"):
    return ItemRef(share, item, title, urls, vault, "Login")


VIEW_JSON = json.dumps(
    {
        "item": {
            "content": {
                "title": "GitHub",
                "note": "work account",
                "content": {
                    "Login": {
                        "username": "me",
                        "password": "secret",
                        "urls": ["https://github.com"],
                        "totp_uri": "",
                    }
                },
            }
        }
    }
)


class FakeClipboard:
    def __init__(self, text=""):
        self.text = text
        self.written = []

    def read(self):
        return self.text

    def write(self, value):
        self.written.append(value)


class FakeClient:
    def __init__(self, view=VIEW_JSON):
        self.view = view
        self.view_calls = []

    def list_all_items(self):
        return [], 0

    def view_item_with_retry(self, item_ref):
        self.view_calls.append(item_ref)
        return json.loads(self.view)

    def totp(self, item_ref):
        return "123456"


def make_app(tmp_path, client=None, clipboard=None, refs=None):
    from pass_tui.cache import Cache

    config = Config(cache_dir=str(tmp_path / "cache"))
    cache_dir = Path(config.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if refs is not None:
        dump_cache(refs, cache_dir / CACHE_NAME)
    client = client or FakeClient()
    cache = Cache(cache_dir / CACHE_NAME, client, ttl=config.cache_ttl)
    state = StateStore(cache_dir)
    return App(config, cache, state, client, clipboard or FakeClipboard())


# ------------------------------------------------------------ key mapping


def test_key_name_symbolic():
    import curses

    assert tui._key_name(curses.KEY_UP) == "up"
    assert tui._key_name(curses.KEY_DOWN) == "down"
    assert tui._key_name(curses.KEY_PPAGE) == "pageup"
    assert tui._key_name(curses.KEY_NPAGE) == "pagedown"
    assert tui._key_name(curses.KEY_HOME) == "home"
    assert tui._key_name(curses.KEY_END) == "end"
    assert tui._key_name(10) == "enter"
    assert tui._key_name(13) == "enter"
    assert tui._key_name(27) == "esc"
    assert tui._key_name(127) == "backspace"
    assert tui._key_name(9) == "tab"
    assert tui._key_name(3) == "ctrl_c"
    assert tui._key_name(18) == "ctrl_r"


def test_key_name_emacs_bindings():
    # Ctrl+n / Ctrl+p navigate, Ctrl+g is back/quit, Ctrl+j is enter.
    assert tui._key_name(14) == "down"  # Ctrl+n
    assert tui._key_name(16) == "up"  # Ctrl+p
    assert tui._key_name(7) == "esc"  # Ctrl+g
    assert tui._key_name(10) == "enter"  # Ctrl+j


def test_key_name_printable():
    assert tui._key_name(ord("a")) == "a"
    assert tui._key_name(ord("Z")) == "Z"
    assert tui._key_name(ord(" ")) == " "
    assert tui._key_name(0) == ""


def test_truncate():
    assert tui._truncate("hello", 10) == "hello"
    assert tui._truncate("hello world", 8) == "hello w…"
    assert tui._truncate("hello", 1) == "h"
    assert tui._truncate("hello", 0) == ""


# ------------------------------------------------------------ rendering


class StubWindow:
    """Minimal stand-in for a curses window: overlays addnstr segments
    into a character grid so multi-segment rows render correctly."""

    def __init__(self, height=24, width=80):
        self.height = height
        self.width = width
        self.cells = {}
        self.cursor = (0, 0)
        self.refreshed = 0

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.cells = {}

    def addnstr(self, y, x, text, n, attr=0):
        for i, ch in enumerate(text[:n]):
            if 0 <= y < self.height and 0 <= x + i < self.width:
                self.cells[(y, x + i)] = (ch, attr)

    def move(self, y, x):
        self.cursor = (y, x)

    def refresh(self):
        self.refreshed += 1

    def row_attr(self, y, x=0):
        return self.cells.get((y, x), ("", 0))[1]

    def row(self, y):
        chars = [self.cells.get((y, x), (" ", 0))[0] for x in range(self.width)]
        return "".join(chars)

    def text(self):
        lines = []
        for y in range(self.height):
            chars = [self.cells.get((y, x), (None, 0))[0] for x in range(self.width)]
            lines.append(
                "".join(ch if ch is not None else " " for ch in chars).rstrip()
            )
        return "\n".join(lines)


def test_draw_list_shows_items_and_prompt(tmp_path):
    app = make_app(tmp_path, refs=[ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))])
    app.start()
    win = StubWindow()
    tui._draw_list(win, app)
    text = win.text()
    assert "Proton Pass" in text
    assert win.row(1).startswith(">")
    assert "GitHub" in text
    assert "Bank" in text
    assert "https://github.com" in text
    assert "[Personal]" in text
    assert "2/2 items" in text
    assert win.refreshed == 1


def test_draw_list_shows_type_badges(tmp_path):
    from pass_tui.models import ItemRef

    refs = [
        ItemRef("s1", "i1", "GitHub", ("https://github.com",), "Personal", "Login"),
        ItemRef("s1", "i2", "actress_revolving098@smail.me", (), "Personal", "Alias"),
        ItemRef("s1", "i3", "Bank note", (), "Personal", "Note"),
        ItemRef("s1", "i4", "Visa", (), "Personal", "CreditCard"),
        ItemRef("s1", "i5", "No type", (), "Personal", ""),
    ]
    app = make_app(tmp_path, refs=refs)
    app.start()
    win = StubWindow()
    tui._draw_list(win, app)
    text = win.text()
    assert "GitHub" in text and "login" in text
    assert "actress_revolving098@smail.me" in text and "alias" in text
    assert "Bank note" in text and "note" in text
    assert "Visa" in text and "card" in text
    # Unknown/empty type: no badge text on the row.
    no_type_row = next(
        line for line in text.splitlines() if line.startswith("No type")
    )
    assert "[Personal]" in no_type_row
    for badge in ("login", "alias", "note", "card"):
        assert f"·  {badge}" not in no_type_row


def test_draw_fields_header_shows_type_badge(tmp_path):
    from pass_tui.models import ItemRef

    refs = [ItemRef("s1", "i1", "actress_revolving098@smail.me", (), "Personal", "Alias")]
    app = make_app(tmp_path, refs=refs)
    app.start()
    app.handle_key("enter")
    win = StubWindow()
    tui._draw_fields(win, app)
    assert "actress_revolving098@smail.me  ·  alias" in win.text()


def test_draw_list_shows_host_filter_and_query(tmp_path):
    app = make_app(
        tmp_path,
        clipboard=FakeClipboard("https://github.com/login"),
        refs=[ref()],
    )
    screen = app.start()
    assert isinstance(screen, FieldScreen)  # single match opens directly
    # Multi-match case:
    app2 = make_app(
        tmp_path,
        clipboard=FakeClipboard("https://github.com"),
        refs=[ref(), ref("s1", "i2", "GitHub Enterprise", ("https://github.com/enterprise",))],
    )
    screen = app2.start()
    assert isinstance(screen, ListScreen)
    win = StubWindow()
    tui._draw_list(win, app2)
    text = win.text()
    assert "(github.com)" in text
    assert "> github.com" in text


def test_draw_list_marks_selected_row(tmp_path):
    app = make_app(tmp_path, refs=[ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))])
    app.start()
    app.handle_key("down")
    win = StubWindow()
    tui._draw_list(win, app)
    # The selected row (Bank, second row at y=3) uses A_REVERSE.
    import curses

    assert win.cells[(3, 0)][1] == curses.A_REVERSE
    assert win.cells[(2, 0)][1] == curses.A_NORMAL


def test_draw_list_fixed_width_columns(tmp_path):
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",), "Personal"),
        ref("s1", "i2", "B", ("https://b.example.com",), "Work"),
    ]
    app = make_app(tmp_path, refs=refs)
    app.start()
    win = StubWindow()
    tui._draw_list(win, app)
    # Title-sorted: "B" is the first row, "GitHub" the second.
    row_b = win.row(2)
    row_g = win.row(3)
    # Badge and url columns start at the same offset on every row.
    assert row_b.index("login") == row_g.index("login")
    assert row_b.index("https://") == row_g.index("https://")
    # The vault column is right-aligned: both rows end at the same edge.
    assert row_b.rstrip().endswith("[Work]")
    assert row_g.rstrip().endswith("[Personal]")
    assert len(row_b.rstrip()) == len(row_g.rstrip())


def test_draw_list_narrow_terminal_keeps_title_column(tmp_path):
    refs = [ref("s1", "i1", "GitHub", ("https://github.com",), "Personal")]
    app = make_app(tmp_path, refs=refs)
    app.start()
    win = StubWindow(height=10, width=30)
    tui._draw_list(win, app)
    # Nothing overflows the screen width.
    for y in range(win.height):
        assert len(win.row(y).rstrip()) <= 29


def test_split_field_label():
    assert tui._split_field_label("note") == ("note", "note")
    assert tui._split_field_label("totp") == ("totp", "totp")
    assert tui._split_field_label("url: https://x.com") == ("url", "https://x.com")
    assert tui._split_field_label("email: a@b.c") == ("email", "a@b.c")
    assert tui._split_field_label("field: PIN") == ("field", "PIN")
    assert tui._split_field_label("Admin: user") == ("Admin", "user")
    assert tui._split_field_label("username") == ("field", "username")


def test_field_columns_fixed_width():
    labels = ["note", "url: https://github.com", "field: API key", "username"]
    kind_w, value_w = tui._field_columns(labels, 80)
    assert kind_w == len("field")  # widest kind
    assert kind_w + len(tui._COL_SEP) + value_w <= 79


def test_field_hint_truncates_and_marks_multiline():
    assert tui._field_hint("password", "secret") == "secret"
    assert tui._field_hint("totp", "") == ""
    long_value = "x" * 100
    assert len(tui._field_hint("password", long_value)) <= tui._MAX_HINT_COL
    multi = "line1\nline2"
    assert tui._field_hint("private_key", multi) == "line1 \u2026"


def test_draw_fields_two_columns_with_hints(tmp_path):
    app = make_app(tmp_path, refs=[ref()])
    app.start()
    app.handle_key("enter")
    assert isinstance(app.screen, FieldScreen)
    win = StubWindow()
    tui._draw_fields(win, app, app.field_values())
    rows = [win.row(2 + i) for i in range(len(app.screen.visible))]
    labels = app.screen.visible
    by_label = dict(zip(labels, rows))
    # Kind column at x=0, separator at a fixed offset on every row.
    sep_idx = {row.index(tui._COL_SEP) for row in rows}
    assert len(sep_idx) == 1
    # note row: kind "note", name "note", preview "work account".
    assert by_label["note"].startswith("note")
    assert "work account" in by_label["note"]
    # username/password are plain section fields: kind "field".
    assert by_label["username"].startswith("field")
    assert "me" in by_label["username"]
    assert by_label["password"].startswith("field")
    assert "secret" in by_label["password"]
    # url row: kind "url", name and preview are the url.
    assert by_label["url: https://github.com"].startswith("url")
    assert by_label["url: https://github.com"].count("https://github.com") == 2


def test_draw_fields_without_values_draws_two_columns(tmp_path):
    app = make_app(tmp_path, refs=[ref()])
    app.start()
    app.handle_key("enter")
    win = StubWindow()
    tui._draw_fields(win, app)  # no field_values: no hint column
    row = win.row(2)
    assert row.startswith("note")
    assert "work account" not in row


def test_draw_fields_totp_has_no_preview(tmp_path):
    view = {
        "item": {
            "content": {
                "title": "GitHub",
                "note": "",
                "content": {
                    "Login": {
                        "username": "me",
                        "password": "secret",
                        "urls": [],
                        "totp_uri": "otpauth://totp/x",
                    }
                },
            }
        }
    }
    import json as _json

    app = make_app(tmp_path, client=FakeClient(view=_json.dumps(view)), refs=[ref()])
    app.start()
    app.handle_key("enter")
    values = app.field_values()
    assert "totp" not in values  # fetched live at copy time, never stored
    win = StubWindow()
    tui._draw_fields(win, app, values)
    totp_row = next(
        win.row(2 + i)
        for i, label in enumerate(app.screen.visible)
        if label == "totp"
    )
    assert totp_row.startswith("totp")
    # No masked preview after the value column for totp.
    assert totp_row.count(tui._COL_SEP) == 1


def test_draw_fields_shows_labels(tmp_path):
    app = make_app(tmp_path, refs=[ref()])
    app.start()
    app.handle_key("enter")
    assert isinstance(app.screen, FieldScreen)
    win = StubWindow()
    tui._draw_fields(win, app)
    text = win.text()
    assert "GitHub" in text
    assert "username" in text
    assert "password" in text
    assert "note" in text
    assert "enter: copy" in text


def test_draw_fields_failed_copy_message(tmp_path):
    clipboard = FakeClipboard()

    def fail(value):
        from pass_tui.clipboard import ClipboardError

        raise ClipboardError("no clipboard")

    clipboard.write = fail
    app = make_app(tmp_path, clipboard=clipboard, refs=[ref()])
    app.start()
    app.handle_key("enter")
    labels = app.screen.labels
    idx = labels.index("password")
    for _ in range(idx):
        app.handle_key("down")
    screen = app.handle_key("enter")
    assert screen.message == "Nothing to copy"
    win = StubWindow()
    tui._draw_fields(win, app)
    assert "Nothing to copy" in win.text()


def test_draw_list_truncates_long_lines(tmp_path):
    long_title = "A" * 200
    app = make_app(tmp_path, refs=[ref(title=long_title, urls=("https://" + "b" * 100,))])
    app.start()
    win = StubWindow(height=24, width=40)
    tui._draw_list(win, app)
    assert len(win.row(2).rstrip()) <= 39


def test_draw_scrolls_when_selection_moves_past_view(tmp_path):
    refs = [ref("s1", f"i{n}", f"Item {n:02d}", ()) for n in range(30)]
    app = make_app(tmp_path, refs=refs)
    app.start()
    for _ in range(25):
        app.handle_key("down")
    win = StubWindow(height=10, width=80)
    tui._draw_list(win, app)
    # Selected item 25 must be visible; offset must have scrolled.
    text = win.text()
    assert "Item 25" in text
    assert app.screen.offset > 0


# ------------------------------------------------------------ pty smoke


def _run_in_pty(tmp_path, script, refs):
    """Run the real curses UI in a pty; `script` is a list of keystrokes
    (bytes) sent with small delays. Returns the app after the loop ends."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dump_cache(refs, cache_dir / CACHE_NAME)

    env = dict(os.environ)
    env["TERM"] = "xterm"
    env["PASS_TUI_CACHE_DIR"] = str(cache_dir)
    env["LINES"] = "24"
    env["COLUMNS"] = "80"

    argv = [sys.executable, "-c", _PTY_CHILD]
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        argv, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True
    )
    os.close(slave)
    try:
        for keys in script:
            time.sleep(0.15)
            os.write(master, keys)
        deadline = time.time() + 10
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if proc.poll() is None:
            proc.kill()
            raise AssertionError("pty child did not exit")
    finally:
        try:
            os.close(master)
        except OSError:
            pass
    return proc.returncode


_PTY_CHILD = r"""
import sys
sys.path.insert(0, %r)
from pass_tui.app import App
from pass_tui.cache import Cache, CACHE_NAME
from pass_tui.clipboard import Clipboard
from pass_tui.config import Config
from pass_tui.passcli import PassCli
from pass_tui.state import StateStore
from pass_tui.tui import run
from pathlib import Path
import json

config = Config.from_env(__import__("os").environ)
cache_dir = Path(config.cache_dir)
client = PassCli("pass-cli", runner=lambda argv: json.dumps({"vaults": []}))
cache = Cache(cache_dir / CACHE_NAME, client, ttl=config.cache_ttl)
state = StateStore(cache_dir)

VIEW = json.dumps({"item": {"content": {"title": "GitHub", "note": "", "content": {"Login": {"username": "me", "password": "secret", "urls": ["https://github.com"], "totp_uri": ""}}}}})

class C:
    def view_item_with_retry(self, ref):
        with open(cache_dir / "opened.txt", "a") as fh:
            fh.write(ref.item_id + chr(10))
        return json.loads(VIEW)
    def totp(self, ref):
        return "123456"
    def list_all_items(self):
        return [], 0

class Clip:
    def read(self):
        return ""
    def write(self, value):
        Path(cache_dir / "copied.txt").write_text(value)

app = App(config, cache, state, C(), Clip())
run(app)
""" % str(Path(__file__).resolve().parent.parent / "src")


def test_pty_quit_with_esc(tmp_path):
    rc = _run_in_pty(tmp_path, [b"\x1b"], [ref()])
    assert rc == 0


def test_pty_open_item_and_copy(tmp_path):
    rc = _run_in_pty(tmp_path, [b"\r", b"\x1b", b"\x1b"], [ref()])
    assert rc == 0
    # esc from fields goes back to list; second esc quits.
    assert (tmp_path / "cache").is_dir()


def test_pty_type_to_filter_then_quit(tmp_path):
    refs = [ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))]
    rc = _run_in_pty(tmp_path, [b"bank", b"\x1b", b"\x1b"], refs)
    assert rc == 0


def test_pty_copy_quits_program(tmp_path):
    """Copying a field must end the program, like rofi."""
    rc = _run_in_pty(tmp_path, [b"\r", b"\r"], [ref()])
    assert rc == 0
    copied = (tmp_path / "cache" / "copied.txt").read_text()
    # First copyable field of the Login view is the username.
    assert copied == "me"


def test_pty_ctrl_n_moves_down(tmp_path):
    # Title-sorted: Bank (i2) first, GitHub (i1) second.
    refs = [ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))]
    rc = _run_in_pty(tmp_path, [b"\x0e", b"\r", b"\x1b", b"\x1b"], refs)
    assert rc == 0
    opened = (tmp_path / "cache" / "opened.txt").read_text().split()
    assert opened == ["i1"]  # Ctrl+n selected GitHub


def test_pty_ctrl_p_moves_up(tmp_path):
    refs = [ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))]
    rc = _run_in_pty(tmp_path, [b"\x0e", b"\x10", b"\r", b"\x1b", b"\x1b"], refs)
    assert rc == 0
    opened = (tmp_path / "cache" / "opened.txt").read_text().split()
    assert opened == ["i2"]  # Ctrl+n then Ctrl+p: back on Bank


def test_pty_ctrl_g_goes_back_from_fields(tmp_path):
    rc = _run_in_pty(tmp_path, [b"\r", b"\x07", b"\x1b"], [ref()])
    assert rc == 0
    # Ctrl+g returned to the list, so no second open happened.
    opened = (tmp_path / "cache" / "opened.txt").read_text().split()
    assert opened == ["i1"]


def test_pty_ctrl_c_quits(tmp_path):
    """Ctrl-C must quit cleanly.

    The sandbox pty does not translate the 0x03 byte into SIGINT (ISIG
    delivery is unreliable here), so send the signal directly to the
    child's process group, exactly as a real terminal would on Ctrl-C.
    """
    import signal

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    env = dict(os.environ)
    env["TERM"] = "xterm"
    env["PASS_TUI_CACHE_DIR"] = str(cache_dir)
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-c", _PTY_CHILD],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave)
    try:
        time.sleep(0.8)
        os.killpg(proc.pid, signal.SIGINT)
        deadline = time.time() + 10
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        assert proc.poll() is not None, "pty child did not exit after SIGINT"
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            os.close(master)
        except OSError:
            pass
    assert proc.returncode == 0


import subprocess  # noqa: E402  (used by _run_in_pty)
