from pass_tui.models import ItemRef
from pass_tui.search import filter_items, search_text


def ref(share="s1", item="i1", title="GitHub", urls=("https://github.com",), vault="Personal"):
    return ItemRef(share, item, title, urls, vault, "Login")


def test_empty_query_returns_all_in_order():
    refs = [ref("s1", "i2", "Bank"), ref("s1", "i1", "GitHub")]
    assert filter_items(refs, "") == refs
    assert filter_items(refs, "   ") == refs


def test_substring_match_on_title_case_insensitive():
    refs = [ref(), ref("s1", "i2", "GitLab", ("https://gitlab.com",)), ref("s1", "i3", "Bank", ("https://bank.example.com",))]
    assert [r.title for r in filter_items(refs, "git")] == ["GitHub", "GitLab"]


def test_substring_match_on_url():
    refs = [ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))]
    assert [r.title for r in filter_items(refs, "example.com")] == ["Bank"]


def test_substring_match_on_vault_name():
    refs = [ref(vault="Work"), ref("s1", "i2", "Bank", vault="Personal")]
    assert [r.title for r in filter_items(refs, "work")] == ["GitHub"]


def test_subsequence_match():
    refs = [ref(title="NextCloud"), ref("s1", "i2", "Bank", ("https://bank.example.com",))]
    assert [r.title for r in filter_items(refs, "nxld")] == ["NextCloud"]


def test_two_letter_subsequence_does_not_match():
    """Short tokens stay precise: subsequence matching needs >= 3 chars."""
    refs = [ref(title="NextCloud"), ref("s1", "i2", "Bank", ("https://bank.example.com",))]
    assert [r.title for r in filter_items(refs, "nc")] == []


def test_title_match_ranks_before_url_match():
    refs = [
        ref("s1", "i1", "Bank", ("https://money.example.com",)),
        ref("s1", "i2", "money", ("https://example.com",)),
    ]
    assert [r.title for r in filter_items(refs, "money")] == ["money", "Bank"]


def test_no_match_returns_empty():
    assert filter_items([ref()], "zzzzz") == []


def test_search_text_contains_title_urls_vault():
    text = search_text(ref(title="GitHub", urls=("https://github.com", "https://gist.github.com"), vault="Work"))
    assert "GitHub" in text
    assert "https://github.com" in text
    assert "https://gist.github.com" in text
    assert "Work" in text


def test_search_by_type_badge_finds_aliases():
    from pass_tui.models import ItemRef

    refs = [
        ItemRef("s1", "i1", "GitHub", ("https://github.com",), "Personal", "Login"),
        ItemRef("s1", "i2", "actress_revolving098@smail.me", (), "Personal", "Alias"),
        ItemRef("s1", "i3", "other@smail.me", (), "Personal", "Alias"),
    ]
    assert [r.title for r in filter_items(refs, "alias")] == [
        "actress_revolving098@smail.me",
        "other@smail.me",
    ]
    assert [r.title for r in filter_items(refs, "login")] == ["GitHub"]


def test_badge_match_ranks_aliases_above_title_contains():
    """Typing "alias" must put the two alias items first, above a login
    whose title merely contains the word "alias"."""
    from pass_tui.models import ItemRef

    refs = [
        ItemRef("s1", "i1", "alias-generator login", ("https://example.com",), "Personal", "Login"),
        ItemRef("s1", "i2", "x@smail.me", (), "Personal", "Alias"),
        ItemRef("s1", "i3", "y@smail.me", (), "Personal", "Alias"),
    ]
    result = filter_items(refs, "alias")
    assert [r.item_type for r in result] == ["Alias", "Alias", "Login"]


def test_badge_match_still_allows_other_tokens():
    """A query like "alias smail" matches aliases whose text contains both."""
    from pass_tui.models import ItemRef

    refs = [
        ItemRef("s1", "i1", "x@smail.me", (), "Personal", "Alias"),
        ItemRef("s1", "i2", "y@other.me", (), "Personal", "Alias"),
        ItemRef("s1", "i3", "GitHub", ("https://github.com",), "Personal", "Login"),
    ]
    assert [r.title for r in filter_items(refs, "alias smail")] == ["x@smail.me"]


def test_search_text_contains_badge():
    from pass_tui.models import ItemRef

    text = search_text(ItemRef("s1", "i1", "x@smail.me", (), "Personal", "Alias"))
    assert "alias" in text


def test_order_is_stable_within_rank():
    refs = [ref("s1", "i1", "Alpha git"), ref("s1", "i2", "Beta git"), ref("s1", "i3", "git")]
    result = filter_items(refs, "git")
    # "git" is a title substring of all three; original order kept.
    assert [r.title for r in result] == ["Alpha git", "Beta git", "git"]
