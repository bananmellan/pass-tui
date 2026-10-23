import json

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


def run(argv, env, capsys, client=None, clipboard=None):
    config = Config.from_env(env)
    rc = cli_mod.run_rofi_mode(
        argv,
        env,
        config=config,
        client=client or FakeClient(),
        clipboard=clipboard or FakeClipboard(),
    )
    out = capsys.readouterr().out
    return rc, out


def test_initial_lists_cached_items_with_info(env, capsys, tmp_path):
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
    assert any("GitHub" in line for line in item_lines)
    assert any("Bank" in line for line in item_lines)
    assert any("\x00display\x1f" in line and "<b>GitHub</b>" in line for line in item_lines)
    assert any("\x1fmeta\x1f" in line and "GitHub" in line for line in item_lines)
    assert any("\x1finfo\x1fitem\x1fs1\x1fi1" in line for line in item_lines)


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
    assert "GitHub" in out
    assert "GitHub Enterprise" in out
    assert "Bank" not in out
    assert not client.view_calls


def test_initial_clipboard_no_match_shows_full_list(env, capsys):
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    rc, out = run([], env, capsys, clipboard=FakeClipboard("https://unrelated.dev/page"))
    assert cli_mod.REFRESH_ROW in out
    assert "GitHub" in out


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
    spawned = []
    monkeypatch.setattr(cli_mod, "_spawn_rebuild", lambda config: spawned.append(config))
    cache_dir = tmp_path_cache(env)
    cache_dir.mkdir(parents=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    client.list_all_items = lambda: [ref(), ref("s1", "i9", "New", ())]
    env2 = dict(env, ROFI_RETV="10")
    rc, out = run([], env2, capsys, client=client)
    assert rc == 0
    assert "GitHub" in out
    assert "New" in out
    assert "\x00use-hot-keys\x1ftrue" in out


def test_custom_keybinding_refresh_failure_still_lists(env, capsys, monkeypatch):
    """Ctrl+L with a failing pass-cli still returns the (stale) cached list."""
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
