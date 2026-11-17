from __future__ import annotations

from collections import defaultdict

from .models import ItemRef


def dedup_refs(refs: list[ItemRef]) -> list[ItemRef]:
    """Drop duplicate items, keeping first occurrence.

    Primary identity is the item_id: Bitwarden item ids are globally
    unique.

    The cache stores item_uuid = item_id for bw, so identity is the
    item id; the uuid-first rule below is kept for older cache entries.

    Entries without an item_uuid fall back to item_id. A stale cache
    file written by an older version must not produce duplicate rows
    either.
    """
    seen_uuids: set[str] = set()
    seen_ids: set[str] = set()
    unique = []
    for ref in refs:
        if ref.item_uuid:
            if ref.item_uuid in seen_uuids:
                continue
            seen_uuids.add(ref.item_uuid)
        elif ref.item_id in seen_ids:
            continue
        else:
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
