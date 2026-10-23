from __future__ import annotations

import html

from .models import ItemRef

SEP = "\x1f"


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def item_row(ref: ItemRef) -> str:
    """Rofi row for an item, with an \\0info\\x1f payload carrying share/item ids."""
    parts = [f"<b>{escape(ref.title)}</b>"]
    if ref.urls:
        parts.append(f"<span alpha='55%'>{escape(ref.urls[0])}</span>")
    if ref.vault_name:
        parts.append(f"<span alpha='40%'>[{escape(ref.vault_name)}]</span>")
    info = SEP.join(("item", ref.share_id, ref.item_id))
    return f"{'  ·  '.join(parts)}\x00info{SEP}{info}"


def field_row(label: str, info: str) -> str:
    return f"{escape(label)}\x00info{SEP}{info}"


def parse_info(row: str) -> tuple[str, ...]:
    if "\x00info" + SEP in row:
        info = row.rsplit("\x00info" + SEP, 1)[1]
    elif SEP in row:
        info = row
    else:
        return ()
    return tuple(info.split(SEP))
