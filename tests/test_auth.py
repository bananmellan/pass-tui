"""Tests for the Bitwarden (bw) auth gate: unlocked passthrough, locked
-> master-password unlock with re-prompt on wrong password, logged out
-> API-key login from the environment or email + master password."""

import io

import pytest

from pass_tui.auth import (
    AuthAborted,
    ensure_authenticated,
    has_api_key,
    prompt_for_email,
    prompt_for_master_password,
)
from pass_tui.bitwarden import BwError, WrongPasswordError

UNLOCKED = "unlocked"
LOCKED = "locked"
LOGGED_OUT = "logged_out"

API_ENV = {"BW_CLIENTID": "user.abc", "BW_CLIENTSECRET": "secret123"}


class FakeClient:
    def __init__(self, state=LOGGED_OUT, login_error=None, unlock_error=None,
                 apikey_error=None, apikey_state=LOCKED):
        self._state = state
        self._login_error = login_error
        self._unlock_error = unlock_error
        self._apikey_error = apikey_error
        self._apikey_state = apikey_state
        self.login_calls = []
        self.unlock_calls = []
        self.apikey_calls = 0
        self.state_calls = 0

    def session_state(self):
        self.state_calls += 1
        return self._state

    def login(self, email, password):
        self.login_calls.append((email, password))
        if self._login_error is not None:
            raise self._login_error
        self._state = UNLOCKED

    def login_with_api_key(self):
        self.apikey_calls += 1
        if self._apikey_error is not None:
            raise self._apikey_error
        self._state = self._apikey_state

    def unlock_session(self, password):
        self.unlock_calls.append(password)
        if self._unlock_error is not None:
            raise self._unlock_error
        self._state = UNLOCKED


# ------------------------------------------------------------ prompts


def test_prompt_for_email_returns_stripped_email():
    stream = io.StringIO()
    assert prompt_for_email(stream=stream, input_fn=lambda _p: "  me@bitwarden.eu  ") == "me@bitwarden.eu"
    assert "not logged in" in stream.getvalue()


def test_prompt_for_email_abort():
    def boom(_p):
        raise EOFError
    with pytest.raises(AuthAborted):
        prompt_for_email(stream=io.StringIO(), input_fn=boom)


def test_prompt_for_master_password_hidden():
    stream = io.StringIO()
    assert prompt_for_master_password(stream=stream, input_fn=lambda _p: "hunter2") == "hunter2"
    # The prompt text is printed, but the password never is.
    assert "hunter2" not in stream.getvalue()


def test_prompt_for_master_password_empty_reprompts():
    answers = iter(["", "hunter2"])
    assert prompt_for_master_password(stream=io.StringIO(), input_fn=lambda _p: next(answers)) == "hunter2"


def test_has_api_key():
    assert has_api_key(API_ENV) is True
    assert has_api_key({"BW_CLIENTID": "x"}) is False
    assert has_api_key({}) is False


# ------------------------------------------------------------ gate


def test_ensure_authenticated_passthrough_when_unlocked():
    client = FakeClient(state=UNLOCKED)
    logs = []
    assert ensure_authenticated(client, log=logs.append, env={}) is True
    assert client.login_calls == []
    assert client.unlock_calls == []
    assert client.apikey_calls == 0
    assert any("unlocked" in m for m in logs)


def test_ensure_authenticated_probes_when_no_state_given():
    client = FakeClient(state=UNLOCKED)
    assert ensure_authenticated(client, env={}) is True
    assert client.state_calls == 1


def test_ensure_authenticated_accepts_preprobed_state():
    client = FakeClient(state=UNLOCKED)
    assert ensure_authenticated(client, state=UNLOCKED, env={}) is True
    assert client.state_calls == 0


def test_ensure_authenticated_unlocks_locked_session():
    client = FakeClient(state=LOCKED)
    stream = io.StringIO()
    assert ensure_authenticated(
        client, prompt_password=lambda: "hunter2", stream=stream, env={}
    ) is True
    assert client.unlock_calls == ["hunter2"]
    assert client.login_calls == []
    # The master password must never be printed to the stream.
    assert "hunter2" not in stream.getvalue()


def test_ensure_authenticated_reprompts_on_wrong_password():
    client = FakeClient(state=LOCKED)

    def unlock(password):
        client.unlock_calls.append(password)
        if password == "wrong":
            raise WrongPasswordError("wrong master password")
        client._state = UNLOCKED

    client.unlock_session = unlock
    passwords = iter(["wrong", "hunter2"])
    stream = io.StringIO()
    assert ensure_authenticated(
        client, prompt_password=lambda: next(passwords), stream=stream, env={}
    ) is True
    assert client.unlock_calls == ["wrong", "hunter2"]
    assert "wrong master password" in stream.getvalue()
    assert "wrong" not in stream.getvalue().replace("wrong master password", "")
    assert "hunter2" not in stream.getvalue()


def test_ensure_authenticated_unlock_failure_returns_false():
    client = FakeClient(state=LOCKED, unlock_error=BwError("boom"))
    stream = io.StringIO()
    assert ensure_authenticated(
        client, prompt_password=lambda: "hunter2", stream=stream, env={}
    ) is False
    assert "unlock failed" in stream.getvalue()


def test_ensure_authenticated_unlock_succeeds_but_state_still_bad():
    client = FakeClient(state=LOCKED)
    client.unlock_session = lambda password: None  # no error, but stays locked
    stream = io.StringIO()
    assert ensure_authenticated(
        client, prompt_password=lambda: "hunter2", stream=stream, env={}
    ) is False
    assert "did not establish a usable session" in stream.getvalue()


def test_ensure_authenticated_locked_prompt_abort_propagates():
    client = FakeClient(state=LOCKED)

    def abort():
        raise AuthAborted()

    with pytest.raises(AuthAborted):
        ensure_authenticated(client, prompt_password=abort, env={})
    assert client.unlock_calls == []


def test_ensure_authenticated_logged_out_logs_in_with_email_and_password():
    client = FakeClient(state=LOGGED_OUT)
    stream = io.StringIO()
    assert ensure_authenticated(
        client,
        prompt_email=lambda: "me@bitwarden.eu",
        prompt_password=lambda: "hunter2",
        stream=stream,
        env={},
    ) is True
    assert client.login_calls == [("me@bitwarden.eu", "hunter2")]
    assert client.unlock_calls == []
    assert client.apikey_calls == 0
    # Neither email nor password leaks into the gate messages beyond the
    # non-secret email being fine to show; the password never appears.
    assert "hunter2" not in stream.getvalue()


def test_ensure_authenticated_login_wrong_password_reprompts():
    client = FakeClient(state=LOGGED_OUT)

    def login(email, password):
        client.login_calls.append((email, password))
        if password == "wrong":
            raise WrongPasswordError("wrong master password")
        client._state = UNLOCKED

    client.login = login
    passwords = iter(["wrong", "hunter2"])
    stream = io.StringIO()
    assert ensure_authenticated(
        client,
        prompt_email=lambda: "me@bitwarden.eu",
        prompt_password=lambda: next(passwords),
        stream=stream,
        env={},
    ) is True
    assert [p for _e, p in client.login_calls] == ["wrong", "hunter2"]
    assert "hunter2" not in stream.getvalue()


def test_ensure_authenticated_login_failure_returns_false():
    client = FakeClient(state=LOGGED_OUT, login_error=BwError("two-step login required"))
    stream = io.StringIO()
    assert ensure_authenticated(
        client,
        prompt_email=lambda: "me@bitwarden.eu",
        prompt_password=lambda: "hunter2",
        stream=stream,
        env={},
    ) is False
    assert "login failed" in stream.getvalue()
    assert "hunter2" not in stream.getvalue()


def test_ensure_authenticated_session_check_after_login_fails():
    client = FakeClient(state=LOGGED_OUT)

    def login(email, password):
        client.login_calls.append((email, password))
        # login "succeeds" but the vault never becomes unlocked

    client.login = login
    stream = io.StringIO()
    assert ensure_authenticated(
        client,
        prompt_email=lambda: "me@bitwarden.eu",
        prompt_password=lambda: "hunter2",
        stream=stream,
        env={},
    ) is False
    assert "did not unlock" in stream.getvalue()


def test_ensure_authenticated_logged_out_with_api_key_env():
    """BW_CLIENTID/BW_CLIENTSECRET in the environment: API key login, then
    the vault is locked and the master password prompt unlocks it."""
    client = FakeClient(state=LOGGED_OUT, apikey_state=LOCKED)
    stream = io.StringIO()
    assert ensure_authenticated(
        client,
        prompt_password=lambda: "hunter2",
        stream=stream,
        env=API_ENV,
    ) is True
    assert client.apikey_calls == 1
    assert client.login_calls == []  # no email/password login
    assert client.unlock_calls == ["hunter2"]
    assert "hunter2" not in stream.getvalue()


def test_ensure_authenticated_api_key_login_failure_returns_false():
    client = FakeClient(state=LOGGED_OUT, apikey_error=BwError("invalid api key"))
    stream = io.StringIO()
    assert ensure_authenticated(
        client, prompt_password=lambda: "unused", stream=stream, env=API_ENV
    ) is False
    assert "API key login failed" in stream.getvalue()
    assert client.unlock_calls == []
