from __future__ import annotations

import json
import os
import pty
import select
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable

from .dedup import dedup_refs
from .models import ItemRef, Vault


class PassCliError(RuntimeError):
    pass


class SessionLockedError(PassCliError):
    """The pass-cli session exists but is locked by a session lock."""


# Outcome of probing the pass-cli session state.
SESSION_UNLOCKED = "unlocked"  # session exists and answers API calls
SESSION_LOCKED = "locked"  # session exists but is locked (needs lock code)
SESSION_LOGGED_OUT = "logged_out"  # no session at all

# How long a locked-session probe may take before it is treated as
# logged out (a locked session fails fast, so this is generous).
_STATE_PROBE_TIMEOUT = 15.0

# Transcript cap for the pty used by session unlock; the lock code
# itself is never echoed by pass-cli (rpassword), so this only bounds
# prompt text.
_PTY_TRANSCRIPT_CAP = 8192


def _run(argv: list[str], timeout: float | None = None) -> str:
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PassCliError(f"executable not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PassCliError(f"timed out: {' '.join(argv)}") from exc
    stderr = proc.stderr.decode("utf-8", errors="replace")
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        detail = (stderr or stdout or "").strip().splitlines()
        raise PassCliError(detail[-1] if detail else f"{argv[0]} failed")
    return stdout


def _is_locked_error(exc: PassCliError) -> bool:
    text = str(exc).lower()
    return "sessionlocked" in text or "session is locked" in text or "locked session" in text


def _reap(pid: int) -> None:
    """Kill the child and anything it spawned, and wait until it is gone."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return
            if wpid == pid:
                return
            time.sleep(0.05)


def _run_pty(argv: list[str], code: str, timeout: float = 15.0) -> tuple[str, bool]:
    """Run pass-cli under a pty and answer its lock-code prompt.

    `pass-cli session unlock` reads the lock code from a real terminal
    (rpassword), so piping stdin fails with "No such device or address".
    The pty makes pass-cli see a terminal while the code is written to
    the pty master straight from memory — it never appears in argv,
    never in the transcript (pass-cli disables echo for the entry) and
    is never written to disk.

    Returns (transcript, timed_out). The transcript is capped and never
    contains the code.
    """
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)
        except OSError:
            os._exit(127)
    transcript = b""
    answered = False
    timed_out = False
    start = time.time()
    while time.time() - start < timeout:
        ready, _, _ = select.select([fd], [], [], 0.3)
        if fd in ready:
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            if len(transcript) < _PTY_TRANSCRIPT_CAP:
                transcript += chunk[: _PTY_TRANSCRIPT_CAP - len(transcript)]
            if not answered and b"lock code" in transcript.lower():
                try:
                    os.write(fd, (code + "\n").encode())
                except OSError:
                    pass
                answered = True
        if not os.path.exists(f"/proc/{pid}"):
            time.sleep(0.2)
            break
    else:
        timed_out = True
    _reap(pid)
    try:
        os.close(fd)
    except OSError:
        pass
    return transcript.decode("utf-8", errors="replace"), timed_out


def _run_json(argv: list[str], timeout: float | None = None) -> Any:
    out = _run(argv, timeout=timeout)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise PassCliError(f"invalid JSON from {' '.join(argv)}") from exc


def _as_list(data: Any, keys: Iterable[str]) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _scalar(data: dict, *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _nested(data: dict, *keys: str):
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def parse_vaults(data: Any) -> list[Vault]:
    vaults = []
    seen: set[str] = set()
    for raw in _as_list(data, ("vaults", "shares", "data", "items")):
        if not isinstance(raw, dict):
            continue
        share_id = _scalar(raw, "share_id", "shareId", "shareID", "id")
        if not share_id or share_id in seen:
            continue
        seen.add(share_id)
        name = _scalar(raw, "name", "title", "vault_name", "vaultName") or share_id
        vaults.append(Vault(share_id=share_id, name=name or "Unnamed vault"))
    return vaults


def _urls_from_entry(raw: dict) -> list[str]:
    """Extract urls from a pass-cli item entry, secrets aside.

    pass-cli serializes ItemContent as an externally-tagged enum:
    content.content = {"Login": {"urls": [...], "autofill_urls": [...], ...}}.
    autofill_urls entries are objects ({"url": ..., "mode": ...}), urls
    entries are plain strings. Older/alternate shapes (bare strings, a
    single "url" string) are tolerated.
    """
    urls: list[str] = []
    content = _nested(raw, "content") or _nested(raw, "item", "content") or {}
    if not isinstance(content, dict):
        return urls
    inner = content.get("content")
    sections: list = []
    if isinstance(inner, dict):
        sections = [inner]
    elif isinstance(inner, list):
        sections = [s for s in inner if isinstance(s, dict)]
    for section in sections:
        for value in section.values():
            if not isinstance(value, dict):
                continue
            for key in ("urls", "url", "autofill_urls", "autofillUrls"):
                candidate = value.get(key)
                entries = candidate if isinstance(candidate, list) else [candidate]
                for entry in entries:
                    if isinstance(entry, str) and entry:
                        urls.append(entry)
                    elif isinstance(entry, dict):
                        url = entry.get("url")
                        if isinstance(url, str) and url:
                            urls.append(url)
    return urls


def parse_items(data: Any, vault: Vault | None = None) -> list[ItemRef]:
    items = []
    seen: set[str] = set()
    for raw in _as_list(data, ("items", "data", "entries")):
        if not isinstance(raw, dict):
            continue
        if raw.get("state") == "Trashed":
            continue
        item_id = _scalar(raw, "item_id", "itemId", "itemID", "id")
        content = _nested(raw, "content") or _nested(raw, "item", "content") or {}
        title = _scalar(raw, "title") or _scalar(content, "title")
        if not item_id or not title:
            continue
        inner = content.get("content")
        if isinstance(inner, dict):
            item_type = (
                _scalar(raw, "type", "item_type", "itemType")
                or next(iter(inner.keys()), "")
                or "item"
            )
        else:
            item_type = _scalar(raw, "type", "item_type", "itemType") or "item"
        share_id = (
            _scalar(raw, "share_id", "shareId", "shareID", "vault_id", "vaultId")
            or (vault.share_id if vault else "")
        )
        vault_name = (
            _scalar(raw, "vault_name", "vaultName", "share_name")
            or (vault.name if vault else "")
        )
        share_id = share_id or ""
        item_uuid = _scalar(content, "item_uuid", "itemUuid") or ""
        # The same item can appear more than once in a vault listing (e.g.
        # pass-cli re-emits it when the vault response is paginated oddly, or
        # a shared item shows up in two shares). Dedup on item_id — Proton
        # Pass item ids are globally unique — so the list never shows
        # the same item twice.
        if item_id in seen:
            continue
        seen.add(item_id)
        items.append(
            ItemRef(
                share_id=share_id,
                item_id=item_id,
                title=title,
                urls=tuple(dict.fromkeys(_urls_from_entry(raw))),
                vault_name=vault_name or "",
                item_type=item_type,
                item_uuid=item_uuid,
            )
        )
    return items


def parse_item_view(data: Any) -> dict:
    if not isinstance(data, dict):
        raise PassCliError("item view returned a non-object")
    content = _nested(data, "item", "content") or _nested(data, "content") or data
    if not isinstance(content, dict) or "title" not in content:
        raise PassCliError("item view JSON has no content.title")
    return data


class PassCli:
    def __init__(
        self,
        binary: str = "pass-cli",
        runner: Callable[[list[str]], str] | None = None,
        timeout: float = 60.0,
        max_workers: int = 8,
        pty_runner: Callable[[list[str], str], tuple[str, bool]] | None = None,
    ) -> None:
        self.binary = binary
        self._runner = runner
        self.timeout = timeout
        self.max_workers = max_workers
        self._pty_runner = pty_runner or _run_pty

    def _exec(self, argv: list[str], timeout: float | None = None) -> str:
        if self._runner is not None:
            return self._runner(argv)
        return _run(argv, timeout=timeout if timeout is not None else self.timeout)

    def _exec_json(self, argv: list[str]) -> Any:
        out = self._exec(argv)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise PassCliError(f"invalid JSON from {' '.join(argv)}") from exc

    def is_authenticated(self) -> bool:
        """True when pass-cli has a session that is not locked.

        Kept for compatibility: True exactly when session_state() is
        SESSION_UNLOCKED.
        """
        return self.session_state() == SESSION_UNLOCKED

    def session_state(self) -> str:
        """Classify the pass-cli session: SESSION_UNLOCKED, SESSION_LOCKED
        or SESSION_LOGGED_OUT.

        `pass-cli info` succeeds whenever a session exists, locked or
        not (the lock is enforced server-side on API calls), so the real
        state comes from a cheap authenticated call, `vault list`. Its
        stderr distinguishes a locked session (SessionLocked) from a
        missing one. A missing pass-cli binary counts as logged out.
        """
        try:
            self._exec([self.binary, "info"], timeout=_STATE_PROBE_TIMEOUT)
        except PassCliError:
            return SESSION_LOGGED_OUT
        try:
            self._exec(
                [self.binary, "vault", "list", "--output", "json"],
                timeout=_STATE_PROBE_TIMEOUT,
            )
        except PassCliError as exc:
            if _is_locked_error(exc):
                return SESSION_LOCKED
            return SESSION_LOGGED_OUT
        return SESSION_UNLOCKED

    def unlock_session(self, code: str) -> None:
        """Unlock a locked session with its lock code.

        The code is typed by the user with echo off and handed to
        pass-cli over a pty — never via argv (visible in the process
        list) and never written to disk. Raises SessionLockedError when
        the code is wrong, PassCliError on other failures.
        """
        if not code:
            raise PassCliError("empty lock code")
        transcript, timed_out = self._pty_runner(
            [self.binary, "session", "unlock"], code
        )
        if timed_out:
            raise PassCliError("timed out waiting for pass-cli session unlock")
        lowered = transcript.lower()
        if "successfully" in lowered:
            return
        if "sessionlocked" in lowered or "wrong" in lowered or "invalid" in lowered:
            raise SessionLockedError("wrong lock code")
        last = transcript.strip().splitlines()
        raise PassCliError(last[-1] if last else "session unlock failed")

    def login_with_token(self, token: str) -> None:
        """Log in with a personal access token (pst_...::KEY).

        The token is passed via the command line, so it briefly appears
        in the process list of this machine; it is never written to disk
        by pass-cli and never logged by this tool.
        """
        self._exec([self.binary, "login", "--personal-access-token", token])

    def list_vaults(self) -> list[Vault]:
        data = self._exec_json([self.binary, "vault", "list", "--output", "json"])
        return parse_vaults(data)

    def list_items(self, vault: Vault) -> list[ItemRef]:
        # --show-secrets is required: without it pass-cli serializes bare
        # ItemSummary objects (title/id/state only, no content), so no urls
        # would be available for clipboard matching. Only metadata is kept.
        data = self._exec_json(
            [
                self.binary,
                "item",
                "list",
                "--share-id",
                vault.share_id,
                "--output",
                "json",
                "--show-secrets",
            ]
        )
        return parse_items(data, vault)

    def list_all_items(self) -> tuple[list[ItemRef], int]:
        """All items across all vaults, deduped.

        Returns (items, raw_count): raw_count is how many item entries
        pass-cli returned before dedup, so callers can log when the
        response contained duplicates.
        """
        vaults = self.list_vaults()
        if not vaults:
            data = self._exec_json(
                [self.binary, "item", "list", "--output", "json", "--show-secrets"]
            )
            parsed = parse_items(data)
            return parsed, len(parsed)
        results: list[ItemRef] = []
        errors: list[str] = []
        workers = max(1, min(self.max_workers, len(vaults)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Submit in vault order and collect in vault order (not
            # completion order) so the result is deterministic.
            futures = [pool.submit(self.list_items, vault) for vault in vaults]
            for future in futures:
                try:
                    results.extend(future.result())
                except PassCliError as exc:
                    errors.append(str(exc))
        if not results and errors:
            raise PassCliError("; ".join(errors))
        # A shared item can be visible through two shares; parse_items dedups
        # within one vault response, this dedups across vaults on item_id.
        raw_count = len(results)
        return dedup_refs(results), raw_count

    def view_item(self, ref: ItemRef) -> dict:
        data = self._exec_json(
            [
                self.binary,
                "item",
                "view",
                "--share-id",
                ref.share_id,
                "--item-id",
                ref.item_id,
                "--output",
                "json",
            ]
        )
        return parse_item_view(data)

    def view_item_with_retry(
        self, ref: ItemRef, attempts: int = 2, delay: float = 2.0
    ) -> dict:
        import time

        last: PassCliError | None = None
        for attempt in range(attempts):
            try:
                return self.view_item(ref)
            except PassCliError as exc:
                last = exc
                if attempt + 1 < attempts:
                    time.sleep(delay)
        raise last if last else PassCliError("item view failed")

    def totp(self, ref: ItemRef) -> str:
        out = self._exec([self.binary, "item", "view", f"pass://{ref.share_id}/{ref.item_id}/totp"])
        return out.strip()
