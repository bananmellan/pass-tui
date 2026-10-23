from pass_rofi.dedup import dedup_refs, display_duplicate_groups
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
