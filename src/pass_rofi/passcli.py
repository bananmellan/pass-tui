from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable

from .models import ItemRef, Vault


class PassCliError(RuntimeError):
    pass


def _run(argv: list[str], timeout: float | None = None) -> str:
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PassCliError(f"executable not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PassCliError(f"timed out: {' '.join(argv)}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise PassCliError(detail[-1] if detail else f"{argv[0]} failed")
    return proc.stdout


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
    for raw in _as_list(data, ("vaults", "shares", "data", "items")):
        if not isinstance(raw, dict):
            continue
        share_id = _scalar(raw, "share_id", "shareId", "shareID", "id")
        name = _scalar(raw, "name", "title", "vault_name", "vaultName") or share_id
        if share_id:
            vaults.append(Vault(share_id=share_id, name=name or "Unnamed vault"))
    return vaults


def _urls_from_content(content: dict) -> list[str]:
    urls: list[str] = []
    for value in content.values():
        if not isinstance(value, dict):
            continue
        for key in ("urls", "url", "autofill_urls"):
            candidate = value.get(key)
            items = candidate if isinstance(candidate, list) else [candidate]
            for item in items:
                if isinstance(item, str) and item:
                    urls.append(item)
    return urls


def parse_items(data: Any, vault: Vault | None = None) -> list[ItemRef]:
    items = []
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
        inner = content.get("content") if isinstance(content.get("content"), dict) else {}
        item_type = (
            _scalar(raw, "type", "item_type", "itemType")
            or (next(iter(inner.keys())) if inner else "")
            or "item"
        )
        share_id = (
            _scalar(raw, "share_id", "shareId", "shareID", "vault_id", "vaultId")
            or (vault.share_id if vault else "")
        )
        vault_name = (
            _scalar(raw, "vault_name", "vaultName", "share_name")
            or (vault.name if vault else "")
        )
        items.append(
            ItemRef(
                share_id=share_id or "",
                item_id=item_id,
                title=title,
                urls=tuple(dict.fromkeys(_urls_from_content(inner))),
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
        data = self._exec_json(
            [
                self.binary,
                "item",
                "list",
                "--share-id",
                vault.share_id,
                "--output",
                "json",
            ]
        )
        return parse_items(data, vault)

    def list_all_items(self) -> list[ItemRef]:
        vaults = self.list_vaults()
        if not vaults:
            data = self._exec_json([self.binary, "item", "list", "--output", "json"])
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
        return results

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
