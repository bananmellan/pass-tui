from pass_tui.models import ItemRef
from pass_tui.search import _is_host_query, filter_items, search_text


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


# ------------------------------------------------------------ host queries


def _honung_refs():
    return [
        ItemRef("s", "1", "arrogance_backspin132@bi.honung.org", (), "v", "Alias"),
        ItemRef("s", "2", "chaplain_procreate523@bi.honung.org", (), "v", "Alias"),
        ItemRef("s", "3", "NextCloud", ("https://nextcloud.com/", "https://nc.honung.org/", "https://nc.srv/"), "v", "Login"),
        ItemRef("s", "4", "R2 Cloudflare", ("https://nc.honung.org/",), "v", "Login"),
        ItemRef("s", "5", "Google adam.thunberg@hotmail.com", ("https://accounts.google.com/signin/v2/s",), "v", "Login"),
        ItemRef("s", "6", "Lenovo", ("https://passport.lenovo.com/wauthen5/wa",), "v", "Login"),
    ]


def test_is_host_query():
    assert _is_host_query("nc.honung.org")
    assert _is_host_query("example.com:8080")
    assert _is_host_query("mistral.ai")
    assert not _is_host_query("github")  # no dot
    assert not _is_host_query("my search")  # space, not a host token
    assert not _is_host_query("https://nc.honung.org/apps")  # scheme+path
    assert not _is_host_query("")


def test_host_query_does_not_subsequence_match():
    """Regression: a clipboard host prefilled as the query must not pull
    in items that merely contain the host's characters in order (aliases
    at bi.honung.org, gmail titles, unrelated urls)."""
    refs = _honung_refs()
    result = filter_items(refs, "nc.honung.org")
    titles = [r.title for r in result]
    assert "NextCloud" in titles
    assert "R2 Cloudflare" in titles
    assert "arrogance_backspin132@bi.honung.org" not in titles
    assert "chaplain_procreate523@bi.honung.org" not in titles
    assert "Google adam.thunberg@hotmail.com" not in titles
    assert "Lenovo" not in titles


def test_host_query_substring_matches_title_and_urls():
    refs = _honung_refs()
    result = filter_items(refs, "nc.honung.org")
    # Both items whose urls contain the host as a substring match; the
    # title-substring one (none here) would rank first, then url matches
    # in stable (title-sorted) order.
    assert [r.title for r in result] == ["NextCloud", "R2 Cloudflare"]


def test_host_query_with_port_matches_url_with_port():
    refs = [ref("s1", "i1", "Router", ("http://router.local:8080/admin",))]
    assert [r.title for r in filter_items(refs, "router.local:8080")] == ["Router"]
    assert [r.title for r in filter_items(refs, "router.local")] == ["Router"]


def test_host_query_flag_forces_precise_matching():
    """Explicit host_query=True applies precise matching even to queries
    that don't look like hosts."""
    refs = [ref("s1", "i1", "GitHub", ("https://github.com",)),
            ref("s1", "i2", "Gothub Social", ("https://gothub.example.com",))]
    # Fuzzy: both match the subsequence "gthub".
    assert len(filter_items(refs, "gthub")) == 2
    # Precise: only the substring match survives.
    assert [r.title for r in filter_items(refs, "gothub", host_query=True)] == ["Gothub Social"]


def test_normal_fuzzy_queries_still_subsequence_match():
    """The precise host path must not leak into ordinary typing."""
    refs = [ref("s1", "i1", "GitHub"), ref("s1", "i2", "Gothub")]
    assert len(filter_items(refs, "ghub")) == 2
    # "git" is a subsequence of both titles, so both match.
    assert len(filter_items(refs, "git")) == 2
