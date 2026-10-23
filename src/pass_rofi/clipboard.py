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
    """Return the lowercase host[:port] if text looks like an http(s) URL.

    The path is dropped so that `https://nc.honung.org/apps/dashboard/`
    matches items whose urls contain `nc.honung.org` (see match_host).
    """
    text = text.strip()
    if not (text.startswith("http://") or text.startswith("https://")):
        return ""
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    try:
        port = urlsplit(text).port
    except ValueError:
        port = None
    if port is not None:
        return f"{host}:{port}"
    return host


class Clipboard:
    def __init__(self, runner: Runner | None = None) -> None:
        self._runner = runner or _run

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
            return ""
        argv = (
            [paste, "--no-newline"]
            if paste == "wl-paste"
            else [paste, "-selection", "clipboard", "-out"]
            if paste == "xclip"
            else [paste, "--clipboard", "--output"]
        )
        try:
            return self._runner(argv)
        except ClipboardError:
            return ""
        except UnicodeDecodeError:
            return ""

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
