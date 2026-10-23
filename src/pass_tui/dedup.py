from __future__ import annotations

from collections import defaultdict

from .models import ItemRef


def dedup_refs(refs: list[ItemRef]) -> list[ItemRef]:
    """Drop duplicate items, keeping first occurrence.

    Primary identity is the item_id: Proton Pass item ids are globally
    unique, and the same item can be visible through several shares
    (owned vault + a share), in which case pass-cli returns it once per
    share with the same item_id but a different share_id.

    But item_id is not the whole story: pass-cli has been observed to
    hand out different item_ids for what is clearly the same item (e.g.
    after a re-import or a sync conflict), while the content-level
    item_uuid stays stable. So when an entry carries an item_uuid, that
    is the stronger identity and wins over item_id. Two entries with the
    same non-empty item_uuid are the same item, even if their item_ids
    differ — keeping both is what produced visually identical rows where
    only one of them could actually be opened (the other item_id was
    stale and pass-cli item view failed for it).

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
