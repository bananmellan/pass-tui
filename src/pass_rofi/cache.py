from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .models import ItemRef
from .passcli import PassCli, PassCliError

CACHE_VERSION = 1
CACHE_NAME = "items.v1.json"

_ALLOWED_KEYS = {"share_id", "item_id", "title", "urls", "vault_name", "item_type"}


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_cache(path: Path) -> list[ItemRef]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    refs = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if set(entry) - _ALLOWED_KEYS:
            continue
        try:
            refs.append(ItemRef.from_dict(entry))
        except ValueError:
            continue
    return refs


def dump_cache(refs: list[ItemRef], path: Path) -> None:
    payload = {
        "version": CACHE_VERSION,
        "created_at": int(time.time()),
        "items": [ref.to_dict() for ref in refs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def cache_age(path: Path, now: float | None = None) -> float | None:
    try:
        return (now if now is not None else time.time()) - path.stat().st_mtime
    except OSError:
        return None


def match_host(refs: list[ItemRef], host: str) -> list[ItemRef]:
    host = host.lower()
    return [ref for ref in refs if any(host in url.lower() for url in ref.urls)]


class Cache:
    def __init__(self, path: Path, client: PassCli, ttl: int = 600) -> None:
        self.path = path
        self.client = client
        self.ttl = ttl

    def load(self) -> list[ItemRef]:
        return load_cache(self.path)

    def rebuild(self) -> list[ItemRef]:
        refs = self.client.list_all_items()
        dump_cache(refs, self.path)
        return refs

    def get(self, force_refresh: bool = False) -> list[ItemRef]:
        refs = [] if force_refresh else self.load()
        if not refs:
            refs = self.rebuild()
            return refs
        age = cache_age(self.path)
        if age is not None and age > self.ttl:
            try:
                refs = self.rebuild()
            except PassCliError:
                pass
        return refs

    def ensure_fresh_async(self, spawn) -> bool:
        age = cache_age(self.path)
        if age is None or age <= self.ttl:
            return False
        lock = self.path.parent / "rebuild.lock"
        try:
            lock_age = time.time() - lock.stat().st_mtime
        except OSError:
            lock_age = None
        if lock_age is not None and lock_age < 300:
            return False
        try:
            lock.touch()
        except OSError:
            return False
        spawn(self)
        return True

    def refresh_locked(self) -> list[ItemRef]:
        try:
            refs = self.client.list_all_items()
        except PassCliError:
            return self.load()
        dump_cache(refs, self.path)
        try:
            (self.path.parent / "rebuild.lock").unlink()
        except OSError:
            pass
        return refs
