from pass_rofi.clipboard import Clipboard, ClipboardError, clipboard_marker, extract_host


def test_extract_host_http_https():
    assert extract_host("https://github.com/login") == "github.com"
    assert extract_host("http://Example.COM/path?q=1") == "example.com"
    assert extract_host("https://user:pw@sub.example.com:8443/x") == "sub.example.com"


def test_extract_host_rejects_non_urls():
    assert extract_host("just some text") == ""
    assert extract_host("ftp://example.com") == ""
    assert extract_host("") == ""


def test_clipboard_marker_is_stable():
    assert clipboard_marker("https://a.com") == clipboard_marker("https://a.com")
    assert clipboard_marker("https://a.com") != clipboard_marker("https://b.com")


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
