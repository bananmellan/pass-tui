"""Copyable-field extraction for `pass-cli item view --output json`.

pass-cli serializes the item as ``{"item": {...}, "attachments": [...]}``
where ``item.content`` is an :class:`ItemData`:

- ``title``, ``note``, ``item_uuid``
- ``content`` — an externally tagged ``ItemContent`` enum:
  ``{"Login": {...}}``, ``{"Note": {}}``, ``{"Alias": {}}``,
  ``{"CreditCard": {...}}``, ``{"Identity": {...}}``, ``{"SshKey": {...}}``,
  ``{"Wifi": {...}}``, ``{"Custom": {"sections": [...]}}``
- ``extra_fields`` — ``[{"name", "content": {"Hidden"|"Text"|"Totp"|"Timestamp"}}]``
- ``platform_specific`` — ``{"android": {"allowed_apps": [...]}}``

Every copyable value of every item type is surfaced as a label:

- ``note`` for the item note
- each non-empty string field of the content section (login email,
  username, password; card number, PIN; identity names, phones, ...;
  wifi ssid/password; ssh public/private key; ...)
- ``totp`` when the section carries a ``totp_uri``
- ``url: <url>`` per unique URL (``urls`` plus ``autofill_urls`` entries,
  which serialize as ``{"url": ..., "mode": ...}`` objects)
- ``email: <address>`` for the alias address. The address lives in the
  top-level ``item.alias_email`` field (the ``Alias`` section itself is
  a unit struct); when absent we rebuild it from the alias title.
- ``<group>: <name>`` for identity extra-field groups, custom-item
  sections and ssh/wifi sub-sections, and ``field: <name>`` for extra
  fields.
- ``android app: <name> (<package>)`` for allowed Android apps.

Secret values are never logged or written anywhere; they only flow to
the clipboard through :func:`field_value`.
"""

from __future__ import annotations

from typing import Any

# Structural keys inside a content section that are not copyable
# payload: URL lists, TOTP URIs, passkeys, autofill bookkeeping,
# sub-structures handled separately.
_META_KEYS = {
    "urls",
    "url",
    "autofill_urls",
    "autofillUrls",
    "totp_uri",
    "totpUri",
    "passkeys",
    "autofillOnLoad",
    "sections",
    "section_fields",
    "section_name",
    "extra_personal_details",
    "extra_address_details",
    "extra_contact_details",
    "extra_work_details",
    "extra_sections",
}

# Alias-section structural keys (mailbox list, prefix/suffix parts,
# capability flags). The copyable payload of an alias is its address,
# exposed separately as the "email" label.
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

# Enum-valued section keys serialized as bare strings that carry no
# copyable payload (wifi security, card type).
_SKIP_SECTION_KEYS = {"security", "card_type"}

# Fixed-length mask shown on screen for hidden fields. Fixed length so
# the preview never leaks how long the secret is.
HIDDEN_PREVIEW = "••••••"

# Typed-section field names whose values are secrets and must be
# censored on screen (shown as HIDDEN_PREVIEW). Exact matches cover
# every field of the built-in item types (Login.password,
# CreditCard.number/pin/verification_number, SshKey.private_key,
# Wifi.password, Identity ssn/passport/license numbers); substrings
# catch obvious variants of the same idea.
_HIDDEN_SECTION_KEYS = {
    "password",
    "passwd",
    "pwd",
    "pin",
    "number",
    "verification_number",
    "cvv",
    "cvc",
    "card_number",
    "private_key",
    "secret",
    "token",
    "api_key",
    "social_security_number",
    "ssn",
    "passport_number",
    "license_number",
    "licence_number",
}
_HIDDEN_SECTION_SUBSTRINGS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "cvv",
    "verification",
    "private_key",
    "api_key",
    "apikey",
    "ssn",
    "social_security",
    "passport",
    "license",
    "licence",
    "pin",
)


def _section_field_is_hidden(key: str) -> bool:
    """True when a typed-section string field holds a secret."""
    lowered = key.lower()
    if lowered in _HIDDEN_SECTION_KEYS:
        return True
    return any(part in lowered for part in _HIDDEN_SECTION_SUBSTRINGS)

# Identity sub-field groups (Vec<ItemExtraField>) get flattened into
# "<group>: <name>" labels.
_IDENTITY_EXTRA_GROUPS = {
    "extra_personal_details": "personal",
    "extra_address_details": "address",
    "extra_contact_details": "contact",
    "extra_work_details": "work",
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


def _item(data: dict) -> dict:
    item = data.get("item")
    return item if isinstance(item, dict) else {}


def _extra_fields(data: dict) -> list:
    """Extra fields from either the content or the top level (older
    pass-cli shapes put them top-level)."""
    content = _content(data)
    for candidate in (content.get("extra_fields"), data.get("extra_fields")):
        if isinstance(candidate, list):
            return candidate
    return []


def _extra_field_value(field: dict) -> str:
    value = field.get("content")
    if isinstance(value, dict):
        for key in ("Hidden", "Text", "Totp", "value"):
            # An explicitly-present empty string is a real value (a
            # hidden-field placeholder restored from the cache); only
            # a missing key means "no payload".
            if key in value:
                return str(value[key])
        # Timestamp(i64) serializes as a bare integer.
        for key in ("Timestamp", "timestamp"):
            if value.get(key) not in (None, ""):
                return str(value[key])
        return ""
    if value:
        return str(value)
    return ""


def _extra_field_is_hidden(field: dict) -> bool:
    """True when an extra/custom field's content kind is a secret:
    Hidden or Totp. Text and Timestamp values are shown on screen."""
    content = field.get("content")
    if isinstance(content, dict):
        return "Hidden" in content or "Totp" in content
    return False


def _sections(content: dict) -> list[tuple[str, dict]]:
    """The ``content.content`` enum as [(variant_name, section_dict)]."""
    inner = content.get("content")
    if not isinstance(inner, dict):
        return []
    out = []
    for name, section in inner.items():
        if isinstance(section, dict):
            out.append((name, section))
    return out


def _alias_address(data: dict, content: dict) -> str:
    """The alias email address, if this is an alias item.

    The authoritative location is the top-level ``item.alias_email``
    (``AliasItem`` itself is a unit struct). Fallbacks: an explicit
    address key inside the Alias section, prefix@suffix parts, and
    finally the item title (the address is the alias title).
    """
    item = _item(data)
    for key in ("alias_email", "aliasEmail"):
        value = item.get(key)
        if isinstance(value, str) and "@" in value:
            return value
    for _name, section in _sections(content):
        for key in ("email", "alias_email", "aliasEmail", "address"):
            value = section.get(key)
            if isinstance(value, str) and "@" in value:
                return value
        prefix = suffix = None
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
    title = content.get("title")
    if isinstance(title, str) and "@" in title:
        return title
    return ""


def _is_alias(content: dict) -> bool:
    inner = content.get("content")
    return isinstance(inner, dict) and "Alias" in inner


def _autofill_urls(section: dict) -> list[str]:
    """URLs from an ``autofill_urls`` list. Entries are either plain
    strings or ``{"url": ..., "mode": ...}`` objects."""
    raw = section.get("autofill_urls")
    if not isinstance(raw, list):
        return []
    urls = []
    for entry in raw:
        if isinstance(entry, str) and entry:
            urls.append(entry)
        elif isinstance(entry, dict):
            url = entry.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def _section_fields(section: dict) -> list[tuple[str, str]]:
    """(key, value) pairs of copyable string fields in a section.

    A field with an empty-string value is included only when it is a
    hidden (secret) key — that is a cache-restored placeholder whose
    real value is always fetched live. Non-hidden empty fields carry
    no payload and are skipped.
    """
    out = []
    for key, value in section.items():
        if key in _META_KEYS or key in _ALIAS_META_KEYS or key in _SKIP_SECTION_KEYS:
            continue
        if not isinstance(value, str):
            continue
        if value:
            out.append((key, value))
        elif _section_field_is_hidden(key) and section.get("_cached_placeholder"):
            # Cache-restored hidden placeholder (empty value, marker
            # set by cached_view_from_ref).
            out.append((key, value))
    return out


def _group_fields(section: dict, group_key: str, prefix: str) -> list[tuple[str, str, bool]]:
    """Flatten a Vec<ItemExtraField> group into
    ("<prefix>: <name>", value, hidden)."""
    raw = section.get(group_key)
    if not isinstance(raw, list):
        return []
    out = []
    for field in raw:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        value = _extra_field_value(field)
        if value:
            out.append((f"{prefix}: {field['name']}", value, _extra_field_is_hidden(field)))
    return out


def _custom_sections(section: dict, key: str = "sections") -> list[tuple[str, str, bool]]:
    """Flatten Vec<CustomSection> into ("<section>: <field>", value, hidden)."""
    raw = section.get(key)
    if not isinstance(raw, list):
        return []
    out = []
    for section in raw:
        if not isinstance(section, dict):
            continue
        section_name = section.get("section_name") or section.get("sectionName") or ""
        fields = section.get("section_fields") or section.get("sectionFields") or []
        if not isinstance(fields, list):
            continue
        for field in fields:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            value = _extra_field_value(field)
            # Hidden fields stay in the menu (censored) even when the
            # value is empty — a cache-restored placeholder carries an
            # explicitly-present empty Hidden payload.
            if not value and not _extra_field_is_hidden(field):
                continue
            label = f"{section_name}: {field['name']}" if section_name else str(field["name"])
            out.append((label, value, _extra_field_is_hidden(field)))
    return out


def _android_apps(data: dict, content: dict) -> list[tuple[str, str, bool]]:
    """Allowed Android apps from ``platform_specific``."""
    platform = content.get("platform_specific")
    if platform is None:
        platform = _item(data).get("platform_specific")
    if not isinstance(platform, dict):
        return []
    android = platform.get("android")
    if not isinstance(android, dict):
        return []
    raw = android.get("allowed_apps")
    if not isinstance(raw, list):
        return []
    out = []
    for app in raw:
        if not isinstance(app, dict):
            continue
        name = app.get("app_name") or app.get("appName") or ""
        package = app.get("package_name") or app.get("packageName") or ""
        if name and package:
            label = f"android app: {name} ({package})"
        else:
            label = f"android app: {package or name}"
        out.append((label, package or name, False))
    return out


def _label_value_pairs(data: dict) -> list[tuple[str, str, bool]]:
    """Ordered (label, value, hidden) triples of every copyable field.

    ``hidden`` marks secrets that must be censored on screen (shown as
    HIDDEN_PREVIEW): typed-section secret keys, and extra/custom fields
    whose content kind is Hidden or Totp. Copying is unaffected.
    """
    content = _content(data)
    pairs: list[tuple[str, str, bool]] = []
    note = content.get("note")
    if isinstance(note, str) and note:
        pairs.append(("note", note, False))

    if _is_alias(content):
        address = _alias_address(data, content)
        if address:
            pairs.append((f"email: {address}", address, False))

    urls: list[str] = []
    for _name, section in _sections(content):
        for key, value in _section_fields(section):
            pairs.append((key, value, _section_field_is_hidden(key)))
        for key in ("urls", "autofill_urls"):
            raw = section.get(key)
            candidates = raw if isinstance(raw, list) else [raw]
            urls.extend(u for u in candidates if isinstance(u, str) and u)
        urls.extend(_autofill_urls(section))
        if section.get("totp_uri"):
            pairs.append(("totp", "", True))  # value fetched live via pass-cli
        for group_key, prefix in _IDENTITY_EXTRA_GROUPS.items():
            pairs.extend(_group_fields(section, group_key, prefix))
        pairs.extend(_custom_sections(section))
        pairs.extend(_custom_sections(section, key="extra_sections"))

    for url in dict.fromkeys(urls):
        pairs.append((f"url: {url}", url, False))

    for field in _extra_fields(data):
        if isinstance(field, dict) and field.get("name"):
            value = _extra_field_value(field)
            # A hidden field with an empty value is a cache-restored
            # placeholder: it still counts as a (censored) field.
            if value or _extra_field_is_hidden(field):
                pairs.append((f"field: {field['name']}", value, _extra_field_is_hidden(field)))

    pairs.extend(_android_apps(data, content))

    # De-duplicate by label, keeping first occurrence and order.
    seen = set()
    out = []
    for label, value, hidden in pairs:
        if label in seen:
            continue
        seen.add(label)
        out.append((label, value, hidden))
    return out


def field_labels(data: dict) -> list[str]:
    """Ordered, de-duplicated labels of copyable fields for an item view JSON."""
    return [label for label, _value, _hidden in _label_value_pairs(data)]


def hidden_field_labels(data: dict) -> set[str]:
    """Labels of fields whose values are secrets and must be censored
    on screen (see HIDDEN_PREVIEW)."""
    return {label for label, _value, hidden in _label_value_pairs(data) if hidden}


def field_value(data: dict, label: str, totp: str = "") -> str:
    """Resolve a label from :func:`field_labels` to its copyable value.

    ``totp`` is the live code fetched via ``pass-cli item view
    pass://<share>/<item>/totp``; the stored ``totp_uri`` is not copied.
    """
    if label == "totp":
        return totp
    for known, value, _hidden in _label_value_pairs(data):
        if known == label:
            return value
    return ""


def safe_fields_from_view(data: dict) -> dict[str, str]:
    """Fields of an item view that are safe to cache, label -> value.

    Non-hidden fields are included verbatim (usernames, emails, urls,
    notes, Text/Timestamp extra fields, identity details, ...). Hidden
    fields are included as (label, "") — the field *name* is kept so
    the menu can list it (censored), but the secret *value* is dropped
    and always fetched live from pass-cli at copy time. The ``totp``
    label is kept as ("totp", "") for the same reason: codes are
    generated live.
    """
    out: dict[str, str] = {}
    for label, value, hidden in _label_value_pairs(data):
        if hidden:
            out[label] = ""
        elif label != "totp" and value:
            out[label] = value
        else:
            out[label] = ""
    return out


def cached_view_from_ref(ref) -> dict | None:
    """Reconstruct a minimal item-view dict from an ItemRef's cached
    fields, or None when the ref carries none.

    Safe fields are restored with their real values (instant previews
    and copies). Hidden fields are restored as placeholders with empty
    values and their Hidden/Totp content kind, so the field menu lists
    them censored and copying detects them as sensitive (live fetch).
    The exact section structure of the original view is not preserved
    — field_labels/field_value/hidden_field_labels only care about
    labels, and the reconstruction reproduces them exactly.
    """
    if not ref.safe_fields:
        return None
    content: dict[str, Any] = {"title": ref.title}
    section: dict[str, Any] = {}
    extra_fields: list[dict] = []
    urls: list[str] = []
    note = ""
    has_totp = False
    for label, value in ref.safe_fields:
        if label == "note":
            note = value
            continue
        if label.startswith("url: "):
            urls.append(label[len("url: "):])
            continue
        if label == "totp":
            has_totp = True
            continue
        if label.startswith("field: "):
            name = label[len("field: "):]
            kind = "Hidden" if not value else "Text"
            extra_fields.append({"name": name, "content": {kind: value}})
            continue
        if ": " in label:
            # "<group>: <name>" identity/custom/section field. The
            # group prefix is part of the copyable name; hidden group
            # fields keep an empty value so they stay censored.
            group, _, name = label.partition(": ")
            kind = "Hidden" if not value else "Text"
            extra_fields.append({"name": name, "content": {kind: value}, "_group": group})
            continue
        if not value:
            # A typed-section field captured by name only: it is a
            # secret (e.g. password). Restore it under its original
            # key with the placeholder marker so the menu lists it
            # (censored) and copying detects it as sensitive.
            section[label] = ""
            section["_cached_placeholder"] = True
            continue
        section[label] = value
    if note:
        content["note"] = note
    if urls:
        section["urls"] = urls
    if has_totp:
        section["totp_uri"] = "otpauth://totp/cached"
    # Split grouped extra fields back out of the flat extra_fields
    # list into their section groups so labels regenerate as
    # "<group>: <name>".
    flat_extra: list[dict] = []
    grouped: dict[str, list[dict]] = {}
    for field in extra_fields:
        group = field.pop("_group", None)
        if group is None:
            flat_extra.append(field)
        else:
            grouped.setdefault(group, []).append(field)
    content["content"] = {ref.item_type or "Custom": section}
    content["extra_fields"] = flat_extra
    for group, fields in grouped.items():
        section.setdefault("extra_sections", []).append(
            {"section_name": group, "section_fields": fields}
        )
    return {"item": {"content": content}}
