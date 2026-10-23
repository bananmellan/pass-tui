from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

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


def sort_refs(refs: list[ItemRef]) -> list[ItemRef]:
    """Sort items for display: title A-Z (case-insensitive), vault as tiebreak.

    This is the order rows are printed in; rofi keeps this order when no
    filter is active and re-ranks matches when one is (levenshtein, unless
    the user toggles fzf sorting).
    """
    return sorted(refs, key=lambda ref: (ref.title.lower(), ref.vault_name.lower()))


def dump_cache(refs: list[ItemRef], path: Path) -> None:
    payload = {
        "version": CACHE_VERSION,
        "created_at": int(time.time()),
        "items": [ref.to_dict() for ref in sort_refs(refs)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def cache_age(path: Path, now: float | None = None) -> float | None:
    try:
        return (now if now is not None else time.time()) - path.stat().st_mtime
    except OSError:
        return None


def _host_of(url: str) -> str:
    """Extract the lowercase host[:port] from a url, tolerating scheme-less."""
    candidate = url.strip().lower()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        host = urlsplit(candidate).hostname or ""
    except ValueError:
        return ""
    try:
        port = urlsplit(candidate).port
    except ValueError:
        port = None
    return f"{host}:{port}" if port is not None else host


def _title_matches(title: str, host: str) -> int:
    """Bonus for the item title relating to the host. Compares with
    punctuation/case stripped so 'NextCloud' matches 'nc.honung.org'
    (initials 'nc' + product name 'cloud') and 'GitHub' matches
    github.com."""
    squashed_title = re.sub(r"[^a-z0-9]", "", title.lower())
    bare = host.split(":")[0]
    parts = [p for p in bare.split(".") if p]
    candidates = []
    if len(parts) >= 2:
        candidates.append("".join(parts[:-1]))
    if parts:
        candidates.append(parts[0])
    for candidate in candidates:
        if not candidate:
            continue
        if squashed_title.startswith(candidate):
            return 30
        if candidate in squashed_title:
            return 15
    # CamelCase initials heuristic: the item title is a product name whose
    # capital-letter initials are the first host label ('NextCloud' ->
    # 'nc' for nc.honung.org). Also handles space-separated words
    # ('Next Cloud').
    if parts:
        label = parts[0]
        words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", title)
        if not words:
            words = re.findall(r"[A-Za-z]+", title)
        initials = "".join(w[0].lower() for w in words)
        if len(label) >= 2 and initials.startswith(label):
            return 15
    return 0


def _match_score(ref: ItemRef, host: str) -> int:
    """Higher is a better match. -1 means no match.

    Exact host equality beats subdomain matches, which beat host-contains,
    which beat raw substring. Ties break toward: item title matches a host
    label (so 'NextCloud' ranks for nc.honung.org), then fewer urls (a
    more focused item), then title alphabetically for determinism.
    """
    best = -1
    for url in ref.urls:
        url_host = _host_of(url)
        if not url_host:
            continue
        if url_host == host:
            score = 100
        elif url_host.startswith(host + ".") or url_host.endswith("." + host):
            score = 80
        elif host in url_host:
            score = 60
        elif host in url.lower():
            score = 40
        else:
            continue
        best = max(best, score)
    if best < 0:
        return -1
    best += _title_matches(ref.title, host)
    best += (9 - min(len(ref.urls), 9)) * 2
    return best


def match_host(refs: list[ItemRef], host: str) -> list[ItemRef]:
    """Fuzzy host matching, best matches first.

    Matches an item if any of its urls contains the clipboard host (case-
    insensitive), so `nc.honung.org` matches `https://nc.honung.org`,
    `https://nc.honung.org/apps/dashboard/`, `http://nc.honung.org:8080/x`,
    etc. Results are sorted by match quality: exact host equality first
    (e.g. an item whose url is exactly `https://nc.honung.org/` ranks above
    one that merely contains the host among several urls).
    """
    host = host.lower().strip("/")
    if not host:
        return []
    scored = []
    for ref in refs:
        score = _match_score(ref, host)
        if score >= 0:
            scored.append((score, ref.title.lower(), ref))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [ref for _score, _title, ref in scored]


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
