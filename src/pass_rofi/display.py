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

    `meta` carries the searchable text (title, urls, vault name). rofi only
    token-matches the *entry* text in script mode (see script_token_match in
    rofi's source), and the entry is the opaque token — so without `meta`,
    typing a title would filter out every row.
    """
    token = SEP.join(("item", ref.share_id, ref.item_id))
    parts = [f"<b>{escape(ref.title)}</b>"]
    if ref.urls:
        parts.append(f"<span alpha='55%'>{escape(ref.urls[0])}</span>")
    if ref.vault_name:
        parts.append(f"<span alpha='40%'>[{escape(ref.vault_name)}]</span>")
    display = "  ·  ".join(parts)
    meta_parts = [ref.title, *ref.urls, ref.vault_name]
    meta = " ".join(dict.fromkeys(part for part in meta_parts if part))
    extras = (
        f"display{SEP}{display}"
        f"{SEP}meta{SEP}{meta}"
        f"{SEP}info{SEP}{token}"
    )
    return f"{token}\x00{extras}"


def field_row(label: str, info: str) -> str:
    extras = f"display{SEP}{escape(label)}{SEP}info{SEP}{info}"
    return f"{info}\x00{extras}"


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
