import json
import time

from pass_rofi.cache import CACHE_NAME, Cache, cache_age, dump_cache, load_cache, match_host, sort_refs
from pass_rofi.dedup import dedup_refs
from pass_rofi.models import ItemRef
from pass_rofi.passcli import PassCli, PassCliError


def ref(share="s1", item="i1", title="GitHub", urls=("https://github.com",), vault="Personal"):
    return ItemRef(share, item, title, urls, vault, "Login")


def make_client(items=None, fail=False):
    def runner(argv):
        if argv[1:3] == ["vault", "list"]:
            return json.dumps({"vaults": [{"share_id": "s1", "name": "Personal"}]})
        if argv[1:4] == ["item", "list", "--share-id"]:
            if fail:
                raise PassCliError("offline")
            payload = {
                "items": [
                    {
                        "id": i.item_id,
                        "share_id": i.share_id,
                        "content": {
                            "title": i.title,
                            "content": {"Login": {"urls": list(i.urls)}},
                        },
                    }
                    for i in (items or [])
                ]
            }
            return json.dumps(payload)
        raise PassCliError(f"unexpected {argv}")

    return PassCli("pass-cli", runner=runner)


def test_roundtrip(tmp_path):
    path = tmp_path / CACHE_NAME
    refs = [ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))]
    dump_cache(refs, path)
    assert load_cache(path) == sort_refs(refs)


def test_dump_cache_stores_items_title_sorted(tmp_path):
    """The cache is written title-sorted so display order matches the file."""
    path = tmp_path / CACHE_NAME
    dump_cache([ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))], path)
    titles = [item["title"] for item in json.loads(path.read_text())["items"]]
    assert titles == ["Bank", "GitHub"]


def test_dedup_refs_drops_same_identity_keeps_first(tmp_path):
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s1", "i2", "Bank", ("https://bank.example.com",)),
    ]
    assert [r.item_id for r in dedup_refs(refs)] == ["i1", "i2"]


def test_dedup_refs_dedups_same_item_id_across_shares(tmp_path):
    """The same item visible through two shares (same item_id, different
    share_id) is one item — Proton Pass item ids are globally unique."""
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s2", "i1", "GitHub", ("https://github.com",)),
    ]
    result = dedup_refs(refs)
    assert len(result) == 1
    assert result[0].share_id == "s1"


def test_dedup_refs_keeps_same_title_with_different_item_ids(tmp_path):
    """Two genuinely different items that happen to share a title (different
    item_id) both stay."""
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s2", "i2", "GitHub", ("https://github.com",)),
    ]
    assert len(dedup_refs(refs)) == 2


def test_dedup_refs_dedups_same_id_with_different_titles(tmp_path):
    """Defensive: differing metadata on the same item_id is still one item."""
    refs = [
        ref("s1", "i1", "GitHub", ("https://github.com",)),
        ref("s1", "i1", "GitHub (old)", ()),
    ]
    assert [r.title for r in dedup_refs(refs)] == ["GitHub"]


def test_load_cache_dedups_stale_file(tmp_path):
    """A cache file written by an older version with duplicate entries is
    deduped on load so rofi never shows the same item twice."""
    path = tmp_path / CACHE_NAME
    payload = {
        "version": 1,
        "created_at": 0,
        "items": [
            ref("s1", "i1", "GitHub", ("https://github.com",)).to_dict(),
            ref("s1", "i1", "GitHub", ("https://github.com",)).to_dict(),
            ref("s1", "i2", "Bank", ("https://bank.example.com",)).to_dict(),
        ],
    }
    path.write_text(json.dumps(payload))
    assert [r.item_id for r in load_cache(path)] == ["i1", "i2"]


def test_dump_cache_dedups_before_writing(tmp_path):
    path = tmp_path / CACHE_NAME
    dump_cache(
        [
            ref("s1", "i1", "GitHub", ("https://github.com",)),
            ref("s1", "i1", "GitHub", ("https://github.com",)),
        ],
        path,
    )
    items = json.loads(path.read_text())["items"]
    assert len(items) == 1


def test_sort_refs_case_insensitive_title_then_vault(tmp_path):
    refs = [
        ref("s1", "i1", "github", (), "Work"),
        ref("s1", "i2", "Bank", (), "Personal"),
        ref("s1", "i3", "GitHub", (), "Personal"),
    ]
    # Case-insensitive: "github" and "GitHub" tie, vault breaks the tie
    # (same vault "Personal" vs "Work" ordering applies within the pair).
    assert [r.title for r in sort_refs(refs)] == ["Bank", "GitHub", "github"]
    # Same title: vault name breaks the tie.
    refs = [
        ref("s1", "i1", "GitHub", (), "Work"),
        ref("s1", "i2", "GitHub", (), "Personal"),
    ]
    assert [r.vault_name for r in sort_refs(refs)] == ["Personal", "Work"]


def test_cache_file_is_not_world_readable(tmp_path):
    path = tmp_path / CACHE_NAME
    dump_cache([ref()], path)
    assert path.stat().st_mode & 0o077 == 0


def test_cache_contains_no_secrets(tmp_path):
    path = tmp_path / CACHE_NAME
    client = make_client([ref()])
    cache = Cache(path, client)
    cache.rebuild()
    raw = path.read_text()
    assert "password" not in raw
    assert "secret" not in raw
    data = json.loads(raw)
    assert set(data) == {"version", "created_at", "items"}


def test_load_cache_dedups_by_item_uuid(tmp_path):
    """Two cache entries with the same item_uuid but different item_ids
    (pass-cli handed out a new item_id for the same item) collapse to one."""
    path = tmp_path / CACHE_NAME
    payload = {
        "version": 1,
        "created_at": 0,
        "items": [
            {
                "share_id": "s1",
                "item_id": "i1",
                "title": "abundolive.se",
                "urls": ["https://abundolive.se"],
                "vault_name": "Personal",
                "item_type": "Login",
                "item_uuid": "u-same",
            },
            {
                "share_id": "s1",
                "item_id": "i2",
                "title": "abundolive.se",
                "urls": ["https://abundolive.se"],
                "vault_name": "Personal",
                "item_type": "Login",
                "item_uuid": "u-same",
            },
            {
                "share_id": "s1",
                "item_id": "i3",
                "title": "Bank",
                "urls": ["https://bank.example.com"],
                "vault_name": "Personal",
                "item_type": "Login",
                "item_uuid": "u-other",
            },
        ],
    }
    path.write_text(json.dumps(payload))
    loaded = load_cache(path)
    assert [r.item_id for r in loaded] == ["i1", "i3"]


def test_cache_allows_item_uuid_key(tmp_path):
    """item_uuid is a known, non-secret metadata key and must be loaded."""
    path = tmp_path / CACHE_NAME
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "share_id": "s1",
                        "item_id": "i1",
                        "title": "GitHub",
                        "urls": [],
                        "vault_name": "",
                        "item_type": "",
                        "item_uuid": "u-1",
                    }
                ],
            }
        )
    )
    refs = load_cache(path)
    assert len(refs) == 1
    assert refs[0].item_uuid == "u-1"


def test_load_rejects_foreign_keys(tmp_path):
    path = tmp_path / CACHE_NAME
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "share_id": "s1",
                        "item_id": "i1",
                        "title": "GitHub",
                        "urls": [],
                        "vault_name": "",
                        "item_type": "",
                        "password": "leak",
                    }
                ],
            }
        )
    )
    assert load_cache(path) == []


def test_load_ignores_corrupt_and_wrong_version(tmp_path):
    path = tmp_path / CACHE_NAME
    path.write_text("not json")
    assert load_cache(path) == []
    path.write_text(json.dumps({"version": 999, "items": []}))
    assert load_cache(path) == []
    path.write_text(json.dumps({"version": 1, "items": "nope"}))
    assert load_cache(path) == []


def test_cache_age(tmp_path):
    path = tmp_path / CACHE_NAME
    assert cache_age(path) is None
    dump_cache([ref()], path)
    assert cache_age(path, now=time.time() + 10) == pytest.approx(10, abs=2)


def test_get_builds_cache_when_missing(tmp_path):
    path = tmp_path / CACHE_NAME
    cache = Cache(path, make_client([ref()]))
    refs = cache.get()
    assert [r.title for r in refs] == ["GitHub"]
    assert path.exists()


def test_get_serves_stale_cache_and_refreshes(tmp_path):
    path = tmp_path / CACHE_NAME
    refs = [ref()]
    dump_cache(refs, path)
    old = time.time() - 10_000
    path.touch()
    import os

    os.utime(path, (old, old))
    cache = Cache(path, make_client([ref(), ref("s1", "i2", "New", ())]), ttl=600)
    fresh = cache.get()
    assert {r.title for r in fresh} == {"GitHub", "New"}


def test_get_keeps_stale_cache_when_refresh_fails(tmp_path):
    path = tmp_path / CACHE_NAME
    dump_cache([ref()], path)
    import os

    old = time.time() - 10_000
    os.utime(path, (old, old))
    cache = Cache(path, make_client(fail=True), ttl=600)
    refs = cache.get()
    assert [r.title for r in refs] == ["GitHub"]


def test_ensure_fresh_async_spawns_once(tmp_path):
    path = tmp_path / CACHE_NAME
    dump_cache([ref()], path)
    import os

    old = time.time() - 10_000
    os.utime(path, (old, old))
    cache = Cache(path, make_client([ref()]), ttl=600)
    spawned = []
    assert cache.ensure_fresh_async(lambda c: spawned.append(c)) is True
    assert len(spawned) == 1
    assert (tmp_path / "rebuild.lock").exists()
    assert cache.ensure_fresh_async(lambda c: spawned.append(c)) is False
    assert len(spawned) == 1


def test_ensure_fresh_async_skips_fresh_cache(tmp_path):
    path = tmp_path / CACHE_NAME
    dump_cache([ref()], path)
    cache = Cache(path, make_client([ref()]), ttl=600)
    assert cache.ensure_fresh_async(lambda c: None) is False


def test_match_host_is_case_insensitive_substring(tmp_path):
    refs = [
        ref(urls=("https://GitHub.com/login",)),
        ref("s1", "i2", "Bank", ("https://bank.example.com",)),
        ref("s1", "i3", "NoUrl", ()),
    ]
    assert [r.title for r in match_host(refs, "github.com")] == ["GitHub"]
    assert [r.title for r in match_host(refs, "EXAMPLE.com")] == ["Bank"]
    assert match_host(refs, "missing.dev") == []


def test_match_host_fuzzy_path_and_trailing_slash():
    """A clipboard URL with a deep path matches items with the bare host."""
    refs = [
        ref(urls=("https://nc.honung.org",)),
        ref("s1", "i2", "NC app", ("https://nc.honung.org/apps/dashboard/",)),
        ref("s1", "i3", "NC trailing", ("https://nc.honung.org/",)),
        ref("s1", "i4", "Other", ("https://other.example.com",)),
    ]
    matches = match_host(refs, "nc.honung.org")
    assert {r.title for r in matches} == {"GitHub", "NC app", "NC trailing"}


def test_match_host_empty_host_matches_nothing():
    refs = [ref()]
    assert match_host(refs, "") == []
    assert match_host(refs, "/") == []


def test_match_host_with_port():
    refs = [
        ref(urls=("http://localhost:8080/app",)),
        ref("s1", "i2", "Other", ("http://localhost/app",)),
    ]
    matches = match_host(refs, "localhost:8080")
    assert [r.title for r in matches] == ["GitHub"]


import pytest  # noqa: E402


def test_match_host_ranks_exact_host_first():
    """The user's case: clipboard nc.honung.org, two candidates —
    'R2 Cloudflare' (single url https://nc.honung.org/) and 'NextCloud'
    (three urls including nc.honung.org). NextCloud should rank first:
    its title contains the host label and it has an exact-host url, while
    R2's title doesn't mention it."""
    refs = [
        ref("s1", "i1", "R2 Cloudflare", ("https://nc.honung.org/",)),
        ref(
            "s1",
            "i2",
            "NextCloud",
            ("https://nextcloud.com/", "https://nc.honung.org/", "https://nc.srv/"),
        ),
    ]
    matches = match_host(refs, "nc.honung.org")
    assert [r.title for r in matches] == ["NextCloud", "R2 Cloudflare"]


def test_match_host_exact_host_beats_substring():
    refs = [
        ref("s1", "i1", "Substring", ("https://xnc.honung.org.evil.com",)),
        ref("s1", "i2", "Exact", ("https://nc.honung.org",)),
    ]
    matches = match_host(refs, "nc.honung.org")
    assert matches[0].title == "Exact"


def test_match_host_subdomain_beats_contains():
    refs = [
        ref("s1", "i1", "Contains", ("https://notnc.honung.org.example.com",)),
        ref("s1", "i2", "Sub", ("https://nc.honung.org",)),
    ]
    matches = match_host(refs, "honung.org")
    assert matches[0].title == "Sub"


def test_match_host_title_label_tiebreak():
    refs = [
        ref("s1", "i1", "Zebra", ("https://github.com",)),
        ref("s1", "i2", "GitHub", ("https://github.com",)),
    ]
    matches = match_host(refs, "github.com")
    assert matches[0].title == "GitHub"


def test_match_host_deterministic_order():
    refs = [
        ref("s1", "i1", "Beta", ("https://github.com",)),
        ref("s1", "i2", "Alpha", ("https://github.com",)),
    ]
    first = match_host(refs, "github.com")
    second = match_host(list(reversed(refs)), "github.com")
    assert [r.title for r in first] == [r.title for r in second] == ["Alpha", "Beta"]
