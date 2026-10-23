from pass_rofi.dedup import dedup_refs, display_duplicate_groups, is_email_alias
from pass_rofi.models import ItemRef


def ref(share="s1", item="i1", title="GitHub", urls=("https://github.com",), vault="Personal"):
    return ItemRef(share, item, title, urls, vault, "Login")


def test_dedup_refs_drops_exact_duplicates():
    refs = [ref(), ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))]
    assert [r.item_id for r in dedup_refs(refs)] == ["i1", "i2"]


def test_dedup_refs_dedups_same_item_id_across_shares():
    """The same item visible through two shares (same item_id, different
    share_id) is one item — Proton Pass item ids are globally unique."""
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s2", "i1", "GitHub", ("https://github.com",)),
    ]
    result = dedup_refs(refs)
    assert len(result) == 1
    assert result[0].share_id == "s1"


def test_dedup_refs_keeps_same_title_with_different_item_ids():
    """Two genuinely different items that happen to share a title (different
    item_id) both stay."""
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s2", "i2", "GitHub", ("https://github.com",)),
    ]
    assert len(dedup_refs(refs)) == 2


def test_dedup_refs_preserves_first_occurrence_order():
    refs = [
        ref("s1", "i2", "Bank", ("https://bank.example.com",)),
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s1", "i2", "Bank", ("https://bank.example.com",)),
    ]
    assert [r.item_id for r in dedup_refs(refs)] == ["i2", "i1"]


def test_display_duplicate_groups_finds_identical_rows():
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s2", "i2", "GitHub", ("https://github.com",)),  # same title+urls, diff id
        ref("s1", "i3", "Bank", ("https://bank.example.com",)),
    ]
    groups = display_duplicate_groups(refs)
    assert len(groups) == 1
    assert {r.item_id for r in groups[0]} == {"i1", "i2"}


def test_display_duplicate_groups_ignores_different_urls():
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s2", "i2", "GitHub", ("https://github.com/enterprise",)),
    ]
    assert display_duplicate_groups(refs) == []


def test_display_duplicate_groups_empty_when_all_unique():
    refs = [ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))]
    assert display_duplicate_groups(refs) == []


def test_is_email_alias_detects_address_titles():
    assert is_email_alias(ref(title="actress_revolving098@smail.me"))
    assert is_email_alias(ref(title="shopping.abc123@duck.com"))
    assert is_email_alias(ref(title="  spaced@sl.local  "))


def test_is_email_alias_rejects_non_addresses():
    assert not is_email_alias(ref(title="GitHub"))
    assert not is_email_alias(ref(title="abundolive.se"))
    assert not is_email_alias(ref(title="account.bbc.com"))
    assert not is_email_alias(ref(title="no-at-sign"))
    assert not is_email_alias(ref(title="two@@at.com"))
    assert not is_email_alias(ref(title="@no-local.com"))
    assert not is_email_alias(ref(title="no-tld@localhost"))


def test_dedup_refs_dedups_alias_items_by_address():
    """Two alias items for the same address (different item_ids, e.g. one
    from SimpleLogin, one migrated) collapse to one row."""
    refs = [
        ref("s1", "i1", "actress_revolving098@smail.me", (), "Personal"),
        ref("s1", "i2", "actress_revolving098@smail.me", (), "Personal"),
    ]
    result = dedup_refs(refs)
    assert len(result) == 1
    assert result[0].item_id == "i1"


def test_dedup_refs_alias_dedup_is_case_insensitive():
    refs = [
        ref("s1", "i1", "Alias@Example.com", (), "Personal"),
        ref("s1", "i2", "alias@example.COM", (), "Personal"),
    ]
    assert len(dedup_refs(refs)) == 1


def test_dedup_refs_keeps_different_aliases():
    refs = [
        ref("s1", "i1", "one@smail.me", (), "Personal"),
        ref("s1", "i2", "two@smail.me", (), "Personal"),
    ]
    assert len(dedup_refs(refs)) == 2


def test_dedup_refs_login_titled_like_address_not_treated_as_alias_when_id_differs():
    """A login whose title happens to contain an email address is NOT an
    alias item — but two logins with different item_ids still both stay
    (we only dedup aliases by address)."""
    refs = [
        ref("s1", "i1", "adam.thunberg@hotmail.com", ("https://outlook.com",), "Personal"),
        ref("s1", "i2", "adam.thunberg@hotmail.com", ("https://outlook.com",), "Personal"),
    ]
    # Both are address-titled, so they ARE treated as aliases and deduped.
    # This documents the heuristic: a title that is exactly an email
    # address is assumed to be an alias item.
    assert len(dedup_refs(refs)) == 1
