"""Live filtering for the item list.

Pure functions, no I/O: given the full item list and the current query
string, produce the filtered, ordered list. Matching is token-based and
case-insensitive; a token matches when it is a substring of the title or
of any url/vault search text, or when all of the query's characters
appear in order (subsequence) in the search text, so `ghub` finds
"GitHub" and `nc` finds "NextCloud".

Ranking: title-substring matches first, then subsequence matches, then
url/vault-only matches. Within a rank, the original title-sorted order
is kept, so the list is stable while typing.
"""

from __future__ import annotations

from .models import ItemRef
from .types import type_badge


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(ch in it for ch in needle)


def search_text(ref: ItemRef) -> str:
    """All searchable text for a ref: title first, then urls, then vault,
    then the type badge (so typing "alias" lists all hide-my-email items)."""
    parts = [ref.title, *ref.urls]
    if ref.vault_name:
        parts.append(ref.vault_name)
    badge = type_badge(ref.item_type)
    if badge:
        parts.append(badge)
    return " ".join(parts)


def _score(ref: ItemRef, query: str) -> int | None:
    """Lower is better; None means no match.

    Tokens are whitespace-separated. A token matches the title by
    substring (best) or subsequence; otherwise it matches the combined
    title+urls+vault text by substring, then subsequence. A subsequence
    match only counts for tokens of at least 3 characters, so two-letter
    queries stay precise. Every token must match somewhere; the ref's
    score is the worst token rank, so multi-token queries rank by their
    least specific token.
    """
    title = ref.title.lower()
    haystack = search_text(ref).lower()
    badge = type_badge(ref.item_type)
    worst = -1
    for token in query.split():
        if not token:
            continue
        if badge and token == badge:
            # Exact badge match ("alias", "login", ...) ranks first, above
            # even title matches, so typing "alias" surfaces exactly the
            # hide-my-email items, not logins whose title contains the word.
            rank = 0
        elif token in title:
            rank = 1
        elif len(token) >= 3 and _is_subsequence(token, title):
            rank = 2
        elif token in haystack:
            rank = 3
        elif len(token) >= 3 and _is_subsequence(token, haystack):
            rank = 4
        else:
            return None
        worst = max(worst, rank)
    if worst < 0:
        return None
    return worst


def filter_items(refs: list[ItemRef], query: str) -> list[ItemRef]:
    """Items matching the query, best matches first, stable within a rank.

    The input list is expected to be in display order (title-sorted);
    the output preserves that order within each rank. An empty query
    returns everything, so the unfiltered list is the sorted full list.
    """
    query = query.strip().lower()
    if not query:
        return list(refs)
    scored = []
    for ref in refs:
        score = _score(ref, query)
        if score is not None:
            scored.append((score, ref))
    scored.sort(key=lambda entry: entry[0])
    return [ref for _score, ref in scored]
