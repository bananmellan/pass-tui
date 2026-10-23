from pass_rofi.display import field_row, item_row, parse_info
from pass_rofi.models import ItemRef


def ref(**kwargs):
    defaults = dict(
        share_id="s1",
        item_id="i1",
        title="GitHub",
        urls=("https://github.com",),
        vault_name="Personal",
        item_type="Login",
    )
    defaults.update(kwargs)
    return ItemRef(**defaults)


def test_item_row_contains_title_url_vault_and_info():
    row = item_row(ref())
    assert row.startswith("item\x1fs1\x1fi1\x00")
    assert "GitHub" in row
    assert "https://github.com" in row
    assert "[Personal]" in row
    parts = parse_info(row)
    assert parts == ("item", "s1", "i1")


def test_item_row_display_is_pango_and_entry_is_token():
    row = item_row(ref())
    entry, _, extras = row.partition("\x00")
    assert entry == "item\x1fs1\x1fi1"
    pairs = extras.split("\x1f")
    assert pairs[0] == "display" and pairs[1].startswith("<b>GitHub</b>")
    assert pairs[2] == "meta" and "GitHub" in pairs[3]
    assert pairs[4] == "info" and pairs[5:] == ["item", "s1", "i1"]


def test_item_row_meta_carries_searchable_text():
    row = item_row(ref(urls=("https://github.com",), vault_name="Personal"))
    _, _, extras = row.partition("\x00")
    pairs = extras.split("\x1f")
    meta = pairs[pairs.index("meta") + 1]
    assert "GitHub" in meta
    assert "https://github.com" in meta
    assert "Personal" in meta


def test_item_row_escapes_markup():
    row = item_row(ref(title="<b>evil</b> & co"))
    assert "&lt;b&gt;evil&lt;/b&gt;" in row
    assert "<b>evil</b>" not in row.split("\x1f")[1]


def test_item_row_handles_missing_optional_fields():
    row = item_row(ref(urls=(), vault_name=""))
    assert "·" not in row.split("\x00")[0]
    assert parse_info(row) == ("item", "s1", "i1")


def test_field_row_roundtrip():
    row = field_row("url: https://example.com", "url: https://example.com")
    entry, _, extras = row.partition("\x00")
    assert entry == "url: https://example.com"
    pairs = extras.split("\x1f")
    assert pairs[0] == "display" and pairs[1] == "url: https://example.com"
    assert pairs[2] == "info" and pairs[3] == "url: https://example.com"


def test_field_row_display_is_escaped():
    row = field_row("<b>x</b>", "<b>x</b>")
    _, _, extras = row.partition("\x00")
    assert extras.split("\x1f")[1] == "&lt;b&gt;x&lt;/b&gt;"


def test_parse_info_without_info_returns_empty():
    assert parse_info("plain row") == ()
