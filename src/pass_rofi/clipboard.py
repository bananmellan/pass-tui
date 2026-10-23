from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable
from urllib.parse import urlsplit

Runner = Callable[[list[str], str], str]


class ClipboardError(RuntimeError):
    pass


def _run(argv: list[str], input_text: str | None = None) -> str:
    try:
        proc = subprocess.run(
            argv,
            input=input_text.encode("utf-8") if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise ClipboardError(f"{' '.join(argv)} failed: {exc}") from exc
    if proc.returncode != 0:
        raise ClipboardError(f"{' '.join(argv)} failed")
    return proc.stdout.decode("utf-8", errors="replace")


def extract_host(text: str) -> str:
    """Return the lowercase host[:port] if text looks like a URL or bare host.

    Accepts full http(s) URLs and scheme-less hosts (some apps put bare
    domains on the clipboard, e.g. `mistral.ai`). The path is dropped so
    that `https://nc.honung.org/apps/dashboard/` matches items whose urls
    contain `nc.honung.org` (see match_host).
    """
    text = text.strip()
    if not text:
        return ""
    candidate = text
    if not (text.startswith("http://") or text.startswith("https://")):
        # Scheme-less: only treat as a host if it looks like one — a single
        # token containing a dot, no spaces, no path-like prefix.
        first = text.splitlines()[0].strip() if text.splitlines() else ""
        if not first or " " in first or "/" in first.split(".")[0]:
            return ""
        if "." not in first:
            return ""
        candidate = "https://" + first
    try:
        host = urlsplit(candidate).hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    try:
        port = urlsplit(candidate).port
    except ValueError:
        port = None
    if port is not None:
        return f"{host}:{port}"
    return host


class Clipboard:
    def __init__(
        self,
        runner: Runner | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._runner = runner or _run
        self._log = log

    def _pick(self, *names: str) -> str | None:
        if self._runner is not None:
            for name in names:
                if os.environ.get(f"PASS_ROFI_HAS_{name.replace('-', '_').upper()}"):
                    return name
            return names[0]
        for name in names:
            if shutil.which(name):
                return name
        return None

    def read(self) -> str:
        paste = self._pick("wl-paste", "xclip", "xsel")
        if paste is None:
            if self._log:
                self._log("no clipboard tool found (tried wl-paste, xclip, xsel)")
            return ""
        argv = (
            [paste, "--no-newline"]
            if paste == "wl-paste"
            else [paste, "-selection", "clipboard", "-out"]
            if paste == "xclip"
            else [paste, "--clipboard", "--output"]
        )
        if self._log:
            self._log(f"clipboard read via {' '.join(argv)}")
        try:
            out = self._runner(argv)
        except ClipboardError:
            if self._log:
                self._log(f"clipboard read failed: {' '.join(argv)}")
            return ""
        except UnicodeDecodeError:
            if self._log:
                self._log("clipboard read: non-UTF-8 content, ignored")
            return ""
        if self._log:
            self._log(f"clipboard content: {out[:200]!r}")
        return out

    def write(self, text: str) -> None:
        copy = self._pick("wl-copy", "xclip", "xsel")
        if copy is None:
            raise ClipboardError("no clipboard tool found (wl-copy, xclip, xsel)")
        argv = (
            [copy]
            if copy == "wl-copy"
            else [copy, "-selection", "clipboard"]
            if copy == "xclip"
            else [copy, "--clipboard", "--input"]
        )
        self._runner(argv, text)
