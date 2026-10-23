from __future__ import annotations

import re
from collections import defaultdict

from .models import ItemRef

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_email_alias(ref: ItemRef) -> bool:
    """True when the item is a Proton/SimpleLogin email alias.

    Alias items are titled with the alias address itself
    (e.g. 'actress_revolving098@smail.me'), so the title carries the
    identity. Proton occasionally leaves two alias items for the same
    address (e.g. one created via SimpleLogin, one migrated), each with
    its own item_id — those are the duplicates the user sees.
    """
    return _EMAIL_RE.match(ref.title.strip()) is not None


def dedup_refs(refs: list[ItemRef]) -> list[ItemRef]:
    """Drop duplicate items, keeping first occurrence.

    Identity is the item_id alone: Proton Pass item ids are globally
    unique, and the same item can be visible through several shares
    (owned vault + a share), in which case pass-cli returns it once per
    share with the same item_id but a different share_id. Keying on
    (share_id, item_id) would keep those as "different" items and the
    rofi list would show the same entry twice. A stale cache file
    written by an older version must not produce duplicate rows either.

    Email-alias items are the one exception: an alias address is unique
    per alias, but Proton can hold two alias items for the same address
    (different item_ids). Those are deduped on the lowercased address so
    the same alias never appears twice.
    """
    seen_ids: set[str] = set()
    seen_aliases: set[str] = set()
    unique = []
    for ref in refs:
        if ref.item_id in seen_ids:
            continue
        if is_email_alias(ref):
            address = ref.title.strip().lower()
            if address in seen_aliases:
                continue
            seen_aliases.add(address)
        seen_ids.add(ref.item_id)
        unique.append(ref)
    return unique


def display_duplicate_groups(refs: list[ItemRef]) -> list[list[ItemRef]]:
    """Group refs that render identically (same title and same urls).

    Purely diagnostic: used by verbose logging to surface rows the user
    would perceive as duplicates, with the ids that distinguish them.
    """
    groups: dict[tuple[str, tuple[str, ...]], list[ItemRef]] = defaultdict(list)
    for ref in refs:
        groups[(ref.title, tuple(ref.urls))].append(ref)
    return [group for group in groups.values() if len(group) > 1]
