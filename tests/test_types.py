import pytest

from pass_tui.types import normalize_item_type, type_badge


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
