import pytest

from pass_tui.types import normalize_item_type, type_badge, type_emoji


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Login", "login"),
        ("login", "login"),
        ("Note", "note"),
        ("Alias", "alias"),
        ("alias", "alias"),
        ("CreditCard", "creditcard"),
        ("credit_card", "credit_card"),
        ("credit-card", "credit_card"),
        ("Identity", "identity"),
        ("SshKey", "sshkey"),
        ("ssh_key", "ssh_key"),
        ("Wifi", "wifi"),
        ("Custom", "custom"),
        ("", ""),
        ("unknown-thing", ""),
        ("  Login  ", "login"),
    ],
)
def test_normalize_item_type(raw, expected):
    assert normalize_item_type(raw) == expected


@pytest.mark.parametrize(
    "raw,badge",
    [
        ("Login", "login"),
        ("Note", "note"),
        ("Alias", "alias"),
        ("CreditCard", "card"),
        ("Identity", "identity"),
        ("SshKey", "ssh key"),
        ("Wifi", "wifi"),
        ("Custom", "custom"),
        ("", ""),
        ("something-else", ""),
    ],
)
def test_type_badge(raw, badge):
    assert type_badge(raw) == badge


def test_alias_is_distinguished_from_login():
    """A hide-my-email alias must not render as a login."""
    assert type_badge("Alias") == "alias"
    assert type_badge("Alias") != type_badge("Login")


@pytest.mark.parametrize(
    "raw,emoji",
    [
        ("Login", "🌐"),
        ("Note", "📝"),
        ("Alias", "🥷"),
        ("CreditCard", "💳"),
        ("credit_card", "💳"),
        ("Identity", "🪪"),
        ("SshKey", "🔑"),
        ("Wifi", "📶"),
        ("Custom", "📦"),
        ("", ""),
        ("unknown", ""),
    ],
)
def test_type_emoji(raw, emoji):
    assert type_emoji(raw) == emoji


def test_type_emoji_alias_is_ninja_login_is_globe():
    """Aliases get a ninja, logins get a globe."""
    assert type_emoji("Alias") == "🥷"
    assert type_emoji("Login") == "🌐"
    assert type_emoji("Alias") != type_emoji("Login")


def test_type_emoji_all_wide():
    """Every type emoji must be a single codepoint with East Asian
    Width 'W' (double-width) so the fixed _EMOJI_COL=3 column aligns
    correctly in real terminals."""
    import unicodedata

    from pass_tui.types import _TYPE_EMOJIS

    for name, emoji in _TYPE_EMOJIS.items():
        assert len(emoji) == 1, f"{name}: {emoji!r} is multi-codepoint"
        assert unicodedata.east_asian_width(emoji) == "W", (
            f"{name}: {emoji!r} is not double-width"
        )
