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
    assert "GitHub" in row
    assert "https://github.com" in row
    assert "[Personal]" in row
    parts = parse_info(row)
    assert parts == ("item", "s1", "i1")


def test_item_row_escapes_markup():
    row = item_row(ref(title="<b>evil</b> & co"))
    assert "<b>evil</b>" not in row
    assert "&lt;b&gt;evil&lt;/b&gt;" in row


def test_item_row_handles_missing_optional_fields():
    row = item_row(ref(urls=(), vault_name=""))
    assert "·" not in row.split("\x00")[0]
    assert parse_info(row) == ("item", "s1", "i1")


def test_field_row_roundtrip():
    row = field_row("url: https://example.com", "url: https://example.com")
    assert parse_info(row) == ("url: https://example.com",)


def test_parse_info_without_info_returns_empty():
    assert parse_info("plain row") == ()
