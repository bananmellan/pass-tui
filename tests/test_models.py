import json

from pass_tui.models import ItemRef


def test_itemref_dict_roundtrip_keeps_safe_fields():
    ref = ItemRef(
        "s1", "i1", "GitHub",
        ("https://github.com",), "Personal", "Login", "u1",
        safe_fields=(("username", "me"), ("password", ""), ("totp", ""), ("note", "work")),
    )
    back = ItemRef.from_dict(ref.to_dict())
    assert back == ref
    # Hidden fields keep their (empty) value so the menu can list the
    # label censored and copy triggers a live fetch.
    assert dict(back.safe_fields)["password"] == ""
    assert dict(back.safe_fields)["totp"] == ""


def test_itemref_dict_roundtrip_without_safe_fields():
    ref = ItemRef("s1", "i1", "GitHub", ("https://github.com",), "Personal", "Login")
    data = ref.to_dict()
    assert "safe_fields" not in data
    assert ItemRef.from_dict(data) == ref


def test_itemref_from_dict_tolerates_missing_safe_values():
    """Legacy/hand-written payloads without safe_fields parse fine."""
    ref = ItemRef.from_dict(
        {
            "share_id": "s1",
            "item_id": "i1",
            "title": "GitHub",
            "urls": ["https://github.com"],
            "vault_name": "Personal",
            "item_type": "Login",
            "item_uuid": "",
        }
    )
    assert ref.safe_fields == ()


def test_itemref_to_dict_never_serializes_secret_values():
    """A ref only ever holds safe_fields; even so, empty values are
    serialized explicitly so the censored label survives."""
    ref = ItemRef(
        "s1", "i1", "GitHub", (), "Personal", "Login",
        safe_fields=(("password", ""), ("username", "me")),
    )
    blob = json.dumps(ref.to_dict())
    assert "me" in blob
    assert '"password": ""' in blob
