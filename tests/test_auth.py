import io

import pytest

from pass_tui.auth import AuthAborted, ensure_authenticated, prompt_for_lock_code, prompt_for_token
from pass_tui.passcli import PassCliError, SessionLockedError

UNLOCKED = "unlocked"
LOCKED = "locked"
LOGGED_OUT = "logged_out"


class FakeClient:
    def __init__(self, state=LOGGED_OUT, login_error=None, unlock_error=None):
        self._state = state
        self._login_error = login_error
        self._unlock_error = unlock_error
        self.login_calls = []
        self.unlock_calls = []
        self.state_calls = 0

    def session_state(self):
        self.state_calls += 1
        return self._state

    def login_with_token(self, token):
        self.login_calls.append(token)
        if self._login_error is not None:
            raise self._login_error
        self._state = UNLOCKED

    def unlock_session(self, code):
        self.unlock_calls.append(code)
        if self._unlock_error is not None:
            raise self._unlock_error
        self._state = UNLOCKED


def test_ensure_authenticated_passthrough_when_unlocked():
    client = FakeClient(state=UNLOCKED)
    logs = []
    assert ensure_authenticated(client, prompt_token=lambda: "unused",
                              prompt_lock=lambda: "unused", log=logs.append) is True
    assert client.login_calls == []
    assert client.unlock_calls == []
    assert any("session valid" in m for m in logs)


def test_ensure_authenticated_unlocks_locked_session():
    client = FakeClient(state=LOCKED)
    stream = io.StringIO()
    assert ensure_authenticated(client, prompt_token=lambda: "unused",
                              prompt_lock=lambda: "1234", stream=stream) is True
    assert client.unlock_calls == ["1234"]
    assert client.login_calls == []
    # The lock code must never be printed to the stream.
    assert "1234" not in stream.getvalue()


def test_ensure_authenticated_reprompts_on_wrong_lock_code():
    client = FakeClient(state=LOCKED, unlock_error=SessionLockedError("wrong lock code"))
    codes = iter(["0000", "1234"])
    stream = io.StringIO()
    # First code is wrong; the client stays locked until the second call.
    def unlock(code):
        client.unlock_calls.append(code)
        if code == "0000":
            raise SessionLockedError("wrong lock code")
        client._state = UNLOCKED
    client.unlock_session = unlock
    assert ensure_authenticated(client, prompt_lock=lambda: next(codes), stream=stream) is True
    assert client.unlock_calls == ["0000", "1234"]
    assert "wrong lock code" in stream.getvalue()
    assert "0000" not in stream.getvalue()


def test_ensure_authenticated_unlock_failure_returns_false():
    client = FakeClient(state=LOCKED, unlock_error=PassCliError("boom"))
    stream = io.StringIO()
    assert ensure_authenticated(client, prompt_lock=lambda: "1234", stream=stream) is False
    assert "unlock failed" in stream.getvalue()


def test_ensure_authenticated_unlock_succeeds_but_state_still_bad():
    client = FakeClient(state=LOCKED)
    client.unlock_session = lambda code: None  # no error, but stays locked
    stream = io.StringIO()
    assert ensure_authenticated(client, prompt_lock=lambda: "1234", stream=stream) is False
    assert "did not establish a usable session" in stream.getvalue()


def test_ensure_authenticated_locked_prompt_abort_propagates():
    client = FakeClient(state=LOCKED)

    def abort():
        raise AuthAborted()

    with pytest.raises(AuthAborted):
        ensure_authenticated(client, prompt_lock=abort)
    assert client.unlock_calls == []


def test_ensure_authenticated_prompts_token_when_logged_out():
    client = FakeClient(state=LOGGED_OUT)
    stream = io.StringIO()
    assert ensure_authenticated(client, prompt_token=lambda: "pst_abc::key",
                              stream=stream) is True
    assert client.login_calls == ["pst_abc::key"]
    assert client.unlock_calls == []
    # The token must never be printed to the stream.
    assert "pst_abc" not in stream.getvalue()


def test_ensure_authenticated_login_failure_returns_false():
    client = FakeClient(state=LOGGED_OUT, login_error=PassCliError("invalid token"))
    stream = io.StringIO()
    assert ensure_authenticated(client, prompt_token=lambda: "pst_bad", stream=stream) is False
    assert "login failed" in stream.getvalue()
    assert "pst_bad" not in stream.getvalue()


def test_ensure_authenticated_session_check_after_login_fails():
    client = FakeClient(state=LOGGED_OUT)

    def login(token):
        client.login_calls.append(token)
        # login "succeeds" but the session never becomes usable

    client.login_with_token = login
    stream = io.StringIO()
    assert ensure_authenticated(client, prompt_token=lambda: "pst_x", stream=stream) is False
    assert "did not establish a session" in stream.getvalue()


def test_ensure_authenticated_token_prompt_abort_propagates():
    client = FakeClient(state=LOGGED_OUT)

    def abort():
        raise AuthAborted()

    with pytest.raises(AuthAborted):
        ensure_authenticated(client, prompt_token=abort)
    assert client.login_calls == []


def test_prompt_for_token_returns_stripped_token():
    stream = io.StringIO()
    inputs = iter(["  pst_abc::key\n"])
    token = prompt_for_token(stream=stream, input_fn=lambda prompt: next(inputs))
    assert token == "pst_abc::key"
    # The prompt mentions the token docs URL but never echoes the token.
    assert "personal-access-tokens" in stream.getvalue()


def test_prompt_for_token_retries_on_empty_input():
    stream = io.StringIO()
    inputs = iter(["", "   ", "pst_real::key"])
    token = prompt_for_token(stream=stream, input_fn=lambda prompt: next(inputs))
    assert token == "pst_real::key"
    assert "try again" in stream.getvalue()


def test_prompt_for_token_eof_aborts():
    stream = io.StringIO()

    def eof(prompt):
        raise EOFError

    with pytest.raises(AuthAborted):
        prompt_for_token(stream=stream, input_fn=eof)


def test_prompt_for_token_keyboard_interrupt_aborts():
    stream = io.StringIO()

    def interrupt(prompt):
        raise KeyboardInterrupt

    with pytest.raises(AuthAborted):
        prompt_for_token(stream=stream, input_fn=interrupt)


def test_prompt_for_lock_code_returns_stripped_code():
    stream = io.StringIO()
    inputs = iter(["  1234\n"])
    code = prompt_for_lock_code(stream=stream, input_fn=lambda prompt: next(inputs))
    assert code == "1234"
    assert "locked" in stream.getvalue()


def test_prompt_for_lock_code_retries_on_empty_input():
    stream = io.StringIO()
    inputs = iter(["", "4321"])
    code = prompt_for_lock_code(stream=stream, input_fn=lambda prompt: next(inputs))
    assert code == "4321"
    assert "try again" in stream.getvalue()


def test_prompt_for_lock_code_eof_aborts():
    stream = io.StringIO()

    def eof(prompt):
        raise EOFError

    with pytest.raises(AuthAborted):
        prompt_for_lock_code(stream=stream, input_fn=eof)


# ------------------------------------------------------------ setup_session_locks

from pass_tui.auth import setup_session_locks
from pass_tui.config import Config


class LockClient(FakeClient):
    def __init__(self, has_lock=False, create_error=None, **kwargs):
        super().__init__(state=UNLOCKED, **kwargs)
        self._has_lock = has_lock
        self._create_error = create_error
        self.create_calls = []

    def session_has_lock(self):
        return self._has_lock

    def create_lock(self, code, idle_timeout):
        self.create_calls.append((code, idle_timeout))
        if self._create_error is not None:
            raise self._create_error


def test_setup_session_locks_creates_lock_with_configured_timeout():
    client = LockClient()
    setup_session_locks(client, Config(lock_idle_timeout=600), prompt_lock=lambda: "1234")
    assert client.create_calls == [("1234", 600)]


def test_setup_session_locks_skips_when_lock_exists():
    client = LockClient(has_lock=True)
    called = []
    setup_session_locks(
        client,
        Config(lock_idle_timeout=600),
        prompt_lock=lambda: called.append(1) or "1234",
    )
    assert client.create_calls == []
    assert called == []


def test_setup_session_locks_disabled_when_timeout_zero():
    client = LockClient()
    called = []
    setup_session_locks(
        client,
        Config(lock_idle_timeout=0),
        prompt_lock=lambda: called.append(1) or "1234",
    )
    assert client.create_calls == []
    assert called == []


def test_setup_session_locks_abort_skips():
    client = LockClient()

    def abort():
        raise AuthAborted()

    setup_session_locks(client, Config(lock_idle_timeout=600), prompt_lock=abort)
    assert client.create_calls == []


def test_setup_session_locks_create_failure_is_not_fatal():
    """PAT sessions cannot have a lock; pass-cli refuses. Setup must
    swallow that and leave the session usable."""
    client = LockClient(create_error=PassCliError("PAT sessions cannot have a lock"))
    stream = io.StringIO()
    setup_session_locks(
        client, Config(lock_idle_timeout=600), prompt_lock=lambda: "1234", stream=stream
    )
    assert client.create_calls == [("1234", 600)]


def test_setup_session_locks_lock_code_not_printed():
    client = LockClient()
    stream = io.StringIO()
    setup_session_locks(
        client, Config(lock_idle_timeout=600), prompt_lock=lambda: "1234", stream=stream
    )
    assert "1234" not in stream.getvalue()
