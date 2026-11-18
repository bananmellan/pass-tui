"""Tests for the Bitwarden CLI (bw) backend: JSON parsing, the bw->view
adapter, session state, unlock/login via --passwordenv, server pinning,
and the list/get commands."""

import json

import pytest

from pass_tui.bitwarden import (
    PERSONAL_VAULT_NAME,
    SESSION_LOCKED,
    SESSION_LOGGED_OUT,
    SESSION_UNLOCKED,
    BwCli,
    BwError,
    WrongPasswordError,
    _MP_ENV,
    bw_item_to_view,
    parse_folders,
    parse_items,
    parse_organizations,
)
from pass_tui.fields import field_labels, field_value, hidden_field_labels
from pass_tui.models import ItemRef


def make_client(routes, seen=None):
    def runner(argv):
        if seen is not None:
            seen.append(list(argv))
        key = tuple(argv[1:])
        if key not in routes:
            raise BwError(f"unexpected call: {argv}")
        value = routes[key]
        if isinstance(value, Exception):
            raise value
        return value

    return BwCli("bw", runner=runner)


# ------------------------------------------------------------ fixtures

ITEM_LOGIN = {
    "object": "item",
    "id": "i1",
    "organizationId": None,
    "folderId": "f-dev",
    "type": 1,
    "name": "GitHub",
    "notes": "work account",
    "favorite": False,
    "login": {
        "uris": [
            {"uri": "https://github.com", "match": None},
            {"uri": "https://gist.github.com", "match": None},
        ],
        "username": "me",
        "password": "secret",
        "totp": "otpauth://totp/x",
    },
    "fields": [
        {"type": 0, "name": "API key", "value": "sk-123"},
        {"type": 1, "name": "PIN", "value": "9999"},
    ],
    "collectionIds": [],
    "revisionDate": "2026-01-01T00:00:00Z",
    "deletedDate": None,
}

ITEM_CARD_ORG = {
    "object": "item",
    "id": "i2",
    "organizationId": "org1",
    "folderId": None,
    "type": 3,
    "name": "Visa",
    "notes": None,
    "card": {
        "cardholderName": "Me",
        "brand": "Visa",
        "number": "4111111111111111",
        "expMonth": "12",
        "expYear": "2030",
        "code": "123",
    },
    "deletedDate": None,
}

ITEM_TRASHED = {
    "object": "item",
    "id": "i3",
    "organizationId": None,
    "folderId": None,
    "type": 2,
    "name": "Old",
    "notes": "x",
    "secureNote": {"type": 0},
    "deletedDate": "2026-01-02T00:00:00Z",
}

ITEM_IDENTITY = {
    "object": "item",
    "id": "i4",
    "organizationId": None,
    "folderId": None,
    "type": 4,
    "name": "ID card",
    "identity": {"firstName": "Ada", "lastName": "Lovelace", "ssn": "123-45", "email": "ada@x.io"},
}

ITEMS_JSON = json.dumps([ITEM_LOGIN, ITEM_CARD_ORG, ITEM_TRASHED, ITEM_IDENTITY])

FOLDERS_JSON = json.dumps(
    [
        {"object": "folder", "id": "f-dev", "name": "Work/Dev", "revisionDate": "2026-01-01T00:00:00Z"},
        {"object": "folder", "id": "", "name": "No Folder", "revisionDate": "2026-01-01T00:00:00Z"},
    ]
)

ORGS_JSON = json.dumps([{"object": "profileOrganization", "id": "org1", "name": "Acme"}])


# ------------------------------------------------------------ parsers


def test_parse_folders_skips_no_folder_pseudo_entry():
    assert parse_folders(json.loads(FOLDERS_JSON)) == {"f-dev": "Work/Dev"}
    assert parse_folders([]) == {}


def test_parse_organizations():
    assert parse_organizations(json.loads(ORGS_JSON)) == {"org1": "Acme"}


def test_parse_items_login_full_shape():
    refs = parse_items(json.loads(ITEMS_JSON), parse_folders(json.loads(FOLDERS_JSON)),
                       parse_organizations(json.loads(ORGS_JSON)))
    by_id = {r.item_id: r for r in refs}
    gh = by_id["i1"]
    assert gh.title == "GitHub"
    assert gh.urls == ("https://github.com", "https://gist.github.com")
    assert gh.folder == "Work/Dev"
    assert gh.vault_name == PERSONAL_VAULT_NAME  # organizationId null
    assert gh.item_type == "Login"
    assert gh.item_uuid == "i1"
    # Secrets are never cached: password/PIN values dropped, labels kept.
    safe = dict(gh.safe_fields)
    assert safe["password"] == ""
    assert safe["field: PIN"] == ""
    assert safe["username"] == "me"
    assert safe["note"] == "work account"
    assert safe["url: https://github.com"] == "https://github.com"
    assert safe["field: API key"] == "sk-123"


def test_parse_items_card_in_org_vault():
    refs = parse_items(json.loads(ITEMS_JSON), {}, parse_organizations(json.loads(ORGS_JSON)))
    by_id = {r.item_id: r for r in refs}
    card = by_id["i2"]
    assert card.vault_name == "Acme"
    assert card.item_type == "CreditCard"
    assert card.folder == ""
    assert card.urls == ()
    safe = dict(card.safe_fields)
    assert safe["cardNumber"] == ""  # secret: label only
    assert safe["cvv"] == ""
    assert safe["brand"] == "Visa"


def test_parse_items_skips_trash_and_unknown_org_falls_back():
    refs = parse_items(json.loads(ITEMS_JSON), {}, {})
    ids = [r.item_id for r in refs]
    assert "i3" not in ids  # deletedDate set -> trash
    assert set(ids) == {"i1", "i2", "i4"}
    assert refs[1].vault_name == "Organization"  # unknown org id


def test_parse_items_identity_and_dedups_same_id():
    data = [ITEM_IDENTITY, dict(ITEM_IDENTITY)]
    refs = parse_items(data)
    assert len(refs) == 1
    assert refs[0].item_type == "Identity"
    safe = dict(refs[0].safe_fields)
    assert safe["ssn"] == ""  # hidden
    assert safe["firstName"] == "Ada"


# ------------------------------------------------------------ adapter


def test_bw_item_to_view_login_labels_and_hidden():
    view = bw_item_to_view(ITEM_LOGIN)
    labels = field_labels(view)
    assert "username" in labels
    assert "password" in labels
    assert "url: https://github.com" in labels
    assert "totp" in labels
    assert "note" in labels
    assert "field: API key" in labels
    assert "field: PIN" in labels
    hidden = hidden_field_labels(view)
    assert "password" in hidden
    assert "field: PIN" in hidden
    assert "field: API key" not in hidden
    assert field_value(view, "username") == "me"
    assert field_value(view, "url: https://gist.github.com") == "https://gist.github.com"


def test_bw_item_to_view_card_and_identity_censor_secrets():
    card_view = bw_item_to_view(ITEM_CARD_ORG)
    hidden = hidden_field_labels(card_view)
    assert "cardNumber" in hidden
    assert "cvv" in hidden
    assert "brand" not in hidden
    id_view = bw_item_to_view(ITEM_IDENTITY)
    hidden = hidden_field_labels(id_view)
    assert "ssn" in hidden
    assert "firstName" not in hidden


def test_bw_item_to_view_rejects_garbage():
    with pytest.raises(BwError):
        bw_item_to_view({"id": "x"})  # no name
    with pytest.raises(BwError):
        bw_item_to_view(["not", "a", "dict"])


# ------------------------------------------------------------ session


def test_session_state_maps_bw_status():
    assert make_client({("status",): json.dumps({"status": "unlocked", "serverUrl": "https://bitwarden.eu"})}).session_state() == SESSION_UNLOCKED
    assert make_client({("status",): json.dumps({"status": "locked"})}).session_state() == SESSION_LOCKED
    assert make_client({("status",): json.dumps({"status": "unauthenticated"})}).session_state() == SESSION_LOGGED_OUT
    assert make_client({("status",): BwError("bw missing")}).session_state() == SESSION_LOGGED_OUT
    assert make_client({("status",): "not json"}).session_state() == SESSION_LOGGED_OUT


def test_is_authenticated():
    client = make_client({("status",): json.dumps({"status": "unlocked"})})
    assert client.is_authenticated() is True


# ------------------------------------------------------------ server


def test_ensure_server_noop_when_already_configured():
    seen = []
    client = make_client(
        {("status",): json.dumps({"serverUrl": "https://bitwarden.eu", "status": "unlocked"})},
        seen=seen,
    )
    client.ensure_server()
    assert [a[1:] for a in seen] == [["status"]]  # no config call


def test_ensure_server_configures_when_missing():
    seen = []
    responses = iter(
        [
            json.dumps({"serverUrl": None, "status": "unauthenticated"}),
            "Saved setting `config`.",
            json.dumps({"serverUrl": "https://bitwarden.eu", "status": "unauthenticated"}),
        ]
    )

    def runner(argv):
        seen.append(list(argv))
        return next(responses)

    client = BwCli("bw", runner=runner)
    client.ensure_server()
    assert ["bw", "config", "server", "https://bitwarden.eu"] in seen


def test_ensure_server_refuses_wrong_region():
    def runner(argv):
        if argv[1:] == ["status"]:
            return json.dumps({"serverUrl": "https://bitwarden.com", "status": "unauthenticated"})
        if argv[1:4] == ["config", "server", "https://bitwarden.eu"]:
            return "Saved setting `config`."
        raise BwError(f"unexpected {argv}")

    client = BwCli("bw", runner=runner)
    with pytest.raises(BwError, match="not configured"):
        client.ensure_server()


def test_ensure_server_logged_in_on_other_region_refuses_without_config_call():
    """bw refuses `config server` while a session is active; pass-tui must
    not even attempt the switch and must tell the user to `bw logout` first
    (or point PASS_TUI_BW_SERVER at the current region)."""
    for state in ("unlocked", "locked"):
        seen = []
        client = make_client(
            {("status",): json.dumps({"serverUrl": "https://bitwarden.com", "status": state})},
            seen=seen,
        )
        with pytest.raises(BwError, match="bw logout"):
            client.ensure_server()
        # Only the status probe ran - no `config server` call.
        assert [a[1:] for a in seen] == [["status"]]


def test_ensure_server_wraps_config_failure():
    """If `bw config server` itself fails while logged out, the raw bw
    error is wrapped with the target server for context."""

    def runner(argv):
        if argv[1:] == ["status"]:
            return json.dumps({"serverUrl": "https://bitwarden.com", "status": "unauthenticated"})
        if argv[1:4] == ["config", "server", "https://bitwarden.eu"]:
            raise BwError("Logout required before server config update.")
        raise BwError(f"unexpected {argv}")

    client = BwCli("bw", runner=runner)
    with pytest.raises(BwError, match=r"could not point bw at https://bitwarden\.eu"):
        client.ensure_server()


def test_ensure_server_custom_url():
    seen = []
    responses = iter(
        [
            json.dumps({"serverUrl": "https://vault.example.com", "status": "unlocked"}),
        ]
    )

    def runner(argv):
        seen.append(list(argv))
        return next(responses)

    client = BwCli("bw", runner=runner, server="https://vault.example.com")
    client.ensure_server()
    assert seen == [["bw", "status"]]


# ------------------------------------------------------------ login/unlock


def test_unlock_session_uses_passwordenv_and_stores_session_key():
    seen = []
    client = make_client(
        {("unlock", "--raw", "--passwordenv", _MP_ENV): "SESSIONKEY123\n"}, seen=seen
    )
    client.unlock_session("hunter2")
    assert client._session == "SESSIONKEY123"
    # The password is referenced by env var name, never present in argv.
    assert seen == [["bw", "unlock", "--raw", "--passwordenv", _MP_ENV]]
    assert "hunter2" not in str(seen)


def test_unlock_session_wrong_password():
    client = make_client(
        {("unlock", "--raw", "--passwordenv", _MP_ENV): BwError("Invalid master password.")}
    )
    with pytest.raises(WrongPasswordError):
        client.unlock_session("hunter2")


def test_unlock_session_other_error_is_bw_error():
    client = make_client(
        {("unlock", "--raw", "--passwordenv", _MP_ENV): BwError("You are not logged in.")}
    )
    with pytest.raises(BwError) as excinfo:
        client.unlock_session("hunter2")
    assert not isinstance(excinfo.value, WrongPasswordError)


def test_unlock_session_empty_password_rejected():
    client = make_client({})
    with pytest.raises(BwError):
        client.unlock_session("")


def test_login_uses_passwordenv_and_stores_session_key():
    seen = []
    client = make_client(
        {("login", "me@bitwarden.eu", "--raw", "--passwordenv", _MP_ENV): "KEY42\n"},
        seen=seen,
    )
    client.login("me@bitwarden.eu", "hunter2")
    assert client._session == "KEY42"
    assert seen == [["bw", "login", "me@bitwarden.eu", "--raw", "--passwordenv", _MP_ENV]]
    assert "hunter2" not in str(seen)


def test_login_wrong_password():
    client = make_client(
        {("login", "me@bitwarden.eu", "--raw", "--passwordenv", _MP_ENV): BwError("Invalid master password.")}
    )
    with pytest.raises(WrongPasswordError):
        client.login("me@bitwarden.eu", "nope")


def test_login_with_api_key_command():
    seen = []
    client = make_client({("login", "--apikey"): "ok"}, seen=seen)
    client.login_with_api_key()
    assert seen == [["bw", "login", "--apikey"]]


def test_secret_goes_to_child_env_not_argv(monkeypatch):
    """With a real (non-runner) exec, the master password travels in a
    child-only env var and the session key in BW_SESSION."""
    import pass_tui.bitwarden as bw_mod

    captured = {}

    def fake_run(argv, timeout=None, env=None):
        captured["argv"] = list(argv)
        captured["env"] = env
        return "KEY99\n"

    monkeypatch.setattr(bw_mod, "_run", fake_run)
    client = BwCli("bw")
    client._session = "OLDSESSION"
    client.unlock_session("hunter2")
    assert captured["argv"] == ["bw", "unlock", "--raw", "--passwordenv", _MP_ENV]
    assert "hunter2" not in captured["argv"]
    assert captured["env"][_MP_ENV] == "hunter2"
    assert captured["env"]["BW_SESSION"] == "OLDSESSION"
    assert client._session == "KEY99"
    # A later data call carries the new session key, never in argv.
    client.list_folders.__self__._exec(["bw", "list", "folders"])
    assert captured["argv"] == ["bw", "list", "folders"]
    assert captured["env"]["BW_SESSION"] == "KEY99"
    assert "KEY99" not in captured["argv"]


# ------------------------------------------------------------ vault data


def test_list_all_items_syncs_and_resolves_folder_and_org():
    seen = []
    client = make_client(
        {
            ("sync",): "Synced",
            ("list", "items"): ITEMS_JSON,
            ("list", "folders"): FOLDERS_JSON,
            ("list", "organizations"): ORGS_JSON,
        },
        seen=seen,
    )
    refs, raw_count = client.list_all_items()
    assert raw_count == 3  # 4 entries, trash item filtered during parse
    by_id = {r.item_id: r for r in refs}
    assert by_id["i1"].folder == "Work/Dev"
    assert by_id["i1"].vault_name == PERSONAL_VAULT_NAME
    assert by_id["i2"].vault_name == "Acme"
    assert [a[1] for a in seen] == ["sync", "list", "list", "list"]


def test_list_all_items_tolerates_folder_and_org_failure():
    client = make_client(
        {
            ("sync",): "Synced",
            ("list", "items"): ITEMS_JSON,
            ("list", "folders"): BwError("vault locked"),
            ("list", "organizations"): BwError("vault locked"),
        }
    )
    refs, _raw = client.list_all_items()
    by_id = {r.item_id: r for r in refs}
    assert by_id["i1"].folder == ""
    assert by_id["i2"].vault_name == "Organization"


def test_list_all_items_sync_failure_is_tolerated():
    client = make_client(
        {
            ("sync",): BwError("offline"),
            ("list", "items"): ITEMS_JSON,
            ("list", "folders"): "[]",
            ("list", "organizations"): "[]",
        }
    )
    refs, _raw = client.list_all_items()
    assert {r.item_id for r in refs} == {"i1", "i2", "i4"}


def test_view_item_adapts_to_internal_view():
    client = make_client({("get", "item", "i1"): json.dumps(ITEM_LOGIN)})
    view = client.view_item(ItemRef("", "i1", "GitHub"))
    assert field_labels(view)[0] == "note"
    assert field_value(view, "password") == "secret"  # live fetch returns the real value


def test_view_item_with_retry_recovers():
    attempts = {"n": 0}

    def runner(argv):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise BwError("flaky")
        return json.dumps(ITEM_LOGIN)

    client = BwCli("bw", runner=runner)
    view = client.view_item_with_retry(ItemRef("", "i1", "GitHub"), attempts=2, delay=0)
    assert field_value(view, "username") == "me"
    assert attempts["n"] == 2


def test_view_item_retry_exhausts():
    client = make_client({("get", "item", "i1"): BwError("gone")})
    with pytest.raises(BwError):
        client.view_item_with_retry(ItemRef("", "i1", "GitHub"), attempts=2, delay=0)


def test_totp_uses_get_totp_raw():
    seen = []
    client = make_client({("get", "totp", "i1", "--raw"): "123456\n"}, seen=seen)
    assert client.totp(ItemRef("", "i1", "GitHub")) == "123456"
    assert seen == [["bw", "get", "totp", "i1", "--raw"]]
