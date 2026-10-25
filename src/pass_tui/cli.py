from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from .app import App
from .cache import CACHE_NAME, Cache
from .clipboard import Clipboard
from .config import Config
from .passcli import PassCli, PassCliError
from .state import StateStore


def _debug_log(cache_dir: Path, message: str) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        log = cache_dir / "debug.log"
        if log.exists() and log.stat().st_size > 200_000:
            log.write_text("", encoding="utf-8")
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%F %T')} {message}\n")
    except OSError:
        pass


def _verbose(config: Config, cache_dir: Path, message: str) -> None:
    if not config.verbose:
        return
    print(f"pass-tui: {message}", file=sys.stderr)
    _debug_log(cache_dir, f"verbose: {message}")


def _spawn_rebuild(config: Config) -> None:
    env = dict(os.environ)
    env.pop("ROFI_RETV", None)
    env.pop("ROFI_INFO", None)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "pass_tui.cli", "--rebuild"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    except OSError:
        pass


def build_app(
    config: Config,
    client: PassCli | None = None,
    clipboard: Clipboard | None = None,
) -> App:
    cache_dir = Path(config.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    client = client or PassCli(config.pass_cli_bin)
    cache = Cache(cache_dir / CACHE_NAME, client, ttl=config.cache_ttl)
    state = StateStore(cache_dir)
    clipboard = clipboard or Clipboard(
        log=(lambda msg: _verbose(config, cache_dir, msg)) if config.verbose else None
    )

    def log(message: str) -> None:
        _verbose(config, cache_dir, message)

    return App(config, cache, state, client, clipboard, log=log)


def run_tui(config: Config | None = None, env=None) -> int:
    config = config or Config.from_env(env or os.environ)
    # Enable UTF-8 input/output in curses so special characters (å, ä, ö,
    # emoji, …) are decoded correctly. Must run before curses.wrapper().
    import locale

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "pass-tui: interactive terminal required (run it from a terminal)",
            file=sys.stderr,
        )
        return 2
    cache_dir = Path(config.cache_dir)
    if config.keyring_backend == "dbus" and "PROTON_PASS_LINUX_KEYRING" not in os.environ:
        # Default: keep the session across reboots by storing pass-cli's
        # encryption key in the desktop Secret Service instead of the
        # kernel keyring (wiped on reboot). Applies to this process and
        # every pass-cli child it spawns. Opt out with
        # PASS_TUI_KEYRING_BACKEND=kernel (or set PROTON_PASS_LINUX_KEYRING
        # yourself).
        os.environ["PROTON_PASS_LINUX_KEYRING"] = "dbus"
    client = PassCli(config.pass_cli_bin)

    def boot(stdscr):
        # One curses session for splash + main loop: wrapper tears the
        # terminal down on return, so the list UI must live inside it.
        from .tui import _loop, run_splash

        ok, app = run_splash(stdscr, config, client)
        if not ok or app is None:
            return 1
        _loop(stdscr, app)  # app is already started; _loop skips start()
        return 0

    import curses

    try:
        rc = curses.wrapper(boot)
    except KeyboardInterrupt:
        return 0
    if rc:
        print("pass-tui: aborted.", file=sys.stderr)
    return rc


def run_rebuild(config: Config | None = None, env=None) -> int:
    config = config or Config.from_env(env or os.environ)
    cache_dir = Path(config.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = Cache(cache_dir / CACHE_NAME, PassCli(config.pass_cli_bin), ttl=config.cache_ttl)
    _verbose(config, cache_dir, "rebuild: start")
    try:
        refs, raw_count = cache.rebuild()
        with_urls = sum(1 for ref in refs if ref.urls)
        _verbose(
            config,
            cache_dir,
            f"rebuild: ok, {len(refs)} unique items from {raw_count} raw entries, "
            f"{with_urls} with urls",
        )
    except PassCliError as exc:
        _verbose(config, cache_dir, f"rebuild failed: {exc}")
        _debug_log(cache_dir, f"background rebuild failed: {exc}")
    try:
        (cache_dir / "rebuild.lock").unlink()
    except OSError:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pass-tui",
        description="Terminal TUI for Proton Pass (pass-cli).",
    )
    parser.add_argument("--rebuild", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="log decision points to stderr and the debug log",
    )
    args = parser.parse_args(argv)
    env = os.environ
    if args.verbose:
        env = dict(env)
        env["PASS_TUI_VERBOSE"] = "1"
    if args.rebuild:
        return run_rebuild(env=env)
    return run_tui(env=env)


if __name__ == "__main__":
    raise SystemExit(main())
