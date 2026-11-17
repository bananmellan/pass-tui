"""Bitwarden CLI (``bw``) backend for pass-tui.

The TUI talks to the official Bitwarden CLI, pointed at the EU cloud
(``https://bitwarden.eu`` by default, see :class:`BwCli.ensure_server`).

Session model: ``bw status`` reports ``unlocked`` / ``locked`` /
``unauthenticated``. Unlocking (``bw unlock --raw``) or logging in with
the master password (``bw login <email> --raw``) prints a *session key*
to stdout; the key is kept in memory only and injected into every child
as the ``BW_SESSION`` environment variable — never via argv (visible in
the process list) and never written to disk. The master password itself
is handed to ``bw`` via a child-only environment variable
(``--passwordenv``), also never via argv and never on disk.

Vault data: ``bw list items`` returns the decrypted items (the session
key decrypts locally) as a bare JSON array; each item has ``id``,
``organizationId`` (null = personal vault), ``folderId``, ``type``
(1=login, 2=secure note, 3=card, 4=identity), ``name``, ``notes``,
``login`` (with ``uris``/``username``/``password``/``totp``), ``card``,
``identity``, ``secureNote`` and custom ``fields``. ``bw list folders``
returns ``[{"id", "name"}]`` (nested folders are encoded in the name with
``/``); ``bw list organizations`` returns ``[{"id", "name"}]`` for the
vault (organization) names. ``bw get item <id>`` returns one item,
``bw get totp <id>`` the current TOTP code.

Items are adapted to the internal item-view shape that
:mod:`pass_tui.fields` understands (see :func:`bw_item_to_view`), so the
field menu, hidden-field censoring and the never-cache-secrets rule all
work unchanged. Secrets are still fetched live at copy time
(:meth:`BwCli.view_item`), the cache only stores non-sensitive fields.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Callable, Iterable

from .dedup import dedup_refs
from .fields import safe_fields_from_view
from .models import ItemRef

# Default Bitwarden cloud region (EU).
DEFAULT_SERVER = "https://bitwarden.eu"


class BwError(RuntimeError):
    pass


class WrongPasswordError(BwError):
    """The master password was rejected."""


# Outcome of probing the bw session state (bw status .status).
SESSION_UNLOCKED = "unlocked"  # logged in and vault unlocked (BW_SESSION works)
SESSION_LOCKED = "locked"  # logged in, vault locked (needs master password)
SESSION_LOGGED_OUT = "logged_out"  # no bw login at all

# How long a status probe may take before it is treated as logged out.
_STATE_PROBE_TIMEOUT = 15.0

# Child-only environment variable that carries the master password to
# `bw unlock`/`bw login` via --passwordenv. Never argv, never disk.
_MP_ENV = "_PASS_TUI_BW_MASTER_PASSWORD"

# bw item `type` enum -> canonical type name used in the cache/UI.
_TYPE_NAMES = {1: "Login", 2: "Note", 3: "CreditCard", 4: "Identity"}

# Vault label for items without an organizationId.
PERSONAL_VAULT_NAME = "My Vault"


def _run(argv: list[str], timeout: float | None = None, env: dict | None = None) -> str:
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BwError(f"executable not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BwError(f"timed out: {' '.join(argv)}") from exc
    stderr = proc.stderr.decode("utf-8", errors="replace")
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        detail = (stderr or stdout or "").strip().splitlines()
        raise BwError(detail[-1] if detail else f"{argv[0]} failed")
    return stdout


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


def _is_wrong_password(exc: BwError) -> bool:
    return "invalid master password" in str(exc).lower()


def _bw_uris(raw: dict) -> list[str]:
    """Login uris of a bw item: login.uris[] entries are {"uri": ...}
    objects (plain strings tolerated), plus a bare login.url."""
    urls: list[str] = []
    login = raw.get("login")
    if isinstance(login, dict):
        for entry in login.get("uris") or []:
            if isinstance(entry, dict) and entry.get("uri"):
                urls.append(str(entry["uri"]))
            elif isinstance(entry, str) and entry:
                urls.append(entry)
        if login.get("url"):
            urls.append(str(login["url"]))
    return urls


def bw_item_to_view(item: dict) -> dict:
    """Adapt a decrypted bw item to the internal item-view shape that
    :mod:`pass_tui.fields` understands::

        {"item": {"content": {"title", "note",
                              "content": {<Type>: {...}},
                              "extra_fields": [...]}}}

    bw item type 1 -> "Login" (username/password/urls/totp_uri),
    2 -> "Note", 3 -> "CreditCard" (keys chosen so the hidden-field
    detection in fields.py censors number/cvv), 4 -> "Identity",
    anything else -> "Custom". Custom fields (fields[]) become extra
    fields, hidden ones (type 1) as {"Hidden": value}.
    """
    if not isinstance(item, dict):
        raise BwError("bw get item returned a non-object")
    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise BwError("bw item has no name")
    notes = item.get("notes")
    content: dict[str, Any] = {
        "title": name,
        "note": notes if isinstance(notes, str) else "",
    }
    section: dict[str, Any] = {}
    itype = item.get("type")
    if itype == 1:
        login = item.get("login") if isinstance(item.get("login"), dict) else {}
        section["Login"] = {
            "username": login.get("username") or "",
            "password": login.get("password") or "",
            "urls": _bw_uris(item),
            "totp_uri": login.get("totp") or "",
        }
    elif itype == 2:
        section["Note"] = {}
    elif itype == 3:
        card = item.get("card") if isinstance(item.get("card"), dict) else {}
        section["CreditCard"] = {
            "cardholderName": card.get("cardholderName") or "",
            "brand": card.get("brand") or "",
            "cardNumber": card.get("number") or "",
            "expMonth": "" if card.get("expMonth") is None else str(card.get("expMonth")),
            "expYear": "" if card.get("expYear") is None else str(card.get("expYear")),
            "cvv": card.get("code") or "",
        }
    elif itype == 4:
        identity = item.get("identity") if isinstance(item.get("identity"), dict) else {}
        section["Identity"] = {
            key: ("" if value is None else str(value))
            for key, value in identity.items()
            if isinstance(value, (str, int)) and not isinstance(value, bool)
        }
    else:
        section["Custom"] = {}
    content["content"] = section
    extra_fields = []
    fields = item.get("fields")
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            value = field.get("value")
            kind = "Hidden" if field.get("type") == 1 else "Text"
            extra_fields.append(
                {"name": str(field["name"]), "content": {kind: "" if value is None else str(value)}}
            )
    content["extra_fields"] = extra_fields
    return {"item": {"content": content}}


def parse_folders(data: Any) -> dict[str, str]:
    """Map folder id -> name from `bw list folders` (a bare array of
    {"id", "name", ...}; the empty-id "No Folder" pseudo-entry is
    skipped). Nested folders are already encoded in the name ("a/b")."""
    folders: dict[str, str] = {}
    for raw in _as_list(data, ("folders",)):
        if not isinstance(raw, dict):
            continue
        folder_id = _scalar(raw, "id")
        if not folder_id:
            continue
        name = _scalar(raw, "name") or ""
        folders[folder_id] = name
    return folders


def parse_organizations(data: Any) -> dict[str, str]:
    """Map organization id -> name from `bw list organizations`."""
    orgs: dict[str, str] = {}
    for raw in _as_list(data, ("organizations", "orgs")):
        if not isinstance(raw, dict):
            continue
        org_id = _scalar(raw, "id")
        if not org_id:
            continue
        orgs[org_id] = _scalar(raw, "name") or org_id
    return orgs


def parse_items(
    data: Any,
    folder_names: dict[str, str] | None = None,
    org_names: dict[str, str] | None = None,
) -> list[ItemRef]:
    """Parse `bw list items` output into ItemRefs.

    Trashed items (deletedDate set) are skipped. The vault name comes
    from the organization map (personal vault = "My Vault"); the folder
    from the folder map. Non-sensitive fields are captured via the same
    rule as before (secrets are dropped, never cached).
    """
    items: list[ItemRef] = []
    seen: set[str] = set()
    for raw in _as_list(data, ("items", "data")):
        if not isinstance(raw, dict):
            continue
        if raw.get("deletedDate"):
            continue  # in the trash
        item_id = _scalar(raw, "id")
        title = _scalar(raw, "name", "title")
        if not item_id or not title:
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        org_id = _scalar(raw, "organizationId") or ""
        if org_id:
            vault_name = (org_names or {}).get(org_id) or "Organization"
        else:
            vault_name = PERSONAL_VAULT_NAME
        folder_id = _scalar(raw, "folderId") or ""
        folder = (folder_names or {}).get(folder_id, "") if folder_id else ""
        itype = raw.get("type")
        if isinstance(itype, bool):
            item_type = ""
        elif isinstance(itype, int):
            item_type = _TYPE_NAMES.get(itype, "")
        else:
            item_type = _scalar(raw, "item_type") or ""
        safe = safe_fields_from_view(bw_item_to_view(raw))
        items.append(
            ItemRef(
                share_id=org_id,
                item_id=item_id,
                title=title,
                urls=tuple(dict.fromkeys(_bw_uris(raw))),
                vault_name=vault_name,
                item_type=item_type,
                item_uuid=item_id,  # bw has no separate uuid; id is the identity
                folder=folder,
                safe_fields=tuple(safe.items()),
            )
        )
    return items


class BwCli:
    def __init__(
        self,
        binary: str = "bw",
        runner: Callable[[list[str]], str] | None = None,
        timeout: float = 60.0,
        server: str = DEFAULT_SERVER,
    ) -> None:
        self.binary = binary
        self._runner = runner
        self.timeout = timeout
        self.server = server
        # Session key, held in memory only; injected into every child as
        # BW_SESSION (never argv, never disk).
        self._session: str | None = None

    # ------------------------------------------------------------ plumbing

    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        if self._session:
            env["BW_SESSION"] = self._session
        if extra:
            env.update(extra)
        return env

    def _exec(
        self,
        argv: list[str],
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        if self._runner is not None:
            return self._runner(argv)
        return _run(
            argv,
            timeout=timeout if timeout is not None else self.timeout,
            env=env if env is not None else self._env(),
        )

    def _exec_json(self, argv: list[str], timeout: float | None = None) -> Any:
        out = self._exec(argv, timeout=timeout)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise BwError(f"invalid JSON from {' '.join(argv)}") from exc

    def _secret_exec(self, argv: list[str], secret: str) -> str:
        """Run argv with the secret in a child-only env var referenced by
        --passwordenv: the secret never appears in argv or on disk."""
        return self._exec(
            argv + ["--passwordenv", _MP_ENV],
            env=self._env({_MP_ENV: secret}),
        )

    @staticmethod
    def _session_key_from(out: str) -> str:
        lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
        return lines[-1] if lines else ""

    # ------------------------------------------------------------ server

    def ensure_server(self, url: str | None = None) -> None:
        """Point bw at the expected server (default bitwarden.eu).

        `bw config server <url>` persists the setting; we refuse to go
        on if the configured server is not the expected one, so the
        master password is never sent to the wrong region.
        """
        url = url or self.server
        if not url:
            return
        try:
            data = self._exec_json([self.binary, "status"])
        except BwError:
            data = None
        if isinstance(data, dict) and data.get("serverUrl") == url:
            return
        self._exec([self.binary, "config", "server", url])
        data = self._exec_json([self.binary, "status"])
        if not (isinstance(data, dict) and data.get("serverUrl") == url):
            raise BwError(f"bw is not configured for server {url}")

    # ------------------------------------------------------------ session

    def session_state(self) -> str:
        """Classify the bw session via `bw status` (needs no session key):
        SESSION_UNLOCKED / SESSION_LOCKED / SESSION_LOGGED_OUT."""
        try:
            data = self._exec_json([self.binary, "status"], timeout=_STATE_PROBE_TIMEOUT)
        except BwError:
            return SESSION_LOGGED_OUT
        if not isinstance(data, dict):
            return SESSION_LOGGED_OUT
        status = str(data.get("status") or "").lower()
        if status == "unlocked":
            return SESSION_UNLOCKED
        if status == "locked":
            return SESSION_LOCKED
        return SESSION_LOGGED_OUT

    def is_authenticated(self) -> bool:
        """True when bw has an unlocked vault session."""
        return self.session_state() == SESSION_UNLOCKED

    def unlock_session(self, password: str) -> None:
        """Unlock the vault with the master password.

        `bw unlock --raw` prints the new session key on stdout; it is
        kept in memory and injected as BW_SESSION. Raises
        WrongPasswordError when the password is rejected.
        """
        if not password:
            raise BwError("empty master password")
        try:
            out = self._secret_exec([self.binary, "unlock", "--raw"], password)
        except BwError as exc:
            if _is_wrong_password(exc):
                raise WrongPasswordError("wrong master password") from exc
            raise
        key = self._session_key_from(out)
        if not key:
            raise BwError("unlock returned no session key")
        self._session = key

    def login(self, email: str, password: str) -> None:
        """Log in with email + master password (`bw login <email> --raw`),
        which also unlocks the vault; the session key is kept in memory."""
        if not email or not password:
            raise BwError("empty email or password")
        try:
            out = self._secret_exec([self.binary, "login", email, "--raw"], password)
        except BwError as exc:
            if _is_wrong_password(exc):
                raise WrongPasswordError("wrong master password") from exc
            raise
        key = self._session_key_from(out)
        if not key:
            raise BwError("login returned no session key")
        self._session = key

    def login_with_api_key(self) -> None:
        """Log in with the API key from the environment (BW_CLIENTID +
        BW_CLIENTSECRET), `bw login --apikey`. The vault stays locked
        afterwards (an API key cannot derive the vault key); the gate
        then unlocks with the master password."""
        self._exec([self.binary, "login", "--apikey"])

    # ------------------------------------------------------------ vault data

    def sync(self) -> None:
        """Pull the latest vault data; best-effort (offline falls back to
        the locally cached vault)."""
        try:
            self._exec([self.binary, "sync"])
        except BwError:
            pass

    def list_folders(self) -> dict[str, str]:
        """Folder id -> name; {} when the call fails (older bw, offline)."""
        try:
            data = self._exec_json([self.binary, "list", "folders"])
        except BwError:
            return {}
        return parse_folders(data)

    def list_organizations(self) -> dict[str, str]:
        """Organization id -> name; {} when the call fails."""
        try:
            data = self._exec_json([self.binary, "list", "organizations"])
        except BwError:
            return {}
        return parse_organizations(data)

    def list_all_items(self) -> tuple[list[ItemRef], int]:
        """All items in the vault (personal + organizations), deduped.

        Returns (items, raw_count): raw_count is how many entries
        `bw list items` returned before dedup.
        """
        self.sync()
        data = self._exec_json([self.binary, "list", "items"])
        folder_names = self.list_folders()
        org_names = self.list_organizations()
        refs = parse_items(data, folder_names, org_names)
        return dedup_refs(refs), len(refs)

    def view_item(self, ref: ItemRef) -> dict:
        """Full decrypted item as the internal view shape (fields.py)."""
        data = self._exec_json([self.binary, "get", "item", ref.item_id])
        return bw_item_to_view(data)

    def view_item_with_retry(
        self, ref: ItemRef, attempts: int = 2, delay: float = 2.0
    ) -> dict:
        last: BwError | None = None
        for attempt in range(attempts):
            try:
                return self.view_item(ref)
            except BwError as exc:
                last = exc
                if attempt + 1 < attempts:
                    time.sleep(delay)
        raise last if last else BwError("item view failed")

    def totp(self, ref: ItemRef) -> str:
        """Current TOTP code for the item (`bw get totp <id> --raw`)."""
        out = self._exec([self.binary, "get", "totp", ref.item_id, "--raw"])
        return out.strip()
