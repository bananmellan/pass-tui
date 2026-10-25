from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Vault:
    share_id: str
    name: str


@dataclass(frozen=True)
class ItemRef:
    share_id: str
    item_id: str
    title: str
    urls: tuple[str, ...] = field(default_factory=tuple)
    vault_name: str = ""
    item_type: str = ""
    item_uuid: str = ""
    # Non-sensitive copyable fields (label -> value) captured at cache
    # rebuild time from pass-cli's --show-secrets listing. Only fields
    # whose values are not secrets (usernames, emails, urls, notes,
    # Text/Timestamp extra fields, ...) are stored here; secret values
    # are always fetched live. Absent for entries written by older
    # cache versions.
    safe_fields: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        data = {
            "share_id": self.share_id,
            "item_id": self.item_id,
            "title": self.title,
            "urls": list(self.urls),
            "vault_name": self.vault_name,
            "item_type": self.item_type,
            "item_uuid": self.item_uuid,
        }
        if self.safe_fields:
            data["safe_fields"] = {label: value for label, value in self.safe_fields}
        return data

    @classmethod
    def from_dict(cls, raw) -> "ItemRef":
        if not isinstance(raw, dict):
            raise ValueError("item ref must be an object")
        share_id = str(raw.get("share_id") or "")
        item_id = str(raw.get("item_id") or "")
        title = str(raw.get("title") or "")
        if not item_id or not title:
            raise ValueError("item ref requires item_id and title")
        urls_raw = raw.get("urls")
        urls = (
            tuple(str(u) for u in urls_raw if isinstance(u, str) and u)
            if isinstance(urls_raw, list)
            else ()
        )
        safe_raw = raw.get("safe_fields")
        safe_fields: tuple[tuple[str, str], ...] = ()
        if isinstance(safe_raw, dict):
            # Empty values are kept: they mark hidden fields whose
            # labels are safe to show but whose values are secrets
            # (fetched live at copy time).
            safe_fields = tuple(
                (str(label), str(value))
                for label, value in safe_raw.items()
                if isinstance(label, str) and isinstance(value, str)
            )
        return cls(
            share_id=share_id,
            item_id=item_id,
            title=title,
            urls=urls,
            vault_name=str(raw.get("vault_name") or ""),
            item_type=str(raw.get("item_type") or ""),
            item_uuid=str(raw.get("item_uuid") or ""),
            safe_fields=safe_fields,
        )
