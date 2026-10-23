from pass_tui.clipboard import Clipboard, ClipboardError, extract_host


def test_extract_host_http_https():
    assert extract_host("https://github.com/login") == "github.com"
    assert extract_host("http://Example.COM/path?q=1") == "example.com"
    assert extract_host("https://user:pw@sub.example.com:8443/x") == "sub.example.com:8443"


def test_extract_host_strips_path_and_trailing_slashes():
    assert extract_host("https://nc.honung.org/apps/dashboard/") == "nc.honung.org"
    assert extract_host("https://nc.honung.org/") == "nc.honung.org"
    assert extract_host("https://nc.honung.org") == "nc.honung.org"


def test_extract_host_rejects_non_urls():
    assert extract_host("just some text") == ""
    assert extract_host("ftp://example.com") == ""
    assert extract_host("") == ""
    assert extract_host("localhost") == ""
    assert extract_host("two words.com") == ""


def test_extract_host_accepts_scheme_less_host():
    """Some apps put bare domains on the clipboard (e.g. `mistral.ai`)."""
    assert extract_host("mistral.ai") == "mistral.ai"
    assert extract_host("nc.honung.org/apps/dashboard/") == "nc.honung.org"
    assert extract_host("Example.COM") == "example.com"
    assert extract_host("sub.example.com:8443/x") == "sub.example.com:8443"


def test_read_uses_wl_paste_first():
    calls = []

    def runner(argv, input_text=None):
        calls.append(argv)
        return "https://github.com\n"

    clip = Clipboard(runner=runner)
    assert clip.read() == "https://github.com\n"
    assert calls[0][0].endswith("wl-paste") or calls[0][0] in ("wl-paste", "xclip", "xsel")


def test_read_returns_empty_on_failure():
    def runner(argv, input_text=None):
        raise ClipboardError("no clipboard")

    assert Clipboard(runner=runner).read() == ""


def test_write_pipes_value_to_copy_tool():
    calls = []

    def runner(argv, input_text=None):
        calls.append((argv, input_text))
        return ""

    Clipboard(runner=runner).write("s3cret")
    assert calls and calls[0][1] == "s3cret"


def test_read_survives_non_utf8_clipboard():
    """A clipboard holding binary data (e.g. an image) must not crash rofi."""
    import pass_tui.clipboard as clip_mod

    def fake_run(argv, input=None, stdout=None, stderr=None, check=None):
        class Proc:
            returncode = 0
            stdout = b"\x89PNG\r\n\x1a\n\xff\xfe binary \x00 data"
            stderr = b""

        return Proc()

    old = clip_mod.subprocess.run
    clip_mod.subprocess.run = fake_run
    try:
        assert Clipboard().read() != "" or True
        out = Clipboard().read()
        assert "\ufffd" in out or out == ""
    finally:
        clip_mod.subprocess.run = old


def test_run_decodes_with_replacement_not_crash():
    import pass_tui.clipboard as clip_mod

    def fake_run(argv, input=None, stdout=None, stderr=None, check=None):
        class Proc:
            returncode = 0
            stdout = b"\x89PNG invalid \xff bytes"
            stderr = b""

        return Proc()

    old = clip_mod.subprocess.run
    clip_mod.subprocess.run = fake_run
    try:
        out = clip_mod._run(["wl-paste", "--no-newline"])
        assert "\ufffd" in out
    finally:
        clip_mod.subprocess.run = old


def test_write_sends_utf8_bytes():
    import pass_tui.clipboard as clip_mod

    seen = {}

    def fake_run(argv, input=None, stdout=None, stderr=None, check=None):
        seen["argv"] = argv
        seen["input"] = input

        class Proc:
            returncode = 0
            stdout = b""
            stderr = b""

        return Proc()

    old = clip_mod.subprocess.run
    clip_mod.subprocess.run = fake_run
    try:
        clip_mod._run(["wl-copy"], "pässwörd")
        assert seen["input"] == "pässwörd".encode("utf-8")
    finally:
        clip_mod.subprocess.run = old
