import json
import time

from pass_rofi.cache import CACHE_NAME, Cache, cache_age, dump_cache, load_cache, match_host
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
    assert load_cache(path) == refs


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


import pytest  # noqa: E402
