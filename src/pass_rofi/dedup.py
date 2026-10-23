from __future__ import annotations

from .models import ItemRef


def dedup_refs(refs: list[ItemRef]) -> list[ItemRef]:
    """Drop duplicate items, keeping first occurrence.

    Identity is the (share_id, item_id) pair — the same title in two
    vaults is two different items and must both stay. A stale cache file
    written by an older version (or a pass-cli response that listed an
    item twice) must not produce duplicate rofi rows.
    """
    seen: set[tuple[str, str]] = set()
    unique = []
    for ref in refs:
        key = (ref.share_id, ref.item_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique
