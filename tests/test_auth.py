import io

import pytest

from pass_tui.auth import AuthAborted, ensure_authenticated, prompt_for_token
from pass_tui.passcli import PassCliError


class FakeClient:
    def __init__(self, authenticated=False, login_error=None, check_after_login=True):
        self._authenticated = authenticated
        self._login_error = login_error
        self._check_after_login = check_after_login
        self.login_calls = []
        self.info_calls = 0

    def is_authenticated(self):
        self.info_calls += 1
        return self._authenticated

    def login_with_token(self, token):
        self.login_calls.append(token)
        if self._login_error is not None:
            raise self._login_error
        if self._check_after_login:
            self._authenticated = True


def test_ensure_authenticated_passthrough_when_session_valid():
    client = FakeClient(authenticated=True)
    logs = []
    assert ensure_authenticated(client, prompt=lambda: "unused", log=logs.append) is True
    assert client.login_calls == []
    assert client.info_calls == 1
    assert any("session valid" in m for m in logs)


def test_ensure_authenticated_prompts_and_logs_in():
    client = FakeClient(authenticated=False)
    stream = io.StringIO()
    assert ensure_authenticated(client, prompt=lambda: "pst_abc::key", stream=stream) is True
    assert client.login_calls == ["pst_abc::key"]
    # The token must never be printed to the stream.
    assert "pst_abc" not in stream.getvalue()
    # One check before login, one after.
    assert client.info_calls == 2


def test_ensure_authenticated_login_failure_returns_false():
    client = FakeClient(authenticated=False, login_error=PassCliError("invalid token"))
    stream = io.StringIO()
    assert ensure_authenticated(client, prompt=lambda: "pst_bad", stream=stream) is False
    assert "login failed" in stream.getvalue()
    assert "pst_bad" not in stream.getvalue()


def test_ensure_authenticated_session_check_after_login_fails():
    client = FakeClient(authenticated=False, check_after_login=False)
    stream = io.StringIO()
    assert ensure_authenticated(client, prompt=lambda: "pst_x", stream=stream) is False
    assert "did not establish a session" in stream.getvalue()


def test_ensure_authenticated_prompt_abort_propagates():
    client = FakeClient(authenticated=False)

    def abort():
        raise AuthAborted()

    with pytest.raises(AuthAborted):
        ensure_authenticated(client, prompt=abort)
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
