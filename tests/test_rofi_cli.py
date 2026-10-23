import json
from pathlib import Path

import pytest

from pass_rofi import cli as cli_mod
from pass_rofi.cache import CACHE_NAME, dump_cache
from pass_rofi.config import Config
from pass_rofi.models import ItemRef
from pass_rofi.passcli import PassCli, PassCliError


def ref(share="s1", item="i1", title="GitHub", urls=("https://github.com",), vault="Personal"):
    return ItemRef(share, item, title, urls, vault, "Login")


VIEW_JSON = json.dumps(
    {
        "item": {
            "content": {
                "title": "GitHub",
                "note": "work account",
                "content": {
                    "Login": {
                        "username": "me",
                        "password": "secret",
                        "urls": ["https://github.com"],
                        "totp_uri": "otpauth://totp/x",
                    }
                },
            }
        }
    }
)


class FakeClipboard:
    def __init__(self, text=""):
        self.text = text
        self.written = []

    def read(self):
        return self.text

    def write(self, value):
        self.written.append(value)


class FakeClient:
    def __init__(self, view=VIEW_JSON, totp="123456"):
        self.view = view
        self.totp_value = totp
        self.view_calls = []
        self.totp_calls = []

    def list_all_items(self):
        return []

    def view_item_with_retry(self, item_ref):
        self.view_calls.append(item_ref)
        return json.loads(self.view)

    def totp(self, item_ref):
        self.totp_calls.append(item_ref)
        return self.totp_value


@pytest.fixture()
def env(tmp_path):
    return {
        "PASS_ROFI_CACHE_DIR": str(tmp_path / "cache"),
        "PATH": "/usr/bin:/bin",
    }


def run_full(argv, env, capsys, client=None, clipboard=None):
    config = Config.from_env(env)
    if clipboard is not None and config.verbose and getattr(clipboard, "_log", None) is None:
        clipboard._log = lambda msg: cli_mod._verbose(config, Path(config.cache_dir), msg)
    rc = cli_mod.run_rofi_mode(
        argv,
        env,
        config=config,
        client=client or FakeClient(),
        clipboard=clipboard or FakeClipboard(),
    )
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def run(argv, env, capsys, client=None, clipboard=None):
    rc, out, _err = run_full(argv, env, capsys, client=client, clipboard=clipboard)
    return rc, out


def test_initial_lists_cached_items_with_info_sorted(env, capsys, tmp_path):
    """Initial launch lists every cached item, title-sorted, with row metadata."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    dump_cache([ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))], cache_dir / CACHE_NAME)
    rc, out = run([], env, capsys)
    assert rc == 0
    lines = out.splitlines()
    assert "\x00use-hot-keys\x1ftrue" in lines[0]
    assert "\x00markup-rows\x1ftrue" in lines[1]
    assert any(line.startswith("\x00prompt\x1f") for line in lines)
    assert cli_mod.REFRESH_ROW in lines
    item_lines = [line for line in lines if line.startswith("item\x1f")]
    assert len(item_lines) == 2
    # Title-sorted: Bank before GitHub.
    assert "Bank" in item_lines[0]
    assert "GitHub" in item_lines[1]
    assert any("\x00display\x1f" in line and "<b>GitHub</b>" in line for line in item_lines)
    assert any("\x1fmeta\x1f" in line and "GitHub" in line for line in item_lines)
    assert any("\x1finfo\x1fitem\x1fs1\x1fi1" in line for line in item_lines)


def test_initial_clipboard_match_prefills_filter_and_ranks_first(env, capsys):
    """With a clipboard URL, the prompt is prefilled with the host and the
    best matches are printed first in best-match order."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache(
        [
            ref("s1", "i1", "R2 Cloudflare", ("https://nc.honung.org/",)),
            ref("s1", "i2", "NextCloud", ("https://nextcloud.com/", "https://nc.honung.org/", "https://nc.srv/")),
            ref("s1", "i3", "Aardvark Bank", ("https://bank.example.com",)),
        ],
        cache_dir / CACHE_NAME,
    )
    client = FakeClient()
    rc, out = run(
        [], env, capsys, client=client,
        clipboard=FakeClipboard("https://nc.honung.org/apps/dashboard/"),
    )
    assert rc == 0
    assert "\x00filter\x1fnc.honung.org" in out.splitlines()
    assert "\x00prompt\x1fProton Pass (nc.honung.org)" in out.splitlines()
    item_lines = [line for line in out.splitlines() if line.startswith("item\x1f")]
    # All items are printed (live filtering needs them); matches come first,
    # in best-match order (NextCloud's title relates to the host, so it
    # outranks the single-url R2 item despite the extra urls).
    assert len(item_lines) == 3
    assert "NextCloud" in item_lines[0]
    assert "R2 Cloudflare" in item_lines[1]
    assert "Aardvark Bank" in item_lines[2]
    assert not client.view_calls


def test_initial_rebuilds_when_cache_missing(env, capsys, monkeypatch):
    spawned = []
    monkeypatch.setattr(cli_mod, "_spawn_rebuild", lambda config: spawned.append(config))
    client = FakeClient()
    client.list_all_items = lambda: [ref()]
    rc, out = run([], env, capsys, client=client)
    assert rc == 0
    assert "GitHub" in out
    assert (tmp_path_cache(env) / CACHE_NAME).exists()


def tmp_path_cache(env):
    from pathlib import Path

    return Path(env["PASS_ROFI_CACHE_DIR"])


def test_initial_clipboard_deep_path_matches_bare_host_items(env, capsys):
    """Clipboard https://nc.honung.org/apps/dashboard/ matches items whose
    urls are https://nc.honung.org (fuzzy, trailing slashes, paths)."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache(
        [
            ref("s1", "i1", "NC", ("https://nc.honung.org",)),
            ref("s1", "i2", "NC app", ("https://nc.honung.org/apps/dashboard/",)),
            ref("s1", "i3", "Bank", ("https://bank.example.com",)),
        ],
        cache_dir / CACHE_NAME,
    )
    client = FakeClient()
    rc, out = run(
        [], env, capsys, client=client,
        clipboard=FakeClipboard("https://nc.honung.org/apps/dashboard/"),
    )
    assert rc == 0
    assert "\x00filter\x1fnc.honung.org" in out.splitlines()
    item_lines = [line for line in out.splitlines() if line.startswith("item\x1f")]
    assert len(item_lines) == 3
    # Both matches rank above the unrelated Bank item.
    assert "NC" in item_lines[0] or "NC app" in item_lines[0]
    assert "NC" in item_lines[1] or "NC app" in item_lines[1]
    assert "Bank" in item_lines[2]
    assert not client.view_calls


def test_initial_clipboard_single_match_opens_item(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache(
        [ref(), ref("s1", "i2", "GitLab", ("https://gitlab.com",))],
        cache_dir / CACHE_NAME,
    )
    client = FakeClient()
    rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard("https://github.com/login"))
    assert rc == 0
    assert client.view_calls and client.view_calls[0].item_id == "i1"
    assert "username" in out
    assert "password" in out
    assert "secret" not in out


def test_initial_clipboard_multiple_matches_lists_them(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache(
        [
            ref(),
            ref("s1", "i2", "GitHub Enterprise", ("https://github.com/enterprise",)),
            ref("s1", "i3", "Bank", ("https://bank.example.com",)),
        ],
        cache_dir / CACHE_NAME,
    )
    client = FakeClient()
    rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard("https://github.com"))
    assert rc == 0
    assert "\x00filter\x1fgithub.com" in out.splitlines()
    item_lines = [line for line in out.splitlines() if line.startswith("item\x1f")]
    assert len(item_lines) == 3
    assert "GitHub" in item_lines[0] and "GitHub Enterprise" in item_lines[1]
    assert "Bank" in item_lines[2]
    assert not client.view_calls


def test_initial_clipboard_no_match_shows_full_list(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    rc, out = run([], env, capsys, clipboard=FakeClipboard("https://unrelated.dev/page"))
    assert cli_mod.REFRESH_ROW in out
    assert "GitHub" in out
    assert "\x00filter\x1f" not in out


def test_initial_clipboard_non_url_ignored(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    rc, out = run([], env, capsys, clipboard=FakeClipboard("my password is hunter2"))
    assert "GitHub" in out


def test_initial_binary_clipboard_does_not_crash(env, capsys):
    """A binary clipboard (image bytes decoded with replacement) is ignored."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    rc, out = run([], env, capsys, clipboard=FakeClipboard("\ufffd\x89PNG\ufffd"))
    assert rc == 0
    assert "GitHub" in out


def test_clipboard_match_runs_even_without_recent_copy(env, capsys):
    """Clipboard URL matching must not be gated on a recent copy."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    from pass_rofi.state import StateStore

    state = StateStore(cache_dir)
    state.set_last_copied(ref(), timestamp=0)
    client = FakeClient()
    rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard("https://github.com/login"))
    assert rc == 0
    assert client.view_calls and client.view_calls[0].item_id == "i1"


def test_clipboard_match_reruns_when_clipboard_changes(env, capsys):
    """A new clipboard URL re-triggers matching even if the marker was set."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache(
        [ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))],
        cache_dir / CACHE_NAME,
    )
    client = FakeClient()
    rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard("https://github.com"))
    assert client.view_calls and client.view_calls[0].item_id == "i1"
    capsys.readouterr()
    rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard("https://bank.example.com"))
    assert client.view_calls[-1].item_id == "i2"


def test_clipboard_match_every_launch_with_url(env, capsys):
    """Copying a link and launching must match on every launch while the
    clipboard keeps holding that URL — the marker must not suppress it."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    for _ in range(3):
        rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard("https://github.com/login"))
        assert rc == 0
        capsys.readouterr()
    assert len(client.view_calls) == 3


def test_copy_then_launch_with_url_still_matches(env, capsys):
    """After copying a field (clipboard now holds a secret), a later launch
    with a URL in the clipboard must still match that URL."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    clipboard = FakeClipboard()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["item\x1fs1\x1fi1"], env2, capsys, client=client, clipboard=clipboard)
    capsys.readouterr()
    env3 = dict(env, ROFI_RETV="1", ROFI_INFO="password")
    run(["password"], env3, capsys, client=client, clipboard=clipboard)
    assert clipboard.written == ["secret"]
    capsys.readouterr()
    client.view_calls.clear()
    rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard("https://github.com"))
    assert client.view_calls and client.view_calls[0].item_id == "i1"


def test_open_item_does_not_set_last_copied(env, capsys):
    """Opening an item must not arm the reopen-after-copy window."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    from pass_rofi.state import StateStore

    state = StateStore(cache_dir)
    client = FakeClient()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["item\x1fs1\x1fi1"], env2, capsys, client=client)
    assert state.get_last_copied() is None
    assert state.get_open_item() == ref()


def test_reopen_after_close_without_copy_does_not_happen(env, capsys):
    """Close without copying, relaunch: full list, not the last open item."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["item\x1fs1\x1fi1"], env2, capsys, client=client)
    capsys.readouterr()
    client.view_calls.clear()
    rc, out = run([], env, capsys, client=client)
    assert rc == 0
    assert cli_mod.REFRESH_ROW in out
    assert not client.view_calls


def test_reopen_after_copy_is_one_shot(env, capsys):
    """Copy, relaunch (reopens the item), close without copying, relaunch
    again: the remembered item must be gone — full list, not the item."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    from pass_rofi.state import StateStore

    state = StateStore(cache_dir)
    client = FakeClient()
    clipboard = FakeClipboard()
    # Open the item, then copy a field.
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["item\x1fs1\x1fi1"], env2, capsys, client=client, clipboard=clipboard)
    capsys.readouterr()
    env3 = dict(env, ROFI_RETV="1", ROFI_INFO="password")
    run(["password"], env3, capsys, client=client, clipboard=clipboard)
    assert clipboard.written == ["secret"]
    assert state.get_last_copied() is not None
    capsys.readouterr()
    # First relaunch: reopens the copied item.
    client.view_calls.clear()
    rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard(""))
    assert rc == 0
    assert client.view_calls and client.view_calls[0].item_id == "i1"
    # The reopen memory is consumed by that launch.
    assert state.get_last_copied() is None
    capsys.readouterr()
    # Second relaunch (nothing copied in between): plain list.
    client.view_calls.clear()
    rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard(""))
    assert rc == 0
    assert cli_mod.REFRESH_ROW in out
    assert not client.view_calls


def test_back_to_list_reads_cache_without_rebuild(env, capsys, monkeypatch):
    """Returning to the item list must not hit pass-cli (no TTL refresh)."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    import os
    import time as time_mod

    old = time_mod.time() - 10_000
    os.utime(cache_dir / CACHE_NAME, (old, old))
    client = FakeClient()

    def boom():
        raise AssertionError("pass-cli must not be called when going back")

    client.list_all_items = boom
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["item\x1fs1\x1fi1"], env2, capsys, client=client)
    capsys.readouterr()
    rc, out = run([], dict(env, ROFI_RETV="10"), capsys, client=client)
    assert rc == 0
    assert "GitHub" in out


def test_field_copy_uses_open_item(env, capsys):
    """Copying a field resolves the item from open-item state."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    clipboard = FakeClipboard()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["item\x1fs1\x1fi1"], env2, capsys, client=client, clipboard=clipboard)
    capsys.readouterr()
    env3 = dict(env, ROFI_RETV="1", ROFI_INFO="username")
    run(["username"], env3, capsys, client=client, clipboard=clipboard)
    assert clipboard.written == ["me"]


def test_select_item_by_info_opens_fields(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    rc, out = run(["GitHub"], env2, capsys, client=client)
    assert rc == 0
    assert client.view_calls
    assert "username" in out
    assert "url: https://github.com" in out
    assert "totp" in out
    assert "secret" not in out


def test_select_item_by_token_argv_opens_fields(env, capsys):
    """rofi passes the entry text (the token) as argv[1]; ids come from ROFI_INFO."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    rc, out = run(["item\x1fs1\x1fi1"], env2, capsys, client=client)
    assert rc == 0
    assert client.view_calls and client.view_calls[0].item_id == "i1"
    assert "username" in out


def test_select_item_token_argv_without_info_opens_fields(env, capsys):
    """Even if ROFI_INFO is missing, the token in argv[1] resolves the item."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    env2 = dict(env, ROFI_RETV="1")
    rc, out = run(["item\x1fs1\x1fi1"], env2, capsys, client=client)
    assert rc == 0
    assert client.view_calls and client.view_calls[0].item_id == "i1"


def test_select_duplicate_titles_resolves_exact_item(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache(
        [ref(), ref("s2", "i2", "GitHub", ("https://github.com/other",), "Work")],
        cache_dir / CACHE_NAME,
    )
    client = FakeClient()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs2\x1fi2")
    rc, out = run(["item\x1fs2\x1fi2"], env2, capsys, client=client)
    assert rc == 0
    assert client.view_calls[0].share_id == "s2"
    assert client.view_calls[0].item_id == "i2"


def test_select_field_copies_value(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    clipboard = FakeClipboard()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["item\x1fs1\x1fi1"], env2, capsys, client=client, clipboard=clipboard)
    capsys.readouterr()
    env3 = dict(env, ROFI_RETV="1", ROFI_INFO="password")
    rc, out = run(["password"], env3, capsys, client=client, clipboard=clipboard)
    assert rc == 0
    assert clipboard.written == ["secret"]


def test_select_field_bare_label_argv_copies_value(env, capsys):
    """Field rows are plain labels as entry text; argv[1] carries the label."""
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    clipboard = FakeClipboard()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["item\x1fs1\x1fi1"], env2, capsys, client=client, clipboard=clipboard)
    capsys.readouterr()
    env3 = dict(env, ROFI_RETV="1")
    rc, out = run(["password"], env3, capsys, client=client, clipboard=clipboard)
    assert rc == 0
    assert clipboard.written == ["secret"]


def test_select_totp_copies_code(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    clipboard = FakeClipboard()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["GitHub"], env2, capsys, client=client, clipboard=clipboard)
    capsys.readouterr()
    env3 = dict(env, ROFI_RETV="1", ROFI_INFO="totp")
    run(["totp"], env3, capsys, client=client, clipboard=clipboard)
    assert clipboard.written == ["123456"]
    assert client.totp_calls


def test_select_url_field_copies_url(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    clipboard = FakeClipboard()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["GitHub"], env2, capsys, client=client, clipboard=clipboard)
    capsys.readouterr()
    env3 = dict(env, ROFI_RETV="1", ROFI_INFO="url: https://github.com")
    run(["url: https://github.com"], env3, capsys, client=client, clipboard=clipboard)
    assert clipboard.written == ["https://github.com"]


def test_select_refresh_rebuilds(env, capsys, monkeypatch):
    spawned = []
    monkeypatch.setattr(cli_mod, "_spawn_rebuild", lambda config: spawned.append(config))
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    client.list_all_items = lambda: [ref(), ref("s1", "i9", "New", ())]
    env2 = dict(env, ROFI_RETV="1")
    rc, out = run([cli_mod.REFRESH_ROW], env2, capsys, client=client)
    assert rc == 0
    assert "New" in out
    assert "GitHub" in out


def test_custom_keybinding_refreshes(env, capsys, monkeypatch):
    """Ctrl+L triggers a background rebuild when the cache is stale."""
    spawned = []
    monkeypatch.setattr(cli_mod, "_spawn_rebuild", lambda config: spawned.append(config))
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    import os
    import time as time_mod

    old = time_mod.time() - 10_000
    os.utime(cache_dir / CACHE_NAME, (old, old))
    client = FakeClient()
    env2 = dict(env, ROFI_RETV="10")
    rc, out = run([], env2, capsys, client=client)
    assert rc == 0
    assert "GitHub" in out
    assert "\x00use-hot-keys\x1ftrue" in out
    assert spawned, "stale cache should trigger a background rebuild"


def test_custom_keybinding_refresh_failure_still_lists(env, capsys, monkeypatch):
    """Ctrl+L with a failing pass-cli still returns to the lazy list."""
    monkeypatch.setattr(cli_mod, "_spawn_rebuild", lambda config: None)
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()

    def boom():
        raise PassCliError("offline")

    client.list_all_items = boom
    rc, out = run([], dict(env, ROFI_RETV="10"), capsys, client=client)
    assert rc == 0
    assert "GitHub" in out


def test_custom_keybinding_range_all_refresh(env, capsys, monkeypatch):
    """Every custom keybinding slot (10-28) resets to the item list."""
    monkeypatch.setattr(cli_mod, "_spawn_rebuild", lambda config: None)
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    client.list_all_items = lambda: [ref()]
    for retv in ("10", "19", "28"):
        rc, out = run([], dict(env, ROFI_RETV=retv), capsys, client=client)
        assert rc == 0
        assert "GitHub" in out
        capsys.readouterr()


def test_custom_keybinding_from_fields_stage_returns_to_list(env, capsys, monkeypatch):
    """Ctrl+L while in the field menu goes back to the item list."""
    monkeypatch.setattr(cli_mod, "_spawn_rebuild", lambda config: None)
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    client.list_all_items = lambda: [ref()]
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="item\x1fs1\x1fi1")
    run(["item\x1fs1\x1fi1"], env2, capsys, client=client)
    capsys.readouterr()
    rc, out = run([], dict(env, ROFI_RETV="10"), capsys, client=client)
    assert rc == 0
    assert cli_mod.REFRESH_ROW in out
    assert "GitHub" in out


def test_list_emits_hot_keys_and_markup_headers(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    rc, out = run([], env, capsys)
    lines = out.splitlines()
    assert lines[0] == "\x00use-hot-keys\x1ftrue"
    assert lines[1] == "\x00markup-rows\x1ftrue"
    assert lines[2].startswith("\x00prompt\x1f")


def test_select_unknown_item_returns_to_list(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    env2 = dict(env, ROFI_RETV="1")
    rc, out = run(["Nonexistent"], env2, capsys)
    assert rc == 0
    assert cli_mod.REFRESH_ROW in out


def test_reopen_last_copied_within_window(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    from pass_rofi.state import StateStore

    state = StateStore(cache_dir)
    state.set_last_copied(ref())
    client = FakeClient()
    clipboard = FakeClipboard()
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO="password")
    run(["password"], env2, capsys, client=client, clipboard=clipboard)
    capsys.readouterr()
    rc, out = run([], env, capsys, client=client, clipboard=clipboard)
    assert rc == 0
    assert "username" in out


def test_reopen_ignored_after_window_expires(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    from pass_rofi.state import StateStore

    state = StateStore(cache_dir)
    state.set_last_copied(ref(), timestamp=0)
    client = FakeClient()
    rc, out = run([], env, capsys, client=client)
    assert cli_mod.REFRESH_ROW in out
    assert not client.view_calls


def test_run_rebuild_writes_cache(env, monkeypatch):
    monkeypatch.setattr(cli_mod, "_spawn_rebuild", lambda config: None)
    config = Config.from_env(env)
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)

    real_passcli = PassCli

    class StubPassCli(real_passcli):
        def __init__(self, binary="pass-cli", **kwargs):
            def runner(argv):
                if argv[1:3] == ["vault", "list"]:
                    return json.dumps({"vaults": [{"share_id": "s1", "name": "Personal"}]})
                if argv[1:4] == ["item", "list", "--share-id"]:
                    return json.dumps(
                        {
                            "items": [
                                {
                                    "id": "i1",
                                    "share_id": "s1",
                                    "content": {
                                        "title": "GitHub",
                                        "content": {"Login": {"urls": ["https://github.com"]}},
                                    },
                                }
                            ]
                        }
                    )
                raise PassCliError("unexpected")

            super().__init__(binary, runner=runner)

    monkeypatch.setattr(cli_mod, "PassCli", StubPassCli)
    assert cli_mod.run_rebuild(config=config, env=env) == 0
    from pass_rofi.cache import load_cache

    refs = load_cache(cache_dir / CACHE_NAME)
    assert [r.title for r in refs] == ["GitHub"]
    assert not (cache_dir / "rebuild.lock").exists()


def test_verbose_logs_clipboard_decision_points(env, capsys, tmp_path):
    """--verbose (PASS_ROFI_VERBOSE=1) logs clipboard text, host, cache
    stats and match count to stderr."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    env_v = dict(env, PASS_ROFI_VERBOSE="1")
    client = FakeClient()
    rc, out, err = run_full(
        [], env_v, capsys, client=client, clipboard=FakeClipboard("https://github.com/login")
    )
    assert rc == 0
    assert "extracted host: 'github.com'" in err
    assert "cache: 1 items, 1 with urls" in err
    assert "matched 1 items" in err
    assert client.view_calls


def test_verbose_off_by_default_no_stderr(env, capsys, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    rc, out = run([], env, capsys, client=client, clipboard=FakeClipboard("https://github.com"))
    err = capsys.readouterr().err
    assert rc == 0
    assert "pass-rofi:" not in err


def test_verbose_logs_no_host_when_clipboard_not_url(env, capsys, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    env_v = dict(env, PASS_ROFI_VERBOSE="1")
    client = FakeClient()
    rc, out, err = run_full(
        [], env_v, capsys, client=client, clipboard=FakeClipboard("plain text")
    )
    assert rc == 0
    assert "extracted host: ''" in err
    assert "clipboard matching did not handle the launch" in err
    assert cli_mod.REFRESH_ROW in out


def test_main_verbose_flag_sets_env(monkeypatch, tmp_path):
    """--verbose on the CLI entry point enables PASS_ROFI_VERBOSE."""
    captured = {}

    def fake_run_rofi_mode(argv, env):
        captured["env"] = env
        return 0

    monkeypatch.setattr(cli_mod, "run_rofi_mode", fake_run_rofi_mode)
    monkeypatch.setenv("PASS_ROFI_CACHE_DIR", str(tmp_path))
    assert cli_mod.main(["--verbose"]) == 0
    assert captured["env"]["PASS_ROFI_VERBOSE"] == "1"
    assert cli_mod.main([]) == 0
    assert captured["env"].get("PASS_ROFI_VERBOSE") is None


def test_open_item_with_only_extra_fields_shows_them(env, capsys, tmp_path):
    """An item whose Login section is empty but which has extra_fields under
    item.content (current pass-cli shape) must open and list those fields."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    view = json.dumps(
        {
            "item": {
                "content": {
                    "title": "GitHub",
                    "note": "",
                    "content": {"Login": {"username": "", "password": "", "urls": []}},
                    "extra_fields": [
                        {"name": "PIN", "content": {"Hidden": "0000"}},
                    ],
                }
            }
        }
    )
    client = FakeClient(view=view)
    sep = "\x1f"
    env2 = dict(env, ROFI_RETV="1", ROFI_INFO=f"item{sep}s1{sep}i1")
    rc, out = run([f"item{sep}s1{sep}i1"], env2, capsys, client=client)
    assert rc == 0
    assert "field: PIN" in out
    assert client.view_calls
