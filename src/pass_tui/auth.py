"""Authentication gate for pass-tui.

Before the TUI starts, pass-cli must have a usable session. The gate
distinguishes three states:

- unlocked: the session answers API calls; pass-tui starts immediately.
- locked: a session exists but is protected by a pass-cli session lock
  (see https://protonpass.github.io/pass-cli/commands/session/). The
  user is prompted for the lock code (echo disabled) and pass-tui
  unlocks the session. This is the "type a PIN" flow.
- logged out: no session. The user is prompted for a Proton Pass
  personal access token
  (https://protonpass.github.io/pass-cli/get-started/configuration/#personal-access-tokens)
  and pass-cli is logged in with it.

Secrets (lock code, access token) are read with echo disabled and
handed to pass-cli over a pipe/pty from memory. They are never printed,
logged, passed via argv, or written to disk. Only the pass/fail outcome
is ever reported.
"""

from __future__ import annotations

import getpass
import sys
from typing import Callable

from .passcli import (
    SESSION_LOCKED,
    SESSION_UNLOCKED,
    PassCli,
    PassCliError,
    SessionLockedError,
)

_TOKEN_URL = (
    "https://protonpass.github.io/pass-cli/get-started/configuration/"
    "#personal-access-tokens"
)
_LOCK_URL = "https://protonpass.github.io/pass-cli/commands/session/"


class AuthAborted(Exception):
    """The user aborted a prompt (Esc/Ctrl-c/EOF)."""


def prompt_for_token(
    stream=None,
    input_fn: Callable[[str], str] = getpass.getpass,
) -> str:
    """Prompt for a personal access token, hidden (no echo).

    Returns the entered token. Raises AuthAborted when the user aborts
    (empty input is treated as "try again"; Ctrl-c/EOF abort).
    """
    stream = stream or sys.stderr
    while True:
        print(
            "pass-tui: pass-cli is not logged in.",
            file=stream,
        )
        print(
            "Run 'pass-cli login' to log in with your Proton account, or enter a",
            file=stream,
        )
        print(
            f"personal access token (created at {_TOKEN_URL}); note that",
            file=stream,
        )
        print(
            "token sessions expire after 2 hours and will ask again.",
            file=stream,
        )
        try:
            token = input_fn("Personal access token (pst_...::key, input hidden): ")
        except (EOFError, KeyboardInterrupt):
            raise AuthAborted() from None
        token = token.strip()
        if token:
            return token
        print("pass-tui: empty token, try again (Ctrl-c to abort).", file=stream)


def prompt_for_lock_code(
    stream=None,
    input_fn: Callable[[str], str] = getpass.getpass,
) -> str:
    """Prompt for the pass-cli session lock code, hidden (no echo).

    Returns the entered code. Raises AuthAborted when the user aborts
    (Ctrl-c/EOF); empty input re-prompts.
    """
    stream = stream or sys.stderr
    while True:
        print(
            "pass-tui: the pass-cli session is locked.",
            file=stream,
        )
        try:
            code = input_fn("Lock code (input hidden): ")
        except (EOFError, KeyboardInterrupt):
            raise AuthAborted() from None
        code = code.strip()
        if code:
            return code
        print("pass-tui: empty code, try again (Ctrl-c to abort).", file=stream)


def ensure_authenticated(
    client: PassCli,
    prompt_token: Callable[[], str] = prompt_for_token,
    prompt_lock: Callable[[], str] = prompt_for_lock_code,
    stream=None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Make sure pass-cli has a usable (unlocked) session.

    Returns True when pass-tui may start. Returns False when a login or
    unlock attempt failed (the caller should exit with an error
    message). Raises AuthAborted when the user aborts a prompt.
    """
    stream = stream or sys.stderr
    log = log or (lambda msg: None)
    state = client.session_state()
    if state == SESSION_UNLOCKED:
        log("pass-cli session valid")
        return True
    if state == SESSION_LOCKED:
        log("pass-cli session locked, prompting for the lock code")
        while True:
            code = prompt_lock()
            try:
                client.unlock_session(code)
            except SessionLockedError:
                print("pass-tui: wrong lock code, try again.", file=stream)
                log("wrong lock code")
                continue
            except PassCliError as exc:
                print(f"pass-tui: unlock failed: {exc}", file=stream)
                log(f"unlock failed: {exc}")
                return False
            if client.session_state() != SESSION_UNLOCKED:
                print(
                    "pass-tui: unlock did not establish a usable session.",
                    file=stream,
                )
                log("unlock succeeded but session check failed")
                return False
            log("session unlocked")
            return True
    log("pass-cli not authenticated, prompting for a personal access token")
    token = prompt_token()
    try:
        client.login_with_token(token)
    except PassCliError as exc:
        print(f"pass-tui: login failed: {exc}", file=stream)
        log(f"login failed: {exc}")
        return False
    if client.session_state() != SESSION_UNLOCKED:
        print(
            "pass-tui: login did not establish a session "
            "(token invalid or expired?).",
            file=stream,
        )
        log("login succeeded but session check failed")
        return False
    log("logged in with personal access token")
    return True
