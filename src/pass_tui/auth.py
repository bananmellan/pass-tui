"""Authentication gate for pass-tui (Bitwarden CLI, `bw`).

Before the TUI starts, bw must have an unlocked vault session. The gate
distinguishes three states (`bw status`):

- unlocked: a session key is active; pass-tui starts immediately.
- locked: logged in but the vault is locked. The user is prompted for
  the master password (echo disabled) and `bw unlock` runs with it.
- logged out: no bw login. If BW_CLIENTID + BW_CLIENTSECRET are set in
  the environment, the gate logs in with the API key (`bw login
  --apikey`) and falls through to the unlock prompt. Otherwise the
  user is prompted for the account email (visible) and master password
  (hidden), and `bw login <email>` runs with them.

Secrets (master password) are handed to bw through a child-only
environment variable (`--passwordenv`) from memory. They are never
printed, logged, passed via argv, or written to disk. The session key
bw prints is kept in memory and passed to later children as BW_SESSION.
Only the pass/fail outcome is ever reported.
"""

from __future__ import annotations

import getpass
import os
import sys
from typing import Callable

from .bitwarden import (
    SESSION_LOGGED_OUT,
    SESSION_LOCKED,
    SESSION_UNLOCKED,
    BwCli,
    BwError,
    WrongPasswordError,
)
_API_KEY_URL = "https://bitwarden.com/help/cli/#using-an-api-key"


class AuthAborted(Exception):
    """The user aborted a prompt (Esc/Ctrl-c/EOF)."""


def _say(stream, text: str) -> None:
    """Emit a gate message.

    *stream* is either a file-like object (headless: tests, non-TUI
    runs) or a callable (the splash UI, which renders the message as
    its status line).
    """
    if callable(stream):
        stream(text)
    else:
        print(text, file=stream)


def prompt_for_email(
    stream=None,
    input_fn: Callable[[str], str] = input,
) -> str:
    """Prompt for the Bitwarden account email (visible input).

    Returns the entered email. Raises AuthAborted when the user aborts
    (Ctrl-c/EOF); empty input re-prompts.
    """
    stream = stream or sys.stderr
    while True:
        print("pass-tui: bw is not logged in.", file=stream)
        try:
            email = input_fn("Bitwarden account email: ")
        except (EOFError, KeyboardInterrupt):
            raise AuthAborted() from None
        email = email.strip()
        if email:
            return email
        print("pass-tui: empty email, try again (Ctrl-c to abort).", file=stream)


def prompt_for_master_password(
    stream=None,
    input_fn: Callable[[str], str] = getpass.getpass,
) -> str:
    """Prompt for the master password, hidden (no echo).

    Returns the entered password. Raises AuthAborted when the user
    aborts (Ctrl-c/EOF); empty input re-prompts.
    """
    stream = stream or sys.stderr
    while True:
        print("pass-tui: the vault is locked.", file=stream)
        try:
            password = input_fn("Master password (input hidden): ")
        except (EOFError, KeyboardInterrupt):
            raise AuthAborted() from None
        password = password.strip()
        if password:
            return password
        print("pass-tui: empty password, try again (Ctrl-c to abort).", file=stream)


def has_api_key(env=None) -> bool:
    """True when BW_CLIENTID + BW_CLIENTSECRET are set in the environment,
    so `bw login --apikey` can run without prompting for credentials."""
    env = os.environ if env is None else env
    return bool(env.get("BW_CLIENTID") and env.get("BW_CLIENTSECRET"))


def ensure_authenticated(
    client: BwCli,
    prompt_email: Callable[[], str] = prompt_for_email,
    prompt_password: Callable[[], str] = prompt_for_master_password,
    stream=None,
    log: Callable[[str], None] | None = None,
    state: str | None = None,
    env=None,
) -> bool:
    """Make sure bw has an unlocked vault session.

    Returns True when pass-tui may start. Returns False when a login or
    unlock attempt failed (the caller should exit with an error
    message). Raises AuthAborted when the user aborts a prompt.

    ``state`` may carry a pre-probed session state (the splash probes
    in a worker thread so the boot UI stays responsive); when None the
    state is probed here.
    """
    stream = stream or sys.stderr
    log = log or (lambda msg: None)
    if state is None:
        state = client.session_state()
    if state == SESSION_UNLOCKED:
        log("bw session unlocked")
        return True
    if state == SESSION_LOGGED_OUT and has_api_key(env):
        # API key login (credentials come from the environment, not a
        # prompt); the vault stays locked afterwards, so continue to
        # the unlock prompt below.
        log("logging in with API key (BW_CLIENTID/BW_CLIENTSECRET)")
        try:
            client.login_with_api_key()
        except BwError as exc:
            _say(stream, f"pass-tui: API key login failed: {exc}")
            log(f"api key login failed: {exc}")
            return False
        state = client.session_state()
        if state == SESSION_UNLOCKED:
            log("bw session unlocked after API key login")
            return True
        if state != SESSION_LOCKED:
            _say(stream, "pass-tui: API key login did not establish a session.")
            log("api key login: unexpected state after login")
            return False
    if state == SESSION_LOCKED:
        log("vault locked, prompting for the master password")
        while True:
            password = prompt_password()
            try:
                client.unlock_session(password)
            except WrongPasswordError:
                _say(stream, "pass-tui: wrong master password, try again.")
                log("wrong master password")
                continue
            except BwError as exc:
                _say(stream, f"pass-tui: unlock failed: {exc}")
                log(f"unlock failed: {exc}")
                return False
            if client.session_state() != SESSION_UNLOCKED:
                _say(stream, "pass-tui: unlock did not establish a usable session.")
                log("unlock succeeded but session check failed")
                return False
            log("vault unlocked")
            return True
    # Logged out and no API key in the environment: email + master
    # password login.
    log("not logged in, prompting for email and master password")
    _say(
        stream,
        "pass-tui: no bw login found and no API key in the environment "
        f"(set BW_CLIENTID/BW_CLIENTSECRET, see {_API_KEY_URL}).",
    )
    email = prompt_email()
    while True:
        password = prompt_password()
        try:
            client.login(email, password)
        except WrongPasswordError:
            _say(stream, "pass-tui: wrong master password, try again.")
            log("wrong master password at login")
            continue
        except BwError as exc:
            _say(stream, f"pass-tui: login failed: {exc}")
            log(f"login failed: {exc}")
            return False
        if client.session_state() != SESSION_UNLOCKED:
            _say(
                stream,
                "pass-tui: login did not unlock the vault "
                "(2FA/SSO accounts: log in with `bw login` first, then start pass-tui).",
            )
            log("login succeeded but vault not unlocked")
            return False
        log("logged in and vault unlocked")
        return True
