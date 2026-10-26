"""Item-type badges for the list.

pass-cli serializes `ItemContent` (proton-pass-types) as an externally
tagged enum: `content.content = {"Login": {...}}`, `{"Note": {...}}`,
`{"Alias": {...}}`, `{"CreditCard": {...}}`, `{"Identity": {...}}`,
`{"SshKey": {...}}`, `{"Wifi": {...}}`, `{"Custom": {...}}`. The summary
form (`item list` without --show-secrets) carries a snake_case
`item_type` field with the same names. Our cache stores whichever we
saw; both shapes normalize to the canonical names below.

Aliases are hide-my-email addresses: their title IS the address, so
they are badged "alias" (not "login") to tell them apart from logins
that happen to be titled with an email address.
"""

from __future__ import annotations

# Canonical type name -> short badge shown in the list.
_TYPE_BADGES = {
    "login": "login",
    "note": "note",
    "alias": "alias",
    "creditcard": "card",
    "credit_card": "card",
    "identity": "identity",
    "sshkey": "ssh key",
    "ssh_key": "ssh key",
    "wifi": "wifi",
    "custom": "custom",
}

# Canonical type name -> emoji shown in the list's first column.
# All emojis render as double-width in terminals.
_TYPE_EMOJIS = {
    "login": "🌐",
    "note": "📝",
    "alias": "🥷",
    "creditcard": "💳",
    "credit_card": "💳",
    "identity": "🪪",
    "sshkey": "🔑",
    "ssh_key": "🔑",
    "wifi": "📶",
    "custom": "📦",
}


def normalize_item_type(item_type: str) -> str:
    """Map any pass-cli spelling to a canonical lowercase type name."""
    key = item_type.strip().lower().replace("-", "_").replace(" ", "_")
    if key in _TYPE_BADGES:
        return key
    return ""


def type_badge(item_type: str) -> str:
    """Short human-readable badge for the list, e.g. "alias", "login".

    Returns "" for unknown/empty types so the row just shows no badge.
    """
    canonical = normalize_item_type(item_type)
    if not canonical:
        return ""
    return _TYPE_BADGES[canonical]


def type_emoji(item_type: str) -> str:
    """Emoji for the item's first list column, e.g. "🥷" for an alias.

    Returns "" for unknown/empty types so the row just shows no emoji.
    """
    canonical = normalize_item_type(item_type)
    if not canonical:
        return ""
    return _TYPE_EMOJIS[canonical]
