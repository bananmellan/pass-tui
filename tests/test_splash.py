"""Tests for the boot splash (tui.Splash / tui.run_splash) and the
callable-stream support in the auth gate."""

import io
import threading
import time

import curses

import pytest

from pass_tui import tui
from pass_tui.auth import AuthAborted, ensure_authenticated
from pass_tui.config import Config
from pass_tui.bitwarden import BwError, WrongPasswordError


@pytest.fixture(autouse=True)
def _no_curses_calls(monkeypatch):
    """Headless: patch the curses calls the splash/run_splash make."""
    monkeypatch.setattr(curses, "curs_set", lambda *_a, **_k: None)
    monkeypatch.setattr(curses, "has_colors", lambda: False)
    monkeypatch.setattr(tui, "_COLORS_READY", False)

UNLOCKED = "unlocked"
LOCKED = "locked"
LOGGED_OUT = "logged_out"


class StubWindow:
    """Minimal curses window: renders into a cell grid, scripts getch."""

    def __init__(self, height=24, width=80, keys=None):
        self.height = height
        self.width = width
        self.cells = {}
        self.cursor = (0, 0)
        self._keys = list(keys or [])
        self.keypad_enabled = False
        self.timeout_value = None
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

    def keypad(self, flag):
        self.keypad_enabled = flag

    def timeout(self, ms):
        self.timeout_value = ms

    def nodelay(self, flag):
        pass  # accepted for read_key compatibility

    def getch(self):
        if self._keys:
            return self._keys.pop(0)
        return -1  # curses ERR (non-blocking timeout)

    def refresh(self):
        self.refreshed += 1

    def text(self):
        lines = []
        for y in range(self.height):
            chars = [self.cells.get((y, x), (None, 0))[0] for x in range(self.width)]
            lines.append("".join(ch if ch is not None else " " for ch in chars).rstrip())
        return "\n".join(lines)

    def row(self, y):
        chars = [self.cells.get((y, x), (" ", 0))[0] for x in range(self.width)]
        return "".join(chars)


class FakeClient:
    def __init__(self, state=UNLOCKED, probe_delay=0.0, unlock_error=None,
                 login_error=None):
        self._state = state
        self._probe_delay = probe_delay
        self._unlock_error = unlock_error
        self._login_error = login_error
        self.login_calls = []  # list of (email, password)
        self.api_key_logins = 0
        self.unlock_calls = []  # list of master passwords
        self.probe_calls = 0

    def session_state(self):
        self.probe_calls += 1
        if self._probe_delay:
            time.sleep(self._probe_delay)
        return self._state

    def unlock_session(self, password):
        self.unlock_calls.append(password)
        if self._unlock_error is not None:
            raise self._unlock_error
        self._state = UNLOCKED

    def login(self, email, password):
        self.login_calls.append((email, password))
        if self._login_error is not None:
            raise self._login_error
        self._state = UNLOCKED

    def login_with_api_key(self):
        self.api_key_logins += 1
        if self._login_error is not None:
            raise self._login_error
        # An API key cannot derive the vault key: the vault stays locked.
        self._state = LOCKED


# ------------------------------------------------------------ Splash


def test_splash_draws_status_line():
    win = StubWindow()
    splash = tui.Splash(win)
    splash.note("Checking session\u2026")
    text = win.text()
    assert "Bitwarden" in win.row(0)
    assert "Checking session" in win.row(1)


def test_splash_centers_title():
    """The "Bitwarden" title is centered on row 0."""
    win = StubWindow(height=24, width=80)
    splash = tui.Splash(win)
    splash.note("Checking session\u2026")
    full_row = win.row(0)
    title = "Bitwarden"
    idx = full_row.index(title)
    leading = idx
    trailing = 80 - (idx + len(title))
    assert abs(trailing - leading) <= 2


def test_splash_centers_status():
    """The status line is centered on row 1."""
    win = StubWindow(height=24, width=80)
    splash = tui.Splash(win)
    splash.note("Checking session\u2026")
    full_row = win.row(1)
    status = "Checking session\u2026"
    idx = full_row.index(status)
    leading = idx
    trailing = 80 - (idx + len(status))
    assert abs(trailing - leading) <= 2


def test_splash_prompt_has_ascii_box():
    """The prompt is drawn inside a centered box with rounded corners."""
    keys = [10]  # just Enter
    win = StubWindow(height=24, width=80, keys=keys)
    splash = tui.Splash(win)
    splash.prompt("Master password: ")
    text = win.text()
    # Box drawing characters present.
    assert "\u256d" in text  # top-left corner
    assert "\u256e" in text  # top-right corner
    assert "\u2570" in text  # bottom-left corner
    assert "\u256f" in text  # bottom-right corner
    assert "\u2502" in text  # vertical bar
    assert "\u2500" in text  # horizontal bar


def test_splash_prompt_box_is_centered():
    """The prompt box is horizontally centered."""
    keys = [10]
    win = StubWindow(height=24, width=80, keys=keys)
    splash = tui.Splash(win)
    splash.prompt("Master password: ")
    # Find the box top border on row 3.
    row3 = win.row(3)
    corner_idx = row3.index("\u256d")
    # The box should be roughly centered: leading whitespace ~ trailing.
    leading = corner_idx
    # Find the right corner.
    right_idx = row3.index("\u256e")
    trailing = 80 - (right_idx + 1)
    assert abs(trailing - leading) <= 2


def test_splash_prompt_reads_hidden_input():
    # Type "1234", a backspace, "5", then Enter -> "1235".
    keys = [ord("1"), ord("2"), ord("3"), ord("4"), 127, ord("5"), 10]
    win = StubWindow(keys=keys)
    splash = tui.Splash(win)
    value = splash.prompt("Master password (input hidden): ")
    assert value == "1235"
    # The label is drawn inside the box; the typed characters are
    # never drawn.
    assert "Master password" in win.row(4)
    assert "> " in win.row(5)
    assert "1235" not in win.text()
    assert "1234" not in win.text()


def test_splash_prompt_shows_bullet_per_char():
    """Each typed character shows as a bullet (●) on the input line;
    the actual characters are never drawn."""
    keys = [ord("1"), ord("2"), ord("3"), 10]
    win = StubWindow(keys=keys)
    splash = tui.Splash(win)
    splash.prompt("Master password: ")
    # After typing 3 chars, the input line shows 3 bullets.
    assert "> ●●●" in win.row(5)
    # The actual digits never appear.
    assert "123" not in win.text()


def test_splash_prompt_bullets_update_on_backspace():
    """Backspace removes a bullet along with the character."""
    keys = [ord("1"), ord("2"), 127, 10]  # type "12", backspace, enter
    win = StubWindow(keys=keys)
    splash = tui.Splash(win)
    value = splash.prompt("Master password: ")
    assert value == "1"
    # Final state: one bullet remains.
    assert "> ●" in win.row(5)
    assert "●●" not in win.row(5)


def test_splash_prompt_esc_aborts():
    win = StubWindow(keys=[27])
    splash = tui.Splash(win)
    try:
        splash.prompt("Master password: ")
        raise AssertionError("expected AuthAborted")
    except AuthAborted:
        pass


def test_splash_prompt_ctrl_c_aborts():
    win = StubWindow(keys=[3])
    splash = tui.Splash(win)
    try:
        splash.prompt("Master password: ")
        raise AssertionError("expected AuthAborted")
    except AuthAborted:
        pass


def test_splash_note_clears_prompt_display():
    win = StubWindow(keys=[10])
    splash = tui.Splash(win)
    splash.prompt("Master password: ")  # consumes Enter, returns ""
    splash.note("Loading items\u2026")
    assert "Master password" not in win.text()
    assert "Loading items" in win.row(1)


# ------------------------------------------------- callable-stream gate


def test_gate_messages_go_to_callable_stream():
    """With a callable stream, gate messages are delivered to it
    (the splash renders them as status) instead of printed."""
    client = FakeClient(state=LOCKED, unlock_error=BwError("boom"))
    messages = []
    assert ensure_authenticated(
        client,
        prompt_email=lambda: "me@x.eu",
        prompt_password=lambda: "1234",
        stream=messages.append,
    ) is False
    assert any("unlock failed" in m for m in messages)


def test_ensure_authenticated_accepts_preprobed_state():
    """A pre-probed state skips the probe: the client is not queried
    again for the initial classification."""
    client = FakeClient(state=UNLOCKED)
    assert ensure_authenticated(client, state=UNLOCKED) is True
    assert client.probe_calls == 0


def test_ensure_authenticated_probes_when_no_state_given():
    client = FakeClient(state=UNLOCKED)
    assert ensure_authenticated(client) is True
    assert client.probe_calls == 1


def test_gate_never_echoes_secrets_to_callable_stream():
    client = FakeClient(state=LOCKED, unlock_error=WrongPasswordError("wrong master password"))
    messages = []

    codes = iter(["0000", "1234"])

    def unlock(code):
        client.unlock_calls.append(code)
        if code == "0000":
            raise WrongPasswordError("wrong master password")
        client._state = UNLOCKED

    client.unlock_session = unlock
    assert ensure_authenticated(
        client, prompt_password=lambda: next(codes), stream=messages.append
    ) is True
    blob = "\n".join(messages)
    assert "0000" not in blob
    assert "1234" not in blob


# ------------------------------------------------------------ run_splash


class _GateClient(FakeClient):
    """FakeClient that also satisfies build_app's BwCli usage for
    the cache: list_all_items returns no items."""

    def list_all_items(self):
        return [], 0


def _patch_build_app(monkeypatch, tmp_path, client):
    """Point tui.build_app's App construction at a fake cache/client."""
    from pass_tui.app import App
    from pass_tui.cache import CACHE_NAME, Cache
    from pass_tui.clipboard import Clipboard
    from pass_tui.state import StateStore

    def fake_build_app(config, client=None, clipboard=None):
        cache_dir = __import__("pathlib").Path(config.cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = Cache(cache_dir / CACHE_NAME, client, ttl=config.cache_ttl)
        state = StateStore(cache_dir)

        class Clip:
            def read(self):
                return ""

            def write(self, value):
                pass

        return App(config, cache, state, client, Clip())

    from pass_tui import cli

    monkeypatch.setattr(cli, "build_app", fake_build_app)


def test_run_splash_unlocked_session_boots_app(monkeypatch, tmp_path):
    client = _GateClient(state=UNLOCKED)
    config = Config(cache_dir=str(tmp_path / "cache"))
    _patch_build_app(monkeypatch, tmp_path, client)
    win = StubWindow()
    ok, app = tui.run_splash(win, config, client)
    assert ok is True
    assert app is not None
    assert client.probe_calls == 1
    # The splash reached the loading stage.
    assert "Loading items" in win.row(1) or app._started


def test_run_splash_locked_session_prompts_and_unlocks(monkeypatch, tmp_path):
    client = _GateClient(state=LOCKED)
    config = Config(cache_dir=str(tmp_path / "cache"))
    _patch_build_app(monkeypatch, tmp_path, client)
    # The splash prompt reads from the window's scripted keys.
    win = StubWindow(keys=[ord("1"), ord("2"), ord("3"), ord("4"), 10])
    ok, app = tui.run_splash(win, config, client)
    assert ok is True
    assert client.unlock_calls == ["1234"]
    # The code is never drawn.
    assert "1234" not in win.text()


def test_run_splash_logged_out_prompts_email_and_password(monkeypatch, tmp_path):
    """No API key in the environment: the gate asks for the account
    email (visible input) and then the master password (hidden)."""
    monkeypatch.delenv("BW_CLIENTID", raising=False)
    monkeypatch.delenv("BW_CLIENTSECRET", raising=False)
    client = _GateClient(state=LOGGED_OUT)
    config = Config(cache_dir=str(tmp_path / "cache"))
    _patch_build_app(monkeypatch, tmp_path, client)
    keys = [ord(c) for c in "me@x.eu"] + [10] + [ord(c) for c in "hunter2"] + [10]
    win = StubWindow(keys=keys)
    ok, app = tui.run_splash(win, config, client)
    assert ok is True
    assert client.login_calls == [("me@x.eu", "hunter2")]
    # The password is never drawn (the splash clears the prompt box once
    # the gate moves on).
    assert "hunter2" not in win.text()


def test_run_splash_api_key_env_then_unlock(monkeypatch, tmp_path):
    """BW_CLIENTID+BW_CLIENTSECRET in the environment: the gate logs in
    with the API key (the vault stays locked), then prompts for the
    master password to unlock."""
    monkeypatch.setenv("BW_CLIENTID", "user.abc")
    monkeypatch.setenv("BW_CLIENTSECRET", "sekret.def")
    client = _GateClient(state=LOGGED_OUT)
    config = Config(cache_dir=str(tmp_path / "cache"))
    _patch_build_app(monkeypatch, tmp_path, client)
    keys = [ord(c) for c in "1234"] + [10]
    win = StubWindow(keys=keys)
    ok, app = tui.run_splash(win, config, client)
    assert ok is True
    assert client.api_key_logins == 1
    assert client.unlock_calls == ["1234"]
    assert "1234" not in win.text()


def test_run_splash_cancel_during_probe(monkeypatch, tmp_path):
    """Esc while the session probe is still running cancels the boot."""
    client = _GateClient(state=UNLOCKED, probe_delay=2.0)
    config = Config(cache_dir=str(tmp_path / "cache"))
    _patch_build_app(monkeypatch, tmp_path, client)
    win = StubWindow(keys=[27])  # Esc, then ERR forever
    ok, app = tui.run_splash(win, config, client)
    assert ok is False
    assert app is None


def test_run_splash_abort_at_prompt(monkeypatch, tmp_path):
    client = _GateClient(state=LOCKED)
    config = Config(cache_dir=str(tmp_path / "cache"))
    _patch_build_app(monkeypatch, tmp_path, client)
    win = StubWindow(keys=[3])  # Ctrl-c at the lock-code prompt
    ok, app = tui.run_splash(win, config, client)
    assert ok is False
    assert app is None
    assert client.unlock_calls == []


def test_run_splash_failed_login_returns_false(monkeypatch, tmp_path):
    monkeypatch.delenv("BW_CLIENTID", raising=False)
    monkeypatch.delenv("BW_CLIENTSECRET", raising=False)
    client = _GateClient(state=LOGGED_OUT, login_error=BwError("invalid credentials"))
    config = Config(cache_dir=str(tmp_path / "cache"))
    _patch_build_app(monkeypatch, tmp_path, client)
    keys = [ord(c) for c in "me@x.eu"] + [10] + [ord(c) for c in "bad"] + [10]
    win = StubWindow(keys=keys)
    ok, app = tui.run_splash(win, config, client)
    assert ok is False
    assert app is None
    assert client.login_calls == [("me@x.eu", "bad")]


def test_run_splash_wrong_lock_code_reprompts(monkeypatch, tmp_path):
    client = _GateClient(state=LOCKED)

    def unlock(code):
        client.unlock_calls.append(code)
        if code == "0000":
            raise WrongPasswordError("wrong master password")
        client._state = UNLOCKED

    client.unlock_session = unlock
    config = Config(cache_dir=str(tmp_path / "cache"))
    _patch_build_app(monkeypatch, tmp_path, client)
    keys = (
        [ord(c) for c in "0000"] + [10]  # wrong code
        + [ord(c) for c in "1234"] + [10]  # right code
    )
    win = StubWindow(keys=keys)
    ok, app = tui.run_splash(win, config, client)
    assert ok is True
    assert client.unlock_calls == ["0000", "1234"]


def test_run_splash_probe_runs_in_worker_thread(monkeypatch, tmp_path):
    """The session probe must not block the splash: it runs in a
    worker thread while the splash keeps redrawing."""
    started = threading.Event()
    release = threading.Event()
    client = _GateClient(state=UNLOCKED)

    def slow_probe():
        started.set()
        release.wait(timeout=5)
        return UNLOCKED

    client.session_state = slow_probe
    config = Config(cache_dir=str(tmp_path / "cache"))
    _patch_build_app(monkeypatch, tmp_path, client)
    win = StubWindow()

    result = {}

    def boot():
        result["value"] = tui.run_splash(win, config, client)

    t = threading.Thread(target=boot)
    t.start()
    assert started.wait(timeout=2), "probe thread never started"
    # While the probe is blocked, the splash has redrawn at least once
    # and is still waiting (boot thread alive).
    assert t.is_alive()
    assert win.refreshed > 0
    release.set()
    t.join(timeout=5)
    assert not t.is_alive()
    ok, app = result["value"]
    assert ok is True
