"""Authentication gate for pass-tui.

Before the TUI starts, pass-cli must have a valid session. When it does
not, the user is prompted for a Proton Pass personal access token
(https://protonpass.github.io/pass-cli/get-started/configuration/#personal-access-tokens)
and pass-cli is logged in with it (`pass-cli login
--personal-access-token <token>`).

The token is read with echo disabled and never printed, logged or
written to disk. Only the pass/fail outcome is ever reported.
"""

from __future__ import annotations

import getpass
import sys
from typing import Callable

from .passcli import PassCli, PassCliError

_TOKEN_URL = (
    "https://protonpass.github.io/pass-cli/get-started/configuration/"
    "#personal-access-tokens"
)


class AuthAborted(Exception):
    """The user aborted the token prompt (Esc/Ctrl-c/EOF)."""


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
            f"Create a personal access token at {_TOKEN_URL}",
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


def ensure_authenticated(
    client: PassCli,
    prompt: Callable[[], str] = prompt_for_token,
    stream=None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Make sure pass-cli has a session, prompting for a token if not.

    Returns True when authenticated. Returns False when the login
    attempt failed (the caller should exit with an error message).
    Raises AuthAborted when the user aborts the prompt.
    """
    stream = stream or sys.stderr
    log = log or (lambda msg: None)
    if client.is_authenticated():
        log("pass-cli session valid")
        return True
    log("pass-cli not authenticated, prompting for a personal access token")
    token = prompt()
    try:
        client.login_with_token(token)
    except PassCliError as exc:
        print(f"pass-tui: login failed: {exc}", file=stream)
        log(f"login failed: {exc}")
        return False
    if not client.is_authenticated():
        print(
            "pass-tui: login did not establish a session "
            "(token invalid or expired?).",
            file=stream,
        )
        log("login succeeded but session check failed")
        return False
    log("logged in with personal access token")
    return True
