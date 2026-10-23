import json

import pytest

from pass_rofi.models import ItemRef, Vault
from pass_rofi.passcli import PassCli, PassCliError, parse_item_view, parse_items, parse_vaults


def make_client(routes):
    def runner(argv):
        key = tuple(argv[1:])
        if key not in routes:
            raise PassCliError(f"unexpected call: {argv}")
        value = routes[key]
        if isinstance(value, Exception):
            raise value
        return value

    return PassCli("pass-cli", runner=runner)


VAULTS_JSON = json.dumps(
    {"vaults": [{"share_id": "s1", "name": "Personal"}, {"share_id": "s2", "name": "Work"}]}
)

ITEMS_S1 = json.dumps(
    {
        "items": [
            {
                "id": "i1",
                "share_id": "s1",
                "content": {
                    "title": "GitHub",
                    "content": {
                        "Login": {
                            "username": "me",
                            "password": "secret",
                            "urls": ["https://github.com", "https://gist.github.com"],
                            "totp_uri": "otpauth://totp/x",
                        }
                    },
                },
            },
            {
                "item_id": "i2",
                "share_id": "s1",
                "state": "Trashed",
                "content": {"title": "Old", "content": {"Note": {"note": "x"}}},
            },
        ]
    }
)

ITEMS_S2 = json.dumps(
    {
        "items": [
            {
                "id": "i3",
                "share_id": "s2",
                "content": {
                    "title": "Bank",
                    "content": {"CreditCard": {"cardNumber": "4111", "urls": []}},
                },
            }
        ]
    }
)


def test_parse_vaults():
    vaults = parse_vaults(json.loads(VAULTS_JSON))
    assert vaults == [Vault("s1", "Personal"), Vault("s2", "Work")]


def test_parse_vaults_bare_list():
    vaults = parse_vaults([{"share_id": "x", "name": "V"}])
    assert vaults == [Vault("x", "V")]


def test_parse_vaults_dedups_same_share_id():
    """pass-cli can list the same share twice (e.g. shared + owned); each
    share must only be queried once or every item appears duplicated."""
    data = json.dumps(
        {
            "vaults": [
                {"share_id": "s1", "name": "Personal"},
                {"share_id": "s1", "name": "Personal"},
                {"share_id": "s2", "name": "Work"},
            ]
        }
    )
    vaults = parse_vaults(json.loads(data))
    assert vaults == [Vault("s1", "Personal"), Vault("s2", "Work")]


def test_parse_items_dedups_same_item_twice():
    """An item repeated in one vault response is parsed once."""
    entry = {
        "id": "i1",
        "share_id": "s1",
        "content": {
            "title": "GitHub",
            "content": {"Login": {"urls": ["https://github.com"]}},
        },
    }
    items = parse_items({"items": [entry, dict(entry)]}, Vault("s1", "Personal"))
    assert len(items) == 1
    assert items[0].title == "GitHub"


def test_parse_items_keeps_same_item_id_in_different_shares():
    """Same item id under two shares is two distinct items."""
    base = {
        "id": "i1",
        "content": {
            "title": "GitHub",
            "content": {"Login": {"urls": ["https://github.com"]}},
        },
    }
    items = parse_items(
        {"items": [dict(base, share_id="s1"), dict(base, share_id="s2")]}
    )
    assert len(items) == 2


def test_list_all_items_dedups_same_item_listed_twice_across_vaults():
    """The exact same item (same share + id) returned by two vault queries
    must appear only once — e.g. pass-cli listing the same share twice."""
    entry = {
        "id": "i1",
        "share_id": "s1",
        "content": {
            "title": "GitHub",
            "content": {"Login": {"urls": ["https://github.com"]}},
        },
    }
    client = make_client(
        {
            ("vault", "list", "--output", "json"): VAULTS_JSON,
            ("item", "list", "--share-id", "s1", "--output", "json", "--show-secrets"): json.dumps(
                {"items": [entry]}
            ),
            ("item", "list", "--share-id", "s2", "--output", "json", "--show-secrets"): json.dumps(
                {"items": [entry]}
            ),
        }
    )
    items = client.list_all_items()
    assert len(items) == 1
    assert items[0].item_id == "i1"
    assert items[0].share_id == "s1"


def test_parse_items_extracts_metadata_only():
    items = parse_items(json.loads(ITEMS_S1), Vault("s1", "Personal"))
    assert len(items) == 1
    ref = items[0]
    assert ref.item_id == "i1"
    assert ref.title == "GitHub"
    assert ref.urls == ("https://github.com", "https://gist.github.com")
    assert ref.item_type == "Login"
    assert ref.vault_name == "Personal"
    assert "password" not in ref.to_dict()
    assert "secret" not in json.dumps(ref.to_dict())


def test_parse_items_real_passcli_show_secrets_shape():
    """pass-cli item list --show-secrets --output json shape.

    Enum fields serialize as externally-tagged objects ({"Login": {...}}),
    autofill_urls entries are objects, and state is an enum name string.
    """
    raw = {
        "items": [
            {
                "id": "i1",
                "share_id": "s1",
                "vault_id": "v1",
                "state": "Active",
                "flags": [],
                "create_time": "2026-01-01T00:00:00",
                "modify_time": "2026-01-01T00:00:00",
                "content": {
                    "title": "GitHub",
                    "note": "",
                    "item_uuid": "u1",
                    "content": {
                        "Login": {
                            "email": "",
                            "username": "me",
                            "password": "secret",
                            "urls": ["https://github.com"],
                            "totp_uri": "",
                            "passkeys": [],
                            "autofill_urls": [
                                {"url": "https://gist.github.com", "mode": "Default"}
                            ],
                        }
                    },
                    "extra_fields": [],
                    "platform_specific": None,
                    "custom_icon": None,
                },
            },
            {
                "id": "i2",
                "share_id": "s1",
                "state": "Trashed",
                "content": {"title": "Old", "content": {"Note": {}}},
            },
        ]
    }
    items = parse_items(raw, Vault("s1", "Personal"))
    assert len(items) == 1
    ref = items[0]
    assert ref.item_id == "i1"
    assert ref.title == "GitHub"
    assert ref.urls == ("https://github.com", "https://gist.github.com")
    assert ref.item_type == "Login"
    assert "secret" not in json.dumps(ref.to_dict())


def test_parse_items_without_show_secrets_has_no_urls():
    """Without --show-secrets pass-cli emits ItemSummary (no content), so
    urls are empty — callers must request --show-secrets for matching."""
    raw = {
        "items": [
            {
                "id": "i1",
                "share_id": "s1",
                "vault_id": "v1",
                "state": "Active",
                "title": "GitHub",
                "item_type": "login",
            }
        ]
    }
    items = parse_items(raw, Vault("s1", "Personal"))
    assert len(items) == 1
    assert items[0].urls == ()
    assert items[0].item_type == "login"


def test_list_items_requests_show_secrets():
    seen = []

    def runner(argv):
        seen.append(argv)
        return ITEMS_S1

    client = PassCli("pass-cli", runner=runner)
    items = client.list_items(Vault("s1", "Personal"))
    assert seen[0] == [
        "pass-cli", "item", "list", "--share-id", "s1", "--output", "json", "--show-secrets"
    ]
    assert items[0].urls == ("https://github.com", "https://gist.github.com")


def test_parse_items_skips_trashed():
    items = parse_items(json.loads(ITEMS_S1))
    assert [i.title for i in items] == ["GitHub"]


def test_parse_items_handles_bare_list():
    items = parse_items([{"id": "a", "content": {"title": "T", "content": {}}}])
    assert items[0].item_id == "a"


def test_list_all_items_fans_out_across_vaults():
    client = make_client(
        {
            ("vault", "list", "--output", "json"): VAULTS_JSON,
            ("item", "list", "--share-id", "s1", "--output", "json", "--show-secrets"): ITEMS_S1,
            ("item", "list", "--share-id", "s2", "--output", "json", "--show-secrets"): ITEMS_S2,
        }
    )
    items = client.list_all_items()
    assert {i.title for i in items} == {"GitHub", "Bank"}
    assert {i.share_id for i in items} == {"s1", "s2"}


def test_list_all_items_tolerates_one_vault_failing():
    client = make_client(
        {
            ("vault", "list", "--output", "json"): VAULTS_JSON,
            ("item", "list", "--share-id", "s1", "--output", "json", "--show-secrets"): ITEMS_S1,
            ("item", "list", "--share-id", "s2", "--output", "json"): PassCliError("boom"),
        }
    )
    items = client.list_all_items()
    assert [i.title for i in items] == ["GitHub"]


def test_list_all_items_raises_when_everything_fails():
    client = make_client(
        {
            ("vault", "list", "--output", "json"): VAULTS_JSON,
            ("item", "list", "--share-id", "s1", "--output", "json", "--show-secrets"): PassCliError("a"),
            ("item", "list", "--share-id", "s2", "--output", "json", "--show-secrets"): PassCliError("b"),
        }
    )
    with pytest.raises(PassCliError):
        client.list_all_items()


def test_list_all_items_falls_back_to_default_vault():
    client = make_client(
        {
            ("vault", "list", "--output", "json"): json.dumps({"vaults": []}),
            ("item", "list", "--output", "json", "--show-secrets"): json.dumps(
                {"items": [{"id": "i9", "content": {"title": "Solo", "content": {}}}]}
            ),
        }
    )
    items = client.list_all_items()
    assert [i.title for i in items] == ["Solo"]


VIEW_JSON = json.dumps(
    {
        "item": {
            "content": {
                "title": "GitHub",
                "note": "work account",
                "content": {
                    "Login": {
                        "username": "me",
                        "password": "secret",
                        "urls": ["https://github.com"],
                        "totp_uri": "otpauth://totp/x",
                    }
                },
            }
        },
        "extra_fields": [{"name": "API key", "content": {"Hidden": "sk-123"}}],
    }
)


def test_view_item_parses_item_content_shape():
    client = make_client(
        {
            ("item", "view", "--share-id", "s1", "--item-id", "i1", "--output", "json"): VIEW_JSON
        }
    )
    data = client.view_item(ItemRef("s1", "i1", "GitHub"))
    assert data["item"]["content"]["title"] == "GitHub"


def test_parse_item_view_rejects_garbage():
    with pytest.raises(PassCliError):
        parse_item_view({"unexpected": True})
    with pytest.raises(PassCliError):
        parse_item_view([1, 2])


def test_view_item_retries_once():
    calls = []

    def runner(argv):
        calls.append(argv)
        if len(calls) == 1:
            raise PassCliError("transient")
        return VIEW_JSON

    client = PassCli("pass-cli", runner=runner)
    data = client.view_item_with_retry(ItemRef("s1", "i1", "GitHub"), delay=0)
    assert len(calls) == 2
    assert data["item"]["content"]["title"] == "GitHub"


def test_view_item_retry_exhausts():
    def runner(argv):
        raise PassCliError("always")

    client = PassCli("pass-cli", runner=runner)
    with pytest.raises(PassCliError):
        client.view_item_with_retry(ItemRef("s1", "i1", "GitHub"), attempts=2, delay=0)


def test_totp_uses_pass_uri():
    seen = []

    def runner(argv):
        seen.append(argv)
        return "123456\n"

    client = PassCli("pass-cli", runner=runner)
    assert client.totp(ItemRef("s1", "i1", "GitHub")) == "123456"
    assert seen[0][-1] == "pass://s1/i1/totp"
