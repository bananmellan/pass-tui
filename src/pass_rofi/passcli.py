from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable

from .dedup import dedup_refs
from .models import ItemRef, Vault


class PassCliError(RuntimeError):
    pass


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
    seen: set[tuple[str, str]] = set()
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
        # The same item can appear more than once in a vault listing (e.g.
        # pass-cli re-emits it when the vault response is paginated oddly, or
        # a shared item shows up in two shares). Dedup on the identity pair
        # so the rofi list never shows the same item twice.
        key = (share_id, item_id)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            ItemRef(
                share_id=share_id,
                item_id=item_id,
                title=title,
                urls=tuple(dict.fromkeys(_urls_from_entry(raw))),
                vault_name=vault_name or "",
                item_type=item_type,
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
    ) -> None:
        self.binary = binary
        self._runner = runner
        self.timeout = timeout
        self.max_workers = max_workers

    def _exec(self, argv: list[str]) -> str:
        if self._runner is not None:
            return self._runner(argv)
        return _run(argv, timeout=self.timeout)

    def _exec_json(self, argv: list[str]) -> Any:
        out = self._exec(argv)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise PassCliError(f"invalid JSON from {' '.join(argv)}") from exc

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

    def list_all_items(self) -> list[ItemRef]:
        vaults = self.list_vaults()
        if not vaults:
            data = self._exec_json(
                [self.binary, "item", "list", "--output", "json", "--show-secrets"]
            )
            return parse_items(data)
        results: list[ItemRef] = []
        errors: list[str] = []
        workers = max(1, min(self.max_workers, len(vaults)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.list_items, vault): vault for vault in vaults}
            for future in as_completed(futures):
                try:
                    results.extend(future.result())
                except PassCliError as exc:
                    errors.append(str(exc))
        if not results and errors:
            raise PassCliError("; ".join(errors))
        # A shared item can be visible through two shares; parse_items dedups
        # within one vault response, this dedups across vaults on identity.
        return dedup_refs(results)

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
