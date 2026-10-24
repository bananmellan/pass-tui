import json

import pytest

from pass_tui.models import ItemRef, Vault
from pass_tui.passcli import (
    SESSION_LOCKED,
    SESSION_LOGGED_OUT,
    SESSION_UNLOCKED,
    PassCli,
    PassCliError,
    SessionLockedError,
    parse_item_view,
    parse_items,
    parse_vaults,
)


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


def test_parse_items_dedups_same_item_id_across_shares():
    """The same item visible through two shares (same item_id, different
    share_id) is one item — Proton Pass item ids are globally unique."""
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
    assert len(items) == 1
    assert items[0].share_id == "s1"


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
    items, raw_count = client.list_all_items()
    assert len(items) == 1
    assert items[0].item_id == "i1"
    assert items[0].share_id == "s1"
    assert raw_count == 2


def test_list_all_items_dedups_shared_item_visible_in_two_vaults():
    """The same item (same item_id) returned by two different shares — the
    shared-item case — must appear only once."""
    base = {
        "id": "i1",
        "content": {
            "title": "GitHub",
            "content": {"Login": {"urls": ["https://github.com"]}},
        },
    }
    client = make_client(
        {
            ("vault", "list", "--output", "json"): VAULTS_JSON,
            ("item", "list", "--share-id", "s1", "--output", "json", "--show-secrets"): json.dumps(
                {"items": [dict(base, share_id="s1")]}
            ),
            ("item", "list", "--share-id", "s2", "--output", "json", "--show-secrets"): json.dumps(
                {"items": [dict(base, share_id="s2")]}
            ),
        }
    )
    items, raw_count = client.list_all_items()
    assert len(items) == 1
    assert items[0].item_id == "i1"
    assert raw_count == 2


def test_list_all_items_returns_deterministic_vault_order():
    """Results follow vault-list order (not thread completion order)."""
    def slow_first(argv):
        key = tuple(argv[1:])
        if key == ("vault", "list", "--output", "json"):
            return json.dumps({"vaults": [{"share_id": "s1", "name": "A"}, {"share_id": "s2", "name": "B"}]})
        if key[:4] == ("item", "list", "--share-id", "s1"):
            import time as t; t.sleep(0.2)  # s1 finishes last if completion-ordered
            return json.dumps({"items": [{"id": "i1", "share_id": "s1", "content": {"title": "FromS1", "content": {}}}]})
        if key[:4] == ("item", "list", "--share-id", "s2"):
            return json.dumps({"items": [{"id": "i2", "share_id": "s2", "content": {"title": "FromS2", "content": {}}}]})
        raise PassCliError(f"unexpected {argv}")

    client = PassCli("pass-cli", runner=slow_first, max_workers=2)
    items, _raw = client.list_all_items()
    assert [i.title for i in items] == ["FromS1", "FromS2"]


def test_parse_items_extracts_alias_address_from_prefix_suffix():
    """An Alias item's address is <prefix>@<suffix> from content.content.Alias."""
    raw = {
        "items": [
            {
                "id": "i1",
                "share_id": "s1",
                "content": {
                    "title": "actress_revolving098@smail.me",
                    "note": "",
                    "content": {
                        "Alias": {
                            "alias_prefix": "actress_revolving098",
                            "alias_suffix": "smail.me",
                            "mailboxes": [{"email": "me@proton.me"}],
                            "enabled": True,
                        }
                    },
                },
            }
        ]
    }
    items = parse_items(raw, Vault("s1", "Personal"))
    assert len(items) == 1
    assert items[0].item_type == "Alias"
    assert items[0].title == "actress_revolving098@smail.me"
    # The address is derivable metadata, not a secret — it is the title.
    assert "password" not in items[0].to_dict()


def test_parse_items_summary_item_type_snake_case():
    """The ItemSummary shape (item list without --show-secrets) carries a
    snake_case item_type field, e.g. "credit_card", "ssh_key"."""
    raw = {
        "items": [
            {"id": "i1", "share_id": "s1", "title": "Visa", "item_type": "credit_card", "state": "Active"},
            {"id": "i2", "share_id": "s1", "title": "Key", "item_type": "ssh_key", "state": "Active"},
            {"id": "i3", "share_id": "s1", "title": "Alias x", "item_type": "alias", "state": "Active"},
        ]
    }
    items = parse_items(raw, Vault("s1", "Personal"))
    assert [i.item_type for i in items] == ["credit_card", "ssh_key", "alias"]


def test_parse_items_badge_normalization_end_to_end():
    """Every pass-cli ItemType spelling normalizes to a list badge."""
    from pass_tui.types import type_badge

    raw = {
        "items": [
            {"id": f"i{n}", "share_id": "s1", "title": t, "item_type": t, "state": "Active"}
            for n, t in enumerate(["note", "login", "alias", "credit_card", "identity", "ssh_key", "wifi", "custom"])
        ]
    }
    items = parse_items(raw, Vault("s1", "Personal"))
    assert [type_badge(i.item_type) for i in items] == [
        "note", "login", "alias", "card", "identity", "ssh key", "wifi", "custom",
    ]


def test_parse_items_alias_with_precomposed_email_field():
    raw = {
        "items": [
            {
                "id": "i1",
                "share_id": "s1",
                "content": {
                    "title": "shopping.abc@duck.com",
                    "content": {
                        "Alias": {
                            "email": "shopping.abc@duck.com",
                            "mailboxes": [],
                        }
                    },
                },
            }
        ]
    }
    items = parse_items(raw, Vault("s1", "Personal"))
    assert items[0].item_type == "Alias"


def test_parse_items_alias_camelcase_keys():
    raw = {
        "items": [
            {
                "id": "i1",
                "share_id": "s1",
                "content": {
                    "title": "x@y.z",
                    "content": {
                        "Alias": {
                            "aliasPrefix": "x",
                            "aliasSuffix": "y.z",
                            "mailboxes": [],
                        }
                    },
                },
            }
        ]
    }
    items = parse_items(raw, Vault("s1", "Personal"))
    assert items[0].item_type == "Alias"


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


def test_parse_items_extracts_item_uuid():
    """The content-level item_uuid is extracted — it is the stable identity
    used for dedup when pass-cli hands out different item_ids for the
    same item."""
    raw = {
        "items": [
            {
                "id": "i1",
                "share_id": "s1",
                "content": {
                    "title": "GitHub",
                    "item_uuid": "u-abc-123",
                    "content": {"Login": {"urls": []}},
                },
            },
            {
                "id": "i2",
                "share_id": "s1",
                "content": {
                    "title": "NoUuid",
                    "content": {"Note": {"note": "x"}},
                },
            },
        ]
    }
    items = parse_items(raw, Vault("s1", "Personal"))
    assert items[0].item_uuid == "u-abc-123"
    assert items[1].item_uuid == ""


def test_parse_items_item_uuid_camelcase():
    raw = {
        "items": [
            {
                "id": "i1",
                "share_id": "s1",
                "content": {
                    "title": "GitHub",
                    "itemUuid": "u-camel",
                    "content": {"Login": {"urls": []}},
                },
            }
        ]
    }
    items = parse_items(raw, Vault("s1", "Personal"))
    assert items[0].item_uuid == "u-camel"


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
    assert ref.item_uuid == "u1"
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
    items, raw_count = client.list_all_items()
    assert {i.title for i in items} == {"GitHub", "Bank"}
    assert {i.share_id for i in items} == {"s1", "s2"}
    # ITEMS_S1 has 2 entries but one is Trashed -> 1 item; ITEMS_S2 -> 1 item.
    assert raw_count == 2


def test_list_all_items_tolerates_one_vault_failing():
    client = make_client(
        {
            ("vault", "list", "--output", "json"): VAULTS_JSON,
            ("item", "list", "--share-id", "s1", "--output", "json", "--show-secrets"): ITEMS_S1,
            ("item", "list", "--share-id", "s2", "--output", "json"): PassCliError("boom"),
        }
    )
    items, raw_count = client.list_all_items()
    assert [i.title for i in items] == ["GitHub"]
    assert raw_count == 1


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
    items, raw_count = client.list_all_items()
    assert [i.title for i in items] == ["Solo"]
    assert raw_count == 1


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


def test_is_authenticated_true_when_session_usable():
    seen = []

    def runner(argv):
        seen.append(argv)
        if argv[1] == "info":
            return "- Release track: stable\n"
        return '{"vaults": []}'

    client = PassCli("pass-cli", runner=runner)
    assert client.is_authenticated() is True
    # info alone cannot tell locked from unlocked; vault list confirms.
    assert seen == [["pass-cli", "info"], ["pass-cli", "vault", "list", "--output", "json"]]


def test_is_authenticated_false_when_info_fails():
    def runner(argv):
        raise PassCliError("no session, please login")

    client = PassCli("pass-cli", runner=runner)
    assert client.is_authenticated() is False


def test_is_authenticated_false_when_binary_missing():
    def runner(argv):
        raise PassCliError("executable not found: pass-cli")

    client = PassCli("pass-cli", runner=runner)
    assert client.is_authenticated() is False


def test_is_authenticated_false_when_session_locked():
    def runner(argv):
        if argv[1] == "info":
            return "- Session has lock: yes\n"
        raise PassCliError("Error: SessionLocked: session is locked")

    client = PassCli("pass-cli", runner=runner)
    assert client.is_authenticated() is False


def test_session_state_unlocked():
    def runner(argv):
        if argv[1] == "info":
            return "- Username: tester\n"
        return '{"vaults": []}'

    client = PassCli("pass-cli", runner=runner)
    assert client.session_state() == SESSION_UNLOCKED


def test_session_state_locked():
    def runner(argv):
        if argv[1] == "info":
            return "- Session has lock: yes\n"
        raise PassCliError("Error: SessionLocked: the session is locked")

    client = PassCli("pass-cli", runner=runner)
    assert client.session_state() == SESSION_LOCKED


def test_session_state_logged_out_when_no_session():
    def runner(argv):
        raise PassCliError("no authenticated client, please login")

    client = PassCli("pass-cli", runner=runner)
    assert client.session_state() == SESSION_LOGGED_OUT


def test_session_state_logged_out_when_api_call_fails_unrelated():
    """info succeeds but the authenticated call fails for a non-lock
    reason (e.g. network): treated as logged out, not locked."""
    def runner(argv):
        if argv[1] == "info":
            return "- Username: tester\n"
        raise PassCliError("connection refused")

    client = PassCli("pass-cli", runner=runner)
    assert client.session_state() == SESSION_LOGGED_OUT


def test_unlock_session_success():
    seen = []

    def pty_runner(argv, code):
        seen.append((argv, code))
        return "Enter lock code: \nSession unlocked successfully\n", False

    client = PassCli("pass-cli", pty_runner=pty_runner)
    client.unlock_session("1234")
    assert seen == [(["pass-cli", "session", "unlock"], "1234")]


def test_unlock_session_wrong_code():
    def pty_runner(argv, code):
        return "Enter lock code: \nError: SessionLocked: wrong lock code\n", False

    client = PassCli("pass-cli", pty_runner=pty_runner)
    with pytest.raises(SessionLockedError):
        client.unlock_session("0000")


def test_unlock_session_timeout():
    def pty_runner(argv, code):
        return "Enter lock code: ", True

    client = PassCli("pass-cli", pty_runner=pty_runner)
    with pytest.raises(PassCliError):
        client.unlock_session("1234")


def test_unlock_session_empty_code_rejected():
    def pty_runner(argv, code):
        raise AssertionError("must not be called")

    client = PassCli("pass-cli", pty_runner=pty_runner)
    with pytest.raises(PassCliError):
        client.unlock_session("")


def test_session_has_lock_true():
    client = make_client({("info", "--output", "json"): '{"session_has_lock": true}'})
    assert client.session_has_lock() is True


def test_session_has_lock_camelcase_and_yes():
    client = make_client({("info", "--output", "json"): '{"sessionHasLock": "yes"}'})
    assert client.session_has_lock() is True


def test_session_has_lock_false():
    client = make_client({("info", "--output", "json"): '{"session_has_lock": false}'})
    assert client.session_has_lock() is False


def test_session_has_lock_false_on_failure():
    def runner(argv):
        raise PassCliError("no session")

    client = PassCli("pass-cli", runner=runner)
    assert client.session_has_lock() is False


def test_create_lock_answers_both_prompts():
    seen = []

    def pty_runner(argv, code):
        seen.append((argv, code))
        return "Enter lock code: \nRe-enter lock code: \nSession locked successfully\n", False

    client = PassCli("pass-cli", pty_runner=pty_runner)
    client.create_lock("1234", 600)
    assert seen == [(
        ["pass-cli", "session", "create-lock", "--idle-timeout", "600"], "1234"
    )]


def test_create_lock_failure_raises():
    def pty_runner(argv, code):
        return "Enter lock code: \nError: personal access token sessions cannot have a lock\n", False

    client = PassCli("pass-cli", pty_runner=pty_runner)
    with pytest.raises(PassCliError, match="cannot have a lock"):
        client.create_lock("1234", 600)


def test_create_lock_timeout():
    def pty_runner(argv, code):
        return "Enter lock code: ", True

    client = PassCli("pass-cli", pty_runner=pty_runner)
    with pytest.raises(PassCliError, match="timed out"):
        client.create_lock("1234", 600)


def test_unlock_session_generic_failure_reports_last_line():
    def pty_runner(argv, code):
        return "Enter lock code: \nsome failure\nError: boom\n", False

    client = PassCli("pass-cli", pty_runner=pty_runner)
    with pytest.raises(PassCliError, match="boom"):
        client.unlock_session("1234")


def test_login_with_token_passes_token_flag():
    seen = []

    def runner(argv):
        seen.append(argv)
        return ""

    client = PassCli("pass-cli", runner=runner)
    client.login_with_token("pst_abc::key")
    assert seen == [["pass-cli", "login", "--personal-access-token", "pst_abc::key"]]


def test_login_with_token_propagates_failure():
    def runner(argv):
        raise PassCliError("invalid token")

    client = PassCli("pass-cli", runner=runner)
    with pytest.raises(PassCliError):
        client.login_with_token("pst_bad")
