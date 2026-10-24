import json

from pass_tui.fields import field_labels, field_value

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


ALIAS_VIEW = {
    "item": {
        "content": {
            "title": "actress_revolving098@smail.me",
            "note": "created for newsletters",
            "content": {
                "Alias": {
                    "alias_prefix": "actress_revolving098",
                    "alias_suffix": "smail.me",
                    "mailboxes": [{"email": "me@proton.me", "name": "Main"}],
                    "enabled": True,
                    "block_reply": False,
                }
            },
        }
    }
}


def test_field_labels_alias_item_exposes_address_and_hides_structure():
    labels = field_labels(ALIAS_VIEW)
    # The address is the copyable payload.
    assert "email: actress_revolving098@smail.me" in labels
    # Structural keys are not offered as copyable fields.
    assert "alias_prefix" not in labels
    assert "alias_suffix" not in labels
    assert "mailboxes" not in labels
    assert "enabled" not in labels
    assert "block_reply" not in labels
    # The note is still there.
    assert "note" in labels


def test_field_value_alias_address_returns_address():
    assert field_value(ALIAS_VIEW, "email: actress_revolving098@smail.me") == "actress_revolving098@smail.me"


def test_field_labels_alias_with_precomposed_email():
    data = {
        "item": {
            "content": {
                "title": "shopping.abc@duck.com",
                "content": {
                    "Alias": {
                        "email": "shopping.abc@duck.com",
                        "mailboxes": [],
                    }
                },
            }
        }
    }
    labels = field_labels(data)
    assert "email: shopping.abc@duck.com" in labels
    assert field_value(data, "email: shopping.abc@duck.com") == "shopping.abc@duck.com"


def test_field_labels_login_with_email_field_still_shows_email():
    """A Login item's 'email' section field is a normal copyable field."""
    data = {
        "item": {
            "content": {
                "title": "GitHub",
                "content": {
                    "Login": {
                        "email": "me@example.com",
                        "username": "me",
                        "password": "secret",
                        "urls": [],
                    }
                },
            }
        }
    }
    labels = field_labels(data)
    assert "email" in labels
    assert field_value(data, "email") == "me@example.com"
    # No alias-style "email: ..." label for a Login.
    assert not any(label.startswith("email: ") for label in labels)


# ------------------------------------------------------------ per-type


def test_alias_with_top_level_alias_email():
    """Current pass-cli: AliasItem is a unit struct; the address is at
    top-level item.alias_email. This is the shape that produced
    "No readable fields in 'affection_thorn212@slmail.me'"."""
    data = {
        "item": {
            "id": "i1",
            "share_id": "s1",
            "alias_email": "affection_thorn212@slmail.me",
            "content": {
                "title": "affection_thorn212@slmail.me",
                "note": "",
                "content": {"Alias": {}},
                "extra_fields": [],
            },
        },
        "attachments": [],
    }
    labels = field_labels(data)
    assert labels == ["email: affection_thorn212@slmail.me"]
    assert field_value(data, "email: affection_thorn212@slmail.me") == "affection_thorn212@slmail.me"


def test_alias_address_falls_back_to_title():
    """Even with no alias_email anywhere, the title is the address."""
    data = {
        "item": {
            "content": {
                "title": "quiet_fog99@duck.com",
                "content": {"Alias": {}},
            }
        }
    }
    assert field_labels(data) == ["email: quiet_fog99@duck.com"]


def test_alias_with_note_keeps_note_first():
    data = {
        "item": {
            "alias_email": "a.b@slmail.me",
            "content": {
                "title": "a.b@slmail.me",
                "note": "newsletters",
                "content": {"Alias": {}},
            },
        }
    }
    labels = field_labels(data)
    assert labels == ["note", "email: a.b@slmail.me"]
    assert field_value(data, "note") == "newsletters"


def test_note_item_exposes_note():
    data = {
        "item": {
            "content": {
                "title": "Shopping list",
                "note": "milk, eggs",
                "content": {"Note": {}},
            }
        }
    }
    assert field_labels(data) == ["note"]
    assert field_value(data, "note") == "milk, eggs"


def test_credit_card_item_all_fields():
    data = {
        "item": {
            "content": {
                "title": "Visa",
                "note": "",
                "content": {
                    "CreditCard": {
                        "cardholder_name": "Ada Lovelace",
                        "card_type": "Visa",
                        "number": "4111111111111111",
                        "verification_number": "123",
                        "expiration_date": "12/28",
                        "pin": "0000",
                    }
                },
            }
        }
    }
    labels = field_labels(data)
    assert labels == [
        "cardholder_name",
        "number",
        "verification_number",
        "expiration_date",
        "pin",
    ]
    assert field_value(data, "number") == "4111111111111111"
    assert field_value(data, "pin") == "0000"


def test_identity_item_all_string_fields_and_groups():
    data = {
        "item": {
            "content": {
                "title": "Ada",
                "note": "",
                "content": {
                    "Identity": {
                        "full_name": "Ada Lovelace",
                        "email": "ada@example.com",
                        "phone_number": "+46 70 123 45 67",
                        "first_name": "Ada",
                        "middle_name": "",
                        "last_name": "Lovelace",
                        "birthdate": "1815-12-10",
                        "gender": "",
                        "extra_personal_details": [
                            {"name": "Citizenship", "content": {"Text": "SE"}}
                        ],
                        "organization": "Analytical Engines Ltd",
                        "street_address": "St James's Square",
                        "zip_or_postal_code": "SW1",
                        "city": "London",
                        "state_or_province": "",
                        "country_or_region": "UK",
                        "floor": "",
                        "county": "",
                        "extra_address_details": [],
                        "social_security_number": "123-45-6789",
                        "passport_number": "",
                        "license_number": "",
                        "website": "https://ada.example.com",
                        "x_handle": "",
                        "second_phone_number": "",
                        "linkedin": "",
                        "reddit": "",
                        "facebook": "",
                        "yahoo": "",
                        "instagram": "",
                        "extra_contact_details": [],
                        "company": "",
                        "job_title": "",
                        "personal_website": "",
                        "work_phone_number": "",
                        "work_email": "",
                        "extra_work_details": [],
                        "extra_sections": [
                            {
                                "section_name": "Bank",
                                "section_fields": [
                                    {"name": "Account", "content": {"Hidden": "SE123"}}
                                ],
                            }
                        ],
                    }
                },
            }
        }
    }
    labels = field_labels(data)
    assert "full_name" in labels
    assert "email" in labels
    assert "phone_number" in labels
    assert "birthdate" in labels
    assert "social_security_number" in labels
    assert "website" in labels
    assert "personal: Citizenship" in labels
    assert "Bank: Account" in labels
    assert field_value(data, "personal: Citizenship") == "SE"
    assert field_value(data, "Bank: Account") == "SE123"
    assert field_value(data, "social_security_number") == "123-45-6789"
    # Empty strings and sub-structure keys are not labels.
    assert "middle_name" not in labels
    assert "gender" not in labels
    assert "extra_personal_details" not in labels
    assert "extra_sections" not in labels


def test_ssh_key_item():
    data = {
        "item": {
            "content": {
                "title": "GitHub key",
                "content": {
                    "SshKey": {
                        "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n...",
                        "public_key": "ssh-ed25519 AAAA...",
                        "sections": [],
                    }
                },
            }
        }
    }
    labels = field_labels(data)
    assert labels == ["private_key", "public_key"]
    assert field_value(data, "public_key") == "ssh-ed25519 AAAA..."


def test_wifi_item():
    data = {
        "item": {
            "content": {
                "title": "Home wifi",
                "content": {
                    "Wifi": {
                        "ssid": "HonungNet",
                        "password": "hunter2",
                        "security": "Wpa2Personal",
                        "sections": [],
                    }
                },
            }
        }
    }
    labels = field_labels(data)
    assert labels == ["ssid", "password"]
    assert "security" not in labels
    assert field_value(data, "password") == "hunter2"


def test_custom_item_sections():
    data = {
        "item": {
            "content": {
                "title": "Router",
                "note": "admin panel creds",
                "content": {
                    "Custom": {
                        "sections": [
                            {
                                "section_name": "Admin",
                                "section_fields": [
                                    {"name": "user", "content": {"Text": "admin"}},
                                    {"name": "pass", "content": {"Hidden": "p4ss"}},
                                ],
                            },
                            {
                                "section_name": "",
                                "section_fields": [
                                    {"name": "Note field", "content": {"Text": "x"}},
                                ],
                            },
                        ]
                    }
                },
            }
        }
    }
    labels = field_labels(data)
    assert labels[0] == "note"
    assert "Admin: user" in labels
    assert "Admin: pass" in labels
    assert "Note field" in labels
    assert field_value(data, "Admin: pass") == "p4ss"


def test_login_autofill_urls_objects():
    """autofill_urls entries serialize as {"url", "mode"} objects."""
    data = {
        "item": {
            "content": {
                "title": "GitHub",
                "content": {
                    "Login": {
                        "email": "",
                        "username": "me",
                        "password": "secret",
                        "urls": ["https://github.com"],
                        "totp_uri": "",
                        "passkeys": [],
                        "autofill_urls": [
                            {"url": "https://github.com/login", "mode": "Exact"},
                            {"url": "https://github.com", "mode": "Default"},
                        ],
                    }
                },
            }
        }
    }
    labels = field_labels(data)
    assert "url: https://github.com" in labels
    assert "url: https://github.com/login" in labels
    # "mode" is not a copyable field.
    assert "mode" not in labels
    assert field_value(data, "url: https://github.com/login") == "https://github.com/login"


def test_extra_field_timestamp_value():
    data = {
        "item": {
            "content": {
                "title": "Thing",
                "content": {"Note": {}},
                "extra_fields": [
                    {"name": "Renewed", "content": {"Timestamp": 1735689600}},
                ],
            }
        }
    }
    labels = field_labels(data)
    assert labels == ["field: Renewed"]
    assert field_value(data, "field: Renewed") == "1735689600"


def test_extra_field_totp_content_uses_stored_value():
    data = {
        "item": {
            "content": {
                "title": "Thing",
                "content": {"Note": {}},
                "extra_fields": [
                    {"name": "Backup code", "content": {"Totp": "otpauth://totp/x"}},
                ],
            }
        }
    }
    assert field_value(data, "field: Backup code") == "otpauth://totp/x"


def test_android_allowed_apps():
    data = {
        "item": {
            "content": {
                "title": "Bank app",
                "content": {
                    "Login": {
                        "username": "me",
                        "password": "secret",
                        "urls": [],
                        "totp_uri": "",
                        "passkeys": [],
                        "autofill_urls": [],
                        "email": "",
                    }
                },
                "platform_specific": {
                    "android": {
                        "allowed_apps": [
                            {"package_name": "com.bank.app", "hashes": ["abc"], "app_name": "Bank"},
                        ]
                    }
                },
            }
        }
    }
    labels = field_labels(data)
    assert "android app: Bank (com.bank.app)" in labels
    assert field_value(data, "android app: Bank (com.bank.app)") == "com.bank.app"


def test_empty_everything_has_no_labels():
    data = {"item": {"content": {"title": "Empty", "note": "", "content": {"Note": {}}}}}
    assert field_labels(data) == []


def test_labels_never_duplicate():
    data = {
        "item": {
            "content": {
                "title": "X",
                "note": "n",
                "content": {
                    "Login": {
                        "email": "",
                        "username": "u",
                        "password": "p",
                        "urls": ["https://a.com", "https://a.com"],
                        "totp_uri": "otpauth://totp/x",
                        "passkeys": [],
                        "autofill_urls": [{"url": "https://a.com", "mode": "Exact"}],
                    }
                },
                "extra_fields": [
                    {"name": "PIN", "content": {"Hidden": "1"}},
                    {"name": "PIN", "content": {"Hidden": "2"}},
                ],
            }
        }
    }
    labels = field_labels(data)
    assert labels == ["note", "username", "password", "totp", "url: https://a.com", "field: PIN"]
    assert field_value(data, "field: PIN") == "1"
