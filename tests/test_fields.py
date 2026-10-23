import json

from pass_rofi.fields import field_labels, field_value

VIEW = {
    "item": {
        "content": {
            "title": "GitHub",
            "note": "work account",
            "content": {
                "Login": {
                    "username": "me",
                    "password": "secret",
                    "urls": ["https://github.com", "https://gist.github.com"],
                    "totp_uri": "otpauth://totp/x",
                    "autofillOnLoad": True,
                }
            },
        }
    },
    "extra_fields": [
        {"name": "API key", "content": {"Hidden": "sk-123"}},
        {"name": "PIN", "content": {"Text": "0000"}},
    ],
}


def test_field_labels_order_and_dedup():
    labels = field_labels(VIEW)
    assert labels[0] == "note"
    assert "username" in labels
    assert "password" in labels
    assert "totp" in labels
    assert "url: https://github.com" in labels
    assert "url: https://gist.github.com" in labels
    assert "field: API key" in labels
    assert "field: PIN" in labels
    assert len(labels) == len(set(labels))
    assert "urls" not in labels
    assert "totp_uri" not in labels
    assert "autofillOnLoad" not in labels


def test_field_labels_flat_content_shape():
    data = {"content": {"title": "Note", "note": "hello", "content": {}}}
    assert field_labels(data) == ["note"]


def test_field_value_note():
    assert field_value(VIEW, "note") == "work account"


def test_field_value_section_field():
    assert field_value(VIEW, "username") == "me"
    assert field_value(VIEW, "password") == "secret"


def test_field_value_url():
    assert field_value(VIEW, "url: https://gist.github.com") == "https://gist.github.com"


def test_field_value_totp_uses_passed_value():
    assert field_value(VIEW, "totp", totp="123456") == "123456"


def test_field_value_extra_fields():
    assert field_value(VIEW, "field: API key") == "sk-123"
    assert field_value(VIEW, "field: PIN") == "0000"
    assert field_value(VIEW, "field: missing") == ""


def test_field_value_unknown_returns_empty():
    assert field_value(VIEW, "nope") == ""


def test_field_value_ignores_non_string_section_values():
    data = json.loads(json.dumps(VIEW))
    data["item"]["content"]["content"]["Login"]["passkeys"] = [{"id": "x"}]
    labels = field_labels(data)
    assert "passkeys" not in labels


def test_field_labels_extra_fields_under_content():
    """Current pass-cli puts extra_fields inside item.content, not top-level.
    An item with only extra fields must still produce labels."""
    data = {
        "item": {
            "content": {
                "title": "Mors enheter",
                "note": "",
                "content": {
                    "Login": {
                        "email": "",
                        "username": "",
                        "password": "",
                        "urls": [],
                        "totp_uri": "",
                        "passkeys": [],
                        "autofill_urls": [],
                    }
                },
                "extra_fields": [
                    {"name": "iPhone & iPad PIN", "content": {"Hidden": "1111"}},
                    {"name": "MacBook", "content": {"Hidden": "2222"}},
                ],
            }
        }
    }
    labels = field_labels(data)
    assert labels == ["field: iPhone & iPad PIN", "field: MacBook"]
    assert field_value(data, "field: iPhone & iPad PIN") == "1111"
    assert field_value(data, "field: MacBook") == "2222"


def test_field_labels_empty_login_with_no_extra_fields_has_no_labels():
    data = {
        "item": {
            "content": {
                "title": "Empty",
                "note": "",
                "content": {"Login": {"username": "", "password": "", "urls": []}},
                "extra_fields": [],
            }
        }
    }
    assert field_labels(data) == []
