import json
from pathlib import Path

import pytest

from pass_tui.app import App, FieldScreen, ListScreen, QuitRequested
from pass_tui.cache import CACHE_NAME, dump_cache
from pass_tui.config import Config
from pass_tui.models import ItemRef
from pass_tui.passcli import PassCliError
from pass_tui.state import StateStore


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
    def __init__(self, view=VIEW_JSON, totp="123456", items=None):
        self.view = view
        self.totp_value = totp
        self.view_calls = []
        self.totp_calls = []
        self.list_calls = 0
        self._items = items

    def list_all_items(self):
        self.list_calls += 1
        if self._items is not None:
            return self._items
        return [], 0

    def view_item_with_retry(self, item_ref):
        self.view_calls.append(item_ref)
        return json.loads(self.view)

    def totp(self, item_ref):
        self.totp_calls.append(item_ref)
        return self.totp_value


@pytest.fixture()
def env(tmp_path):
    return {"PASS_TUI_CACHE_DIR": str(tmp_path / "cache")}


def make_app(tmp_path, client=None, clipboard=None, refs=None, config=None, now=None):
    from pass_tui.cache import Cache
    from pass_tui.passcli import PassCli

    config = config or Config(cache_dir=str(tmp_path / "cache"), verbose=False)
    cache_dir = Path(config.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if refs is not None:
        dump_cache(refs, cache_dir / CACHE_NAME)
    client = client or FakeClient()
    cache = Cache(cache_dir / CACHE_NAME, client, ttl=config.cache_ttl)
    state = StateStore(cache_dir)
    clipboard = clipboard or FakeClipboard()
    return App(config, cache, state, client, clipboard, now=now or __import__("time").time)


def test_start_lists_cached_items_sorted(tmp_path):
    app = make_app(tmp_path, refs=[ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))])
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert [r.title for r in screen.refs] == ["Bank", "GitHub"]
    assert screen.query == ""


def test_start_rebuilds_when_cache_missing(tmp_path):
    client = FakeClient(items=([ref()], 1))
    app = make_app(tmp_path, client=client, refs=None)
    # No cache dumped; start() must rebuild.
    screen = app.start()
    assert client.list_calls == 1
    assert [r.title for r in screen.refs] == ["GitHub"]
    assert (Path(app.config.cache_dir) / CACHE_NAME).exists()


def test_start_rebuild_failure_shows_empty_list(tmp_path):
    client = FakeClient()

    def boom():
        raise PassCliError("offline")

    client.list_all_items = boom
    app = make_app(tmp_path, client=client, refs=None)
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert screen.refs == []
    # A failed initial rebuild must not crash later refreshes either.
    screen = app.handle_key("ctrl_r")
    assert isinstance(screen, ListScreen)


def test_clipboard_single_match_opens_item(tmp_path):
    client = FakeClient()
    app = make_app(
        tmp_path,
        client=client,
        clipboard=FakeClipboard("https://github.com/login"),
        refs=[ref(), ref("s1", "i2", "GitLab", ("https://gitlab.com",))],
    )
    screen = app.start()
    assert isinstance(screen, FieldScreen)
    assert client.view_calls and client.view_calls[0].item_id == "i1"
    assert "username" in screen.labels
    assert "password" in screen.labels


def test_clipboard_multiple_matches_lists_best_first_with_host_prefilled(tmp_path):
    client = FakeClient()
    app = make_app(
        tmp_path,
        client=client,
        clipboard=FakeClipboard("https://nc.honung.org/apps/dashboard/"),
        refs=[
            ref("s1", "i1", "R2 Cloudflare", ("https://nc.honung.org/",)),
            ref("s1", "i2", "NextCloud", ("https://nextcloud.com/", "https://nc.honung.org/", "https://nc.srv/")),
            ref("s1", "i3", "Aardvark Bank", ("https://bank.example.com",)),
        ],
    )
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert screen.query == "nc.honung.org"
    # Matches first in best-match order, then the rest.
    assert screen.refs[0].title == "NextCloud"
    assert screen.refs[1].title == "R2 Cloudflare"
    assert screen.refs[2].title == "Aardvark Bank"
    assert not client.view_calls


def test_clipboard_multi_match_list_excludes_fuzzy_extras(tmp_path):
    """Regression: with a URL host in the clipboard and several real
    matches, the prefilled list must show exactly the host-matched
    items first — aliases at bi.honung.org and unrelated logins must
    not appear above/among them via fuzzy subsequence matching."""
    app = make_app(
        tmp_path,
        clipboard=FakeClipboard("https://nc.honung.org/apps/dashboard/"),
        refs=[
            ItemRef("s1", "i1", "arrogance_backspin132@bi.honung.org", (), "Personal", "Alias"),
            ItemRef("s1", "i2", "chaplain_procreate523@bi.honung.org", (), "Personal", "Alias"),
            ItemRef("s1", "i3", "NextCloud", ("https://nextcloud.com/", "https://nc.honung.org/", "https://nc.srv/"), "Personal", "Login"),
            ItemRef("s1", "i4", "R2 Cloudflare", ("https://nc.honung.org/",), "Server", "Login"),
            ItemRef("s1", "i5", "Google adam.thunberg@hotmail.com", ("https://accounts.google.com/signin/v2/s",), "Personal", "Login"),
            ItemRef("s1", "i6", "Lenovo", ("https://passport.lenovo.com/wauthen5/wa",), "Personal", "Login"),
        ],
    )
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert screen.query == "nc.honung.org"
    # Real matches first, best-first...
    assert screen.refs[0].title == "NextCloud"
    assert screen.refs[1].title == "R2 Cloudflare"
    # ...and the visible (prefilled) list contains no fuzzy extras.
    visible_titles = [r.title for r in screen.visible]
    assert "arrogance_backspin132@bi.honung.org" not in visible_titles
    assert "chaplain_procreate523@bi.honung.org" not in visible_titles
    assert "Google adam.thunberg@hotmail.com" not in visible_titles
    assert "Lenovo" not in visible_titles
    assert set(visible_titles) == {"NextCloud", "R2 Cloudflare"}


def test_clipboard_host_prefill_fuzzy_resumes_after_user_edits(tmp_path):
    """Once the user edits the prefilled host query away, normal fuzzy
    matching applies again."""
    app = make_app(
        tmp_path,
        clipboard=FakeClipboard("https://github.com"),
        refs=[
            ref(),
            ItemRef("s1", "i2", "GitHub Enterprise", ("https://github.com/enterprise",), "Work", "Login"),
            ItemRef("s1", "i3", "Gothub", ("https://gothub.example.com",), "Personal", "Login"),
            ItemRef("s1", "i4", "GitLab", ("https://gitlab.com",), "Personal", "Login"),
        ],
    )
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert screen.query == "github.com"
    # Precise while the host is in the query: only substring matches.
    assert {r.title for r in screen.visible} == {"GitHub", "GitHub Enterprise"}
    # Clear the query and type a fuzzy one: subsequence matching resumes.
    while screen.query:
        screen = app.handle_key("backspace")
    for ch in "gthub":
        screen = app.handle_key(ch)
    assert {r.title for r in screen.visible} == {"GitHub", "GitHub Enterprise", "Gothub"}


def test_clipboard_no_match_shows_full_list(tmp_path):
    app = make_app(
        tmp_path,
        clipboard=FakeClipboard("https://unrelated.dev/page"),
        refs=[ref()],
    )
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert screen.query == ""
    assert [r.title for r in screen.refs] == ["GitHub"]


def test_clipboard_non_url_ignored(tmp_path):
    app = make_app(
        tmp_path,
        clipboard=FakeClipboard("my password is hunter2"),
        refs=[ref()],
    )
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert [r.title for r in screen.refs] == ["GitHub"]


def test_clipboard_bare_host_matches(tmp_path):
    client = FakeClient()
    app = make_app(
        tmp_path,
        client=client,
        clipboard=FakeClipboard("mistral.ai"),
        refs=[ref(), ref("s1", "i2", "Mistral", ("https://mistral.ai",), "Personal")],
    )
    screen = app.start()
    assert isinstance(screen, FieldScreen)
    assert client.view_calls[-1].item_id == "i2"


def test_clipboard_match_every_launch(tmp_path):
    client = FakeClient()
    app = make_app(
        tmp_path,
        client=client,
        clipboard=FakeClipboard("https://github.com/login"),
        refs=[ref()],
    )
    for _ in range(3):
        screen = app.start()
        assert isinstance(screen, FieldScreen)
    assert len(client.view_calls) == 3


def test_copy_then_launch_with_url_still_matches(tmp_path):
    client = FakeClient()
    clipboard = FakeClipboard()
    app = make_app(tmp_path, client=client, clipboard=clipboard, refs=[ref()])
    app.start()
    app.handle_key("enter")  # open GitHub
    assert isinstance(app.screen, FieldScreen)
    app.handle_key("down")  # note
    app.handle_key("down")  # password
    with pytest.raises(QuitRequested):
        app.handle_key("enter")  # copy password -> quit
    assert clipboard.written == ["secret"]
    # Now the clipboard holds the secret; a URL launch must still match.
    app.clipboard = FakeClipboard("https://github.com")
    screen = app.start()
    assert isinstance(screen, FieldScreen)
    assert client.view_calls[-1].item_id == "i1"


def test_reopen_after_copy_is_one_shot(tmp_path):
    client = FakeClient()
    clipboard = FakeClipboard()
    app = make_app(tmp_path, client=client, clipboard=clipboard, refs=[ref()])
    app.start()
    app.handle_key("enter")
    assert isinstance(app.screen, FieldScreen)
    # labels: note, password, totp, url: ..., username (field_labels order)
    labels = app.screen.labels
    idx = labels.index("password")
    for _ in range(idx):
        app.handle_key("down")
    with pytest.raises(QuitRequested):
        app.handle_key("enter")
    assert clipboard.written == ["secret"]
    assert app.state.get_last_copied() is not None
    # First relaunch: reopens the copied item.
    client.view_calls.clear()
    screen = app.start()
    assert isinstance(screen, FieldScreen)
    assert client.view_calls and client.view_calls[0].item_id == "i1"
    # The reopen memory is consumed.
    assert app.state.get_last_copied() is None
    # Second relaunch (nothing copied in between): plain list.
    client.view_calls.clear()
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert not client.view_calls


def test_open_item_does_not_arm_reopen(tmp_path):
    app = make_app(tmp_path, refs=[ref()])
    app.start()
    app.handle_key("enter")
    assert isinstance(app.screen, FieldScreen)
    assert app.state.get_last_copied() is None


def test_close_without_copy_then_relaunch_shows_list(tmp_path):
    client = FakeClient()
    app = make_app(tmp_path, client=client, refs=[ref()])
    app.start()
    app.handle_key("enter")
    assert isinstance(app.screen, FieldScreen)
    # Quit without copying (esc from fields goes back to list; esc again quits).
    app.handle_key("esc")
    assert isinstance(app.screen, ListScreen)
    with pytest.raises(QuitRequested):
        app.handle_key("esc")
    client.view_calls.clear()
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert not client.view_calls


def test_reopen_window_expired_shows_list(tmp_path):
    import time as time_mod

    now = [time_mod.time()]
    client = FakeClient()
    clipboard = FakeClipboard()
    app = make_app(tmp_path, client=client, clipboard=clipboard, refs=[ref()], now=lambda: now[0])
    app.start()
    app.handle_key("enter")
    labels = app.screen.labels
    idx = labels.index("password")
    for _ in range(idx):
        app.handle_key("down")
    with pytest.raises(QuitRequested):
        app.handle_key("enter")
    assert clipboard.written == ["secret"]
    # Move beyond the 20 s reopen window.
    now[0] += 100
    client.view_calls.clear()
    screen = app.start()
    assert isinstance(screen, ListScreen)
    assert not client.view_calls


def test_typing_filters_list_live(tmp_path):
    app = make_app(
        tmp_path,
        refs=[ref(), ref("s1", "i2", "GitLab", ("https://gitlab.com",)), ref("s1", "i3", "Bank", ("https://bank.example.com",))],
    )
    screen = app.start()
    for ch in "git":
        screen = app.handle_key(ch)
    assert isinstance(screen, ListScreen)
    assert [r.title for r in screen.visible] == ["GitHub", "GitLab"]
    screen = app.handle_key("backspace")
    screen = app.handle_key("backspace")
    screen = app.handle_key("backspace")
    assert [r.title for r in screen.visible] == ["Bank", "GitHub", "GitLab"]


def test_enter_opens_selected_item(tmp_path):
    client = FakeClient()
    app = make_app(tmp_path, client=client, refs=[ref(), ref("s1", "i2", "Bank", ("https://bank.example.com",))])
    app.start()
    screen = app.handle_key("enter")  # Bank is first (title-sorted)
    assert isinstance(screen, FieldScreen)
    assert client.view_calls[0].item_id == "i2"


def test_navigation_keys(tmp_path):
    app = make_app(
        tmp_path,
        refs=[ref("s1", "i1", "A"), ref("s1", "i2", "B"), ref("s1", "i3", "C")],
    )
    screen = app.start()
    assert screen.selected == 0
    screen = app.handle_key("down")
    assert screen.selected == 1
    screen = app.handle_key("down")
    assert screen.selected == 2
    screen = app.handle_key("down")  # clamped
    assert screen.selected == 2
    screen = app.handle_key("up")
    assert screen.selected == 1
    screen = app.handle_key("end")
    assert screen.selected == 2
    screen = app.handle_key("home")
    assert screen.selected == 0
    screen = app.handle_key("pagedown")
    assert screen.selected == 2
    screen = app.handle_key("pageup")
    assert screen.selected == 0


def test_esc_in_list_with_query_clears_query_then_quits(tmp_path):
    app = make_app(tmp_path, refs=[ref()])
    screen = app.start()
    screen = app.handle_key("g")
    assert screen.query == "g"
    screen = app.handle_key("esc")
    assert screen.query == ""
    with pytest.raises(QuitRequested):
        app.handle_key("esc")


def test_esc_in_fields_returns_to_list(tmp_path):
    app = make_app(tmp_path, refs=[ref()])
    app.start()
    app.handle_key("enter")
    assert isinstance(app.screen, FieldScreen)
    screen = app.handle_key("esc")
    assert isinstance(screen, ListScreen)
    assert [r.title for r in screen.refs] == ["GitHub"]


def test_fields_typing_filters_fields(tmp_path):
    app = make_app(tmp_path, refs=[ref()])
    app.start()
    app.handle_key("enter")
    screen = app.screen
    assert isinstance(screen, FieldScreen)
    all_labels = list(screen.labels)
    screen = app.handle_key("p")
    assert screen.query == "p"
    assert set(screen.visible) <= {l for l in all_labels if "p" in l.lower()}
    assert "password" in screen.visible


def test_field_enter_copies_value_and_quits(tmp_path):
    client = FakeClient()
    clipboard = FakeClipboard()
    app = make_app(tmp_path, client=client, clipboard=clipboard, refs=[ref()])
    app.start()
    app.handle_key("enter")
    labels = app.screen.labels
    idx = labels.index("password")
    for _ in range(idx):
        app.handle_key("down")
    with pytest.raises(QuitRequested):
        app.handle_key("enter")
    assert clipboard.written == ["secret"]
    # The reopen-after-copy memory is armed before quitting.
    assert app.state.get_last_copied() is not None


def test_failed_copy_does_not_quit(tmp_path):
    client = FakeClient()
    clipboard = FakeClipboard()

    def fail(value):
        from pass_tui.clipboard import ClipboardError

        raise ClipboardError("no clipboard")

    clipboard.write = fail
    app = make_app(tmp_path, client=client, clipboard=clipboard, refs=[ref()])
    app.start()
    app.handle_key("enter")
    labels = app.screen.labels
    idx = labels.index("password")
    for _ in range(idx):
        app.handle_key("down")
    screen = app.handle_key("enter")
    assert isinstance(screen, FieldScreen)
    assert screen.message == "Nothing to copy"
    assert clipboard.written == []


def test_field_enter_copies_totp(tmp_path):
    client = FakeClient()
    clipboard = FakeClipboard()
    app = make_app(tmp_path, client=client, clipboard=clipboard, refs=[ref()])
    app.start()
    app.handle_key("enter")
    labels = app.screen.labels
    idx = labels.index("totp")
    for _ in range(idx):
        app.handle_key("down")
    with pytest.raises(QuitRequested):
        app.handle_key("enter")
    assert clipboard.written == ["123456"]
    assert client.totp_calls


def test_field_enter_copies_url(tmp_path):
    clipboard = FakeClipboard()
    app = make_app(tmp_path, clipboard=clipboard, refs=[ref()])
    app.start()
    app.handle_key("enter")
    labels = app.screen.labels
    url_label = next(l for l in labels if l.startswith("url: "))
    idx = labels.index(url_label)
    for _ in range(idx):
        app.handle_key("down")
    with pytest.raises(QuitRequested):
        app.handle_key("enter")
    assert clipboard.written == ["https://github.com"]


def test_field_navigation_and_filter(tmp_path):
    app = make_app(tmp_path, refs=[ref()])
    app.start()
    app.handle_key("enter")
    screen = app.screen
    assert isinstance(screen, FieldScreen)
    screen = app.handle_key("end")
    assert screen.selected == len(screen.visible) - 1
    screen = app.handle_key("home")
    assert screen.selected == 0
    screen = app.handle_key("x")  # filter, likely no match
    assert screen.visible == []
    screen = app.handle_key("backspace")
    assert len(screen.visible) > 0


def test_ctrl_r_refreshes_from_passcli(tmp_path):
    client = FakeClient(items=([ref("s1", "i9", "New Item")], 1))
    app = make_app(tmp_path, client=client, refs=[ref()])
    app.start()
    assert [r.title for r in app.screen.refs] == ["GitHub"]
    screen = app.handle_key("ctrl_r")
    assert client.list_calls == 1
    assert [r.title for r in screen.refs] == ["New Item"]


def test_ctrl_r_failure_keeps_list_and_reports(tmp_path):
    client = FakeClient()

    def boom():
        raise PassCliError("offline")

    client.list_all_items = boom
    app = make_app(tmp_path, client=client, refs=[ref()])
    app.start()
    screen = app.handle_key("ctrl_r")
    assert isinstance(screen, ListScreen)
    assert "Refresh failed" in screen.message
    assert [r.title for r in screen.refs] == ["GitHub"]


def test_ctrl_c_quits_and_clears_reopen_memory(tmp_path):
    client = FakeClient()
    clipboard = FakeClipboard()
    app = make_app(tmp_path, client=client, clipboard=clipboard, refs=[ref()])
    app.start()
    app.handle_key("enter")
    labels = app.screen.labels
    idx = labels.index("password")
    for _ in range(idx):
        app.handle_key("down")
    with pytest.raises(QuitRequested):
        app.handle_key("enter")
    assert app.state.get_last_copied() is not None
    with pytest.raises(QuitRequested):
        app.handle_key("ctrl_c")
    assert app.state.get_last_copied() is None


def test_duplicate_titles_open_exact_item(tmp_path):
    client = FakeClient()
    app = make_app(
        tmp_path,
        client=client,
        refs=[ref(), ref("s2", "i2", "GitHub", ("https://github.com/other",), "Work")],
    )
    screen = app.start()
    # Both titled GitHub; select the second one.
    screen = app.handle_key("down")
    assert screen.selected_ref.item_id == "i2"
    screen = app.handle_key("enter")
    assert isinstance(screen, FieldScreen)
    assert client.view_calls[0].share_id == "s2"
    assert client.view_calls[0].item_id == "i2"


def test_view_failure_returns_to_list_with_message(tmp_path):
    client = FakeClient()

    def boom(item_ref):
        raise PassCliError("gone")

    client.view_item_with_retry = boom
    app = make_app(tmp_path, client=client, refs=[ref()])
    screen = app.start()
    screen = app.handle_key("enter")
    assert isinstance(screen, ListScreen)
    assert "Could not open" in screen.message


def test_item_with_only_extra_fields_opens(tmp_path):
    view = json.dumps(
        {
            "item": {
                "content": {
                    "title": "Mors enheter",
                    "note": "",
                    "content": {"Login": {"email": "", "username": "", "password": "", "urls": [], "totp_uri": "", "passkeys": [], "autofill_urls": []}},
                    "extra_fields": [
                        {"name": "iPhone PIN", "content": {"Hidden": "1234"}},
                        {"name": "MacBook", "content": {"Hidden": "5678"}},
                    ],
                }
            }
        }
    )
    client = FakeClient(view=view)
    clipboard = FakeClipboard()
    app = make_app(tmp_path, client=client, clipboard=clipboard, refs=[ref(title="Mors enheter")])
    screen = app.start()
    screen = app.handle_key("enter")
    assert isinstance(screen, FieldScreen)
    assert "field: iPhone PIN" in screen.labels
    assert "field: MacBook" in screen.labels
    # Copy the first extra field; copying quits.
    with pytest.raises(QuitRequested):
        app.handle_key("enter")
    assert clipboard.written == ["1234"]


def test_no_readable_fields_returns_to_list(tmp_path):
    view = json.dumps({"item": {"content": {"title": "Empty", "note": "", "content": {"Login": {}}}}})
    client = FakeClient(view=view)
    app = make_app(tmp_path, client=client, refs=[ref(title="Empty")])
    app.start()
    screen = app.handle_key("enter")
    assert isinstance(screen, ListScreen)
    assert "No readable fields" in screen.message


def test_alias_item_opens_with_readable_email_field(tmp_path):
    """Regression: alias items must expose their address as a copyable
    field instead of "No readable fields" (address lives at top-level
    item.alias_email)."""
    view = json.dumps(
        {
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
    )
    client = FakeClient(view=view)
    clipboard = FakeClipboard()
    app = make_app(
        tmp_path,
        client=client,
        clipboard=clipboard,
        refs=[ItemRef("s1", "i1", "affection_thorn212@slmail.me", (), "Personal", "Alias")],
    )
    app.start()
    screen = app.handle_key("enter")
    assert isinstance(screen, FieldScreen)
    assert screen.labels == ["email: affection_thorn212@slmail.me"]
    with pytest.raises(QuitRequested):
        app.handle_key("enter")
    assert clipboard.written == ["affection_thorn212@slmail.me"]


def test_verbose_log_records_decisions(tmp_path):
    messages = []
    config = Config(cache_dir=str(tmp_path / "cache"), verbose=True)
    from pass_tui.cache import Cache

    cache_dir = Path(config.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    dump_cache([ref()], cache_dir / CACHE_NAME)
    client = FakeClient()
    cache = Cache(cache_dir / CACHE_NAME, client, ttl=config.cache_ttl)
    state = StateStore(cache_dir)
    app = App(
        config,
        cache,
        state,
        client,
        FakeClipboard("https://github.com/login"),
        log=messages.append,
    )
    screen = app.start()
    assert isinstance(screen, FieldScreen)
    assert any("clipboard read" in m for m in messages)
    assert any("extracted host: 'github.com'" in m for m in messages)
    assert any("matched 1 items" in m for m in messages)
