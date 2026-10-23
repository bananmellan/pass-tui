from pass_rofi.dedup import dedup_refs, display_duplicate_groups
from pass_rofi.models import ItemRef


def ref(share="s1", item="i1", title="GitHub", urls=("https://github.com",), vault="Personal", uuid=""):
    return ItemRef(share, item, title, urls, vault, "Login", uuid)


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


def test_dedup_refs_keeps_same_title_with_different_item_ids_and_no_uuid():
    """Two genuinely different items that happen to share a title (different
    item_id, no item_uuid) both stay."""
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s2", "i2", "GitHub", ("https://github.com",)),
    ]
    assert len(dedup_refs(refs)) == 2


def test_dedup_refs_dedups_same_item_uuid_with_different_item_ids():
    """pass-cli can hand out different item_ids for the same item (re-import,
    sync conflict) while the content-level item_uuid stays stable. Same
    item_uuid = same item, even with different item_ids."""
    refs = [
        ref("s1", "i1", "abundolive.se", ("https://abundolive.se",), "Personal", uuid="u-abc"),
        ref("s1", "i2", "abundolive.se", ("https://abundolive.se",), "Personal", uuid="u-abc"),
    ]
    result = dedup_refs(refs)
    assert len(result) == 1
    assert result[0].item_id == "i1"


def test_dedup_refs_different_uuids_with_same_title_both_stay():
    """Same title, different item_uuids — genuinely different items."""
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",), "Personal", uuid="u-1"),
        ref("s2", "i2", "GitHub", ("https://github.com",), "Work", uuid="u-2"),
    ]
    assert len(dedup_refs(refs)) == 2


def test_dedup_refs_uuid_wins_over_item_id():
    """A ref with a uuid and a ref with the same item_id but no uuid are
    different entries (the uuid identifies the real item)."""
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",), "Personal", uuid="u-1"),
        ref("s1", "i1", "GitHub", ("https://github.com",), "Personal", uuid=""),
    ]
    # First has uuid u-1, second has no uuid but same item_id i1.
    # The uuid'd one is kept; the uuid-less one is deduped by item_id
    # only against other uuid-less refs — so both stay here.
    assert len(dedup_refs(refs)) == 2


def test_dedup_refs_uuid_dedup_is_checked_before_item_id():
    """A uuid'd ref followed by a uuid-less ref with the same item_id:
    the uuid-less ref is not dropped just because the item_id matches a
    uuid'd ref (they track different identity spaces)."""
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",), "Personal", uuid="u-1"),
        ref("s1", "i1", "GitHub", ("https://github.com",), "Personal", uuid=""),
    ]
    result = dedup_refs(refs)
    assert len(result) == 2


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
