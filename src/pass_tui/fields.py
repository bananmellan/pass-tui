from __future__ import annotations

from typing import Any

_META_KEYS = {
    "urls",
    "url",
    "autofill_urls",
    "totp_uri",
    "totpUri",
    "passkeys",
    "autofillOnLoad",
}

# Alias-item structural keys (mailbox list, prefix/suffix, flags). The
# copyable payload of an alias is its address, exposed separately as
# the "email" label below.
_ALIAS_META_KEYS = {
    "mailboxes",
    "alias_prefix",
    "aliasPrefix",
    "prefix",
    "alias_suffix",
    "aliasSuffix",
    "suffix",
    "domain",
    "enabled",
    "note",
    "block_reply",
    "blockReply",
    "can_block_reply",
    "canBlockReply",
    "can_modify_prefix",
    "canModifyPrefix",
    "can_modify_suffix",
    "canModifySuffix",
    "can_attach",
    "canAttach",
    "simple_login_id",
    "simpleLoginId",
    "alias_id",
    "aliasId",
    "creation_time",
    "creationTime",
    "deletion_time",
    "deletionTime",
}


def _content(data: dict) -> dict:
    for path in (("item", "content"), ("content",)):
        current: Any = data
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if isinstance(current, dict):
            return current
    return {}


def _extra_fields(data: dict) -> list:
    """Extra fields from either the top level or content (item view wraps
    them under content in current pass-cli, older shapes put them top-level)."""
    content = _content(data)
    for candidate in (content.get("extra_fields"), data.get("extra_fields")):
        if isinstance(candidate, list):
            return candidate
    return []


def _alias_address(content: dict) -> str:
    """The alias email address from an Alias item view, if any.

    Only matches the Alias section shape: content.content =
    {"Alias": {"alias_prefix": ..., "alias_suffix": ...}}. The full
    address is "<prefix>@<suffix>". A Login item's "email" field is NOT
    an alias address and must not be picked up here.
    """
    inner = content.get("content")
    if not isinstance(inner, dict):
        return ""
    section = inner.get("Alias")
    if not isinstance(section, dict):
        return ""
    for key in ("email", "alias_email", "aliasEmail", "address"):
        value = section.get(key)
        if isinstance(value, str) and "@" in value:
            return value
    prefix = None
    suffix = None
    for key in ("alias_prefix", "aliasPrefix", "prefix"):
        if isinstance(section.get(key), str):
            prefix = section[key]
            break
    for key in ("alias_suffix", "aliasSuffix", "suffix", "domain"):
        if isinstance(section.get(key), str):
            suffix = section[key]
            break
    if prefix and suffix:
        return f"{prefix}@{suffix}"
    return ""


def field_labels(data: dict) -> list[str]:
    """Ordered, de-duplicated labels of copyable fields for an item view JSON."""
    content = _content(data)
    labels: list[str] = []
    if content.get("note"):
        labels.append("note")
    alias_address = _alias_address(content)
    inner = content.get("content")
    if isinstance(inner, dict):
        for section in inner.values():
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                if key in _META_KEYS or key in _ALIAS_META_KEYS:
                    continue
                if isinstance(value, str) and value:
                    labels.append(key)
        has_totp = any(
            isinstance(section, dict) and section.get("totp_uri")
            for section in inner.values()
        )
        if has_totp:
            labels.append("totp")
        urls: list[str] = []
        for section in inner.values():
            if not isinstance(section, dict):
                continue
            for key in ("urls", "autofill_urls"):
                value = section.get(key)
                candidates = value if isinstance(value, list) else [value]
                urls.extend(u for u in candidates if isinstance(u, str) and u)
        for url in dict.fromkeys(urls):
            labels.append(f"url: {url}")
    if alias_address:
        labels.append(f"email: {alias_address}")
    for field in _extra_fields(data):
        if isinstance(field, dict) and field.get("name"):
            labels.append(f"field: {field['name']}")
    return list(dict.fromkeys(labels))


def field_value(data: dict, label: str, totp: str = "") -> str:
    if label == "note":
        return str(_content(data).get("note") or "")
    if label == "totp":
        return totp
    if label.startswith("url: "):
        return label[len("url: "):]
    if label.startswith("email: "):
        # The alias address is not a secret — it is the item's title and
        # the value the user wants to copy. Return the label payload.
        return label[len("email: "):]
    if label.startswith("field: "):
        name = label[len("field: "):]
        for field in _extra_fields(data):
            if not isinstance(field, dict) or field.get("name") != name:
                continue
            value = field.get("content")
            if isinstance(value, dict):
                for key in ("Hidden", "Text", "value"):
                    if value.get(key):
                        return str(value[key])
            elif value:
                return str(value)
        return ""
    content = _content(data)
    inner = content.get("content")
    if isinstance(inner, dict):
        for section in inner.values():
            if isinstance(section, dict) and section.get(label):
                return str(section[label])
    return ""
