from __future__ import annotations

import html

from .models import ItemRef

SEP = "\x1f"


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def item_row(ref: ItemRef) -> str:
    """Rofi row for an item.

    The entry text is a plain token ("item" + share id + item id) so the
    selection can be resolved exactly, even with duplicate titles. The human
    readable text is attached via the row's `display` property, and the same
    token is mirrored into `info` for robustness across rofi versions.
    """
    token = SEP.join(("item", ref.share_id, ref.item_id))
    parts = [f"<b>{escape(ref.title)}</b>"]
    if ref.urls:
        parts.append(f"<span alpha='55%'>{escape(ref.urls[0])}</span>")
    if ref.vault_name:
        parts.append(f"<span alpha='40%'>[{escape(ref.vault_name)}]</span>")
    display = "  ·  ".join(parts)
    return f"{token}\x00display{SEP}{display}\x00info{SEP}{token}"


def field_row(label: str, info: str) -> str:
    return f"{info}\x00display{SEP}{escape(label)}\x00info{SEP}{info}"


def parse_info(row: str) -> tuple[str, ...]:
    """Extract the info payload from a rofi row or selection.

    Handles both the full row form ("entry\\0display\\x1f...\\0info\\x1fpayload")
    and a bare selection ("entry\\x1f..." or a plain "\\x1f"-joined payload).
    """
    if "\x00info" + SEP in row:
        info = row.rsplit("\x00info" + SEP, 1)[1]
    else:
        info = row.split("\x00", 1)[0]
    if SEP not in info:
        return ()
    return tuple(info.split(SEP))
