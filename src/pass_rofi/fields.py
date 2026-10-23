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


def field_labels(data: dict) -> list[str]:
    """Ordered, de-duplicated labels of copyable fields for an item view JSON."""
    content = _content(data)
    labels: list[str] = []
    if content.get("note"):
        labels.append("note")
    inner = content.get("content")
    if isinstance(inner, dict):
        for section in inner.values():
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                if key in _META_KEYS:
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
