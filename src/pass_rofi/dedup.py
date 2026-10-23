from __future__ import annotations

from collections import defaultdict

from .models import ItemRef


def dedup_refs(refs: list[ItemRef]) -> list[ItemRef]:
    """Drop duplicate items, keeping first occurrence.

    Identity is the item_id alone: Proton Pass item ids are globally
    unique, and the same item can be visible through several shares
    (owned vault + a share), in which case pass-cli returns it once per
    share with the same item_id but a different share_id. Keying on
    (share_id, item_id) would keep those as "different" items and the
    rofi list would show the same entry twice. A stale cache file
    written by an older version must not produce duplicate rows either.
    """
    seen: set[str] = set()
    unique = []
    for ref in refs:
        if ref.item_id in seen:
            continue
        seen.add(ref.item_id)
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
