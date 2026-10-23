from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from . import rofi as rofi_proto
from .cache import CACHE_NAME, Cache, match_host
from .clipboard import Clipboard, ClipboardError, clipboard_marker, extract_host
from .config import Config
from .display import field_row, item_row, parse_info
from .fields import field_labels, field_value
from .models import ItemRef
from .passcli import PassCli, PassCliError
from .state import StateStore

REFRESH_ROW = "🔄 Refresh item list"


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


def _spawn_rebuild(config: Config) -> None:
    env = dict(os.environ)
    env.pop("ROFI_RETV", None)
    env.pop("ROFI_INFO", None)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "pass_rofi.cli", "--rebuild"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    except OSError:
        pass


def _show_item_list(cache: Cache, state: StateStore, host_filter: str = "") -> None:
    refs = cache.get()
    state.set_stage(rofi_proto.STAGE_LIST)
    print(rofi_proto.mode_option("use-hot-keys", "true"))
    print(rofi_proto.mode_option("markup-rows", "true"))
    print(rofi_proto.mode_option("prompt", "Proton Pass" if not host_filter else f"Proton Pass ({host_filter})"))
    print(REFRESH_ROW)
    rows = [item_row(ref) for ref in refs]
    if rows:
        print("\n".join(rows))


def _open_item(cache: Cache, state: StateStore, client: PassCli, ref: ItemRef) -> bool:
    try:
        data = client.view_item_with_retry(ref)
    except PassCliError as exc:
        _debug_log(cache.path.parent, f"view failed: {exc}")
        return False
    labels = field_labels(data)
    if not labels:
        return False
    state.set_stage(rofi_proto.STAGE_FIELDS)
    state.set_last_copied(ref)
    print(rofi_proto.mode_option("use-hot-keys", "true"))
    print(rofi_proto.mode_option("markup-rows", "true"))
    print(rofi_proto.mode_option("prompt", ref.title))
    print("\n".join(field_row(label, label) for label in labels))
    return True


def _copy_field(
    state: StateStore,
    client: PassCli,
    clipboard: Clipboard,
    ref: ItemRef,
    label: str,
) -> None:
    try:
        data = client.view_item_with_retry(ref)
    except PassCliError as exc:
        _debug_log(state.dir, f"copy view failed: {exc}")
        return
    totp = ""
    if label == "totp":
        try:
            totp = client.totp(ref)
        except PassCliError as exc:
            _debug_log(state.dir, f"totp failed: {exc}")
            return
    value = field_value(data, label, totp=totp)
    if not value:
        return
    try:
        clipboard.write(value)
    except ClipboardError as exc:
        _debug_log(state.dir, f"copy failed: {exc}")
        return
    state.set_last_copied(ref)


def _initial(
    config: Config,
    cache: Cache,
    state: StateStore,
    client: PassCli,
    clipboard: Clipboard,
) -> None:
    now = time.time()
    last = state.get_last_copied()
    if last is not None:
        age = state.last_copy_age(now)
        if age is not None and age < config.reopen_seconds:
            if _open_item(cache, state, client, last):
                return
        state.clear_last_copied()

    clip = clipboard.read()[:2048]
    host = extract_host(clip)
    if host:
        marker = clipboard_marker(clip)
        if marker != state.get_clip_marker():
            state.set_clip_marker(marker)
            cache.ensure_fresh_async(lambda c: _spawn_rebuild(config))
            matches = match_host(cache.get(), host)
            if len(matches) == 1:
                _open_item(cache, state, client, matches[0])
                return
            if len(matches) > 1:
                state.set_stage(rofi_proto.STAGE_LIST)
                print(rofi_proto.mode_option("use-hot-keys", "true"))
                print(rofi_proto.mode_option("markup-rows", "true"))
                print(rofi_proto.mode_option("prompt", f"Proton Pass ({host})"))
                print("\n".join(item_row(ref) for ref in matches))
                return

    cache.ensure_fresh_async(lambda c: _spawn_rebuild(config))
    _show_item_list(cache, state)


def _on_select(
    config: Config,
    cache: Cache,
    state: StateStore,
    client: PassCli,
    clipboard: Clipboard,
    selected: str,
    info: str,
) -> None:
    if selected == REFRESH_ROW:
        try:
            cache.rebuild()
        except PassCliError as exc:
            _debug_log(cache.path.parent, f"refresh failed: {exc}")
        _show_item_list(cache, state)
        return

    parts = parse_info(selected) or parse_info(info)
    if state.get_stage() == rofi_proto.STAGE_FIELDS:
        label = parts[0] if parts else selected.split("\x00", 1)[0]
        last = state.get_last_copied()
        if last is not None:
            _copy_field(state, client, clipboard, last, label)
        return

    if len(parts) == 3 and parts[0] == "item":
        refs = cache.get()
        for ref in refs:
            if ref.share_id == parts[1] and ref.item_id == parts[2]:
                if _open_item(cache, state, client, ref):
                    return
                break
        _show_item_list(cache, state)
        return

    if selected:
        stripped = selected.split("\x00", 1)[0]
        refs = [ref for ref in cache.get() if ref.title == stripped]
        if refs:
            for ref in refs:
                if _open_item(cache, state, client, ref):
                    return
    _show_item_list(cache, state)


def run_rofi_mode(
    argv: list[str],
    env,
    config: Config | None = None,
    client: PassCli | None = None,
    clipboard: Clipboard | None = None,
) -> int:
    config = config or Config.from_env(env)
    cache_dir = Path(config.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = Cache(cache_dir / CACHE_NAME, client or PassCli(config.pass_cli_bin), ttl=config.cache_ttl)
    state = StateStore(cache_dir)
    client = client or PassCli(config.pass_cli_bin)
    clipboard = clipboard or Clipboard()

    if config.debug:
        _debug_log(cache_dir, f"invoked argv={argv} env RETV={env.get('ROFI_RETV')}")

    retv = rofi_proto.retv_from_env(env)
    if rofi_proto.RETV_CUSTOM_KEY_BASE <= retv <= rofi_proto.RETV_CUSTOM_KEY_BASE + 18:
        state.clear_last_copied()
        state.set_stage(rofi_proto.STAGE_LIST)
        cache.ensure_fresh_async(lambda c: _spawn_rebuild(config))
        _show_item_list(cache, state)
        return 0
    if retv in (rofi_proto.RETV_SELECTED, rofi_proto.RETV_CUSTOM):
        selected = argv[0] if argv else ""
        _on_select(config, cache, state, client, clipboard, selected, rofi_proto.info_from_env(env))
        return 0
    _initial(config, cache, state, client, clipboard)
    return 0


def run_rebuild(config: Config | None = None, env=None) -> int:
    config = config or Config.from_env(env or os.environ)
    cache_dir = Path(config.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = Cache(cache_dir / CACHE_NAME, PassCli(config.pass_cli_bin), ttl=config.cache_ttl)
    try:
        cache.rebuild()
    except PassCliError as exc:
        if config.debug:
            _debug_log(cache_dir, f"background rebuild failed: {exc}")
    try:
        (cache_dir / "rebuild.lock").unlink()
    except OSError:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pass-rofi-script")
    parser.add_argument("--rebuild", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("entry", nargs="?", default="")
    args = parser.parse_args(argv)
    env = os.environ
    if args.rebuild and "ROFI_RETV" not in env:
        return run_rebuild(env=env)
    return run_rofi_mode([a for a in (args.entry,) if a], env)


if __name__ == "__main__":
    raise SystemExit(main())
