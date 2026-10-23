from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from . import rofi as rofi_proto
from .cache import CACHE_NAME, Cache, match_host, sort_refs
from .dedup import display_duplicate_groups
from .clipboard import Clipboard, ClipboardError, extract_host
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


def _verbose(config: Config, cache_dir: Path, message: str) -> None:
    """Verbose logging: stderr always (rofi captures it), debug.log on demand."""
    if not config.verbose:
        return
    print(f"pass-rofi: {message}", file=sys.stderr)
    _debug_log(cache_dir, f"verbose: {message}")


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


def _show_item_list(
    cache: Cache,
    state: StateStore,
    host_filter: str = "",
    matches: list[ItemRef] | None = None,
    config: Config | None = None,
) -> None:
    """Print the item list in script mode.

    All items are printed so rofi can filter them live as the user types
    (rofi cannot re-invoke the script per keystroke, so client-side
    filtering is the only way to get keystroke search). Rows are printed
    title-sorted; when the clipboard produced host matches, those go
    first in best-match order and the prompt is prefilled with the host,
    so the matches are on top from the start.
    """
    refs = cache.load()
    state.set_stage(rofi_proto.STAGE_LIST)
    print(rofi_proto.mode_option("use-hot-keys", "true"))
    print(rofi_proto.mode_option("markup-rows", "true"))
    print(rofi_proto.mode_option("prompt", "Proton Pass" if not host_filter else f"Proton Pass ({host_filter})"))
    if host_filter:
        print(rofi_proto.mode_option("filter", host_filter))
    print(REFRESH_ROW)
    if matches is not None:
        matched_ids = {ref.item_id for ref in matches}
        rest = [ref for ref in sort_refs(refs) if ref.item_id not in matched_ids]
        ordered = matches + rest
    else:
        ordered = sort_refs(refs)
    if config is not None and config.verbose:
        id_counts: dict[str, int] = {}
        for ref in refs:
            id_counts[ref.item_id] = id_counts.get(ref.item_id, 0) + 1
        dup_ids = {k: v for k, v in id_counts.items() if v > 1}
        _verbose(
            config,
            cache.path.parent,
            f"list: {len(refs)} cached items, {len(ordered)} rows printed"
            + (f", {len(dup_ids)} item_ids appear more than once: {dict(list(dup_ids.items())[:5])}" if dup_ids else ", no repeated item_ids"),
        )
        for group in display_duplicate_groups(ordered):
            desc = ", ".join(
                f"{ref.title!r} share={ref.share_id[:8]}… item={ref.item_id[:8]}… vault={ref.vault_name!r}"
                for ref in group
            )
            _verbose(config, cache.path.parent, f"list: visually identical rows: {desc}")
    if ordered:
        print("\n".join(item_row(ref) for ref in ordered))


def _open_item(
    cache: Cache,
    state: StateStore,
    client: PassCli,
    ref: ItemRef,
    config: Config | None = None,
) -> bool:
    try:
        data = client.view_item_with_retry(ref)
    except PassCliError as exc:
        if config is not None:
            _verbose(
                config,
                cache.path.parent,
                f"view failed for {ref.title!r} ({ref.share_id}/{ref.item_id}): {exc}",
            )
        _debug_log(cache.path.parent, f"view failed: {exc}")
        return False
    labels = field_labels(data)
    if not labels:
        if config is not None:
            _verbose(
                config,
                cache.path.parent,
                f"no readable fields for {ref.title!r} — see debug.log",
            )
        _debug_log(
            cache.path.parent,
            f"no labels for item {ref.title!r} share={ref.share_id} item={ref.item_id}",
        )
        return False
    state.set_stage(rofi_proto.STAGE_FIELDS)
    state.set_open_item(ref)
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


def _clipboard_matches(
    config: Config,
    cache: Cache,
    state: StateStore,
    client: PassCli,
    clipboard: Clipboard,
) -> bool:
    """Clipboard-URL matching. Returns True if the launch was handled.

    Runs on every launch while the clipboard holds a URL: copying a link
    and launching must always match, even if the clipboard text is unchanged
    since the previous launch.
    """
    clip = clipboard.read()[:2048]
    _verbose(config, cache.path.parent, f"clipboard read: {clip[:200]!r}")
    host = extract_host(clip)
    _verbose(config, cache.path.parent, f"extracted host: {host!r}")
    if not host:
        return False
    cache.ensure_fresh_async(lambda c: _spawn_rebuild(config))
    refs = cache.load()
    with_urls = sum(1 for ref in refs if ref.urls)
    _verbose(
        config,
        cache.path.parent,
        f"cache: {len(refs)} items, {with_urls} with urls",
    )
    matches = match_host(refs, host)
    _verbose(
        config,
        cache.path.parent,
        f"host {host!r} matched {len(matches)} items: "
        + ", ".join(f"{m.title} {list(m.urls)}" for m in matches[:10]),
    )
    if len(matches) == 1:
        _open_item(cache, state, client, matches[0], config=config)
        return True
    if len(matches) > 1:
        _show_item_list(cache, state, host_filter=host, matches=matches, config=config)
        return True
    return False


def _initial(
    config: Config,
    cache: Cache,
    state: StateStore,
    client: PassCli,
    clipboard: Clipboard,
) -> None:
    now = time.time()

    # Reopen-after-copy is a one-shot: it only applies to the launch
    # immediately following a copy. Clear it on every program start so a
    # later launch (where nothing was copied in between) behaves normally.
    last = state.get_last_copied()
    state.clear_last_copied()
    if last is not None:
        last_ref, copied_at = last
        age = now - copied_at
        _verbose(
            config,
            cache.path.parent,
            f"last copied item {last_ref.title!r} age={age}",
        )
        if age < config.reopen_seconds:
            if _open_item(cache, state, client, last_ref, config=config):
                return
    else:
        _verbose(config, cache.path.parent, "no last-copied item")

    if _clipboard_matches(config, cache, state, client, clipboard):
        return
    _verbose(config, cache.path.parent, "clipboard matching did not handle the launch")

    cache.ensure_fresh_async(lambda c: _spawn_rebuild(config))
    if not cache.get():
        try:
            refs, raw_count = cache.rebuild()
            _verbose(
                config,
                cache.path.parent,
                f"initial rebuild: {len(refs)} unique items from {raw_count} raw entries",
            )
        except PassCliError as exc:
            _debug_log(cache.path.parent, f"initial rebuild failed: {exc}")
    _show_item_list(cache, state, config=config)


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
        state.set_open_item(None)
        try:
            refs, raw_count = cache.rebuild()
            _verbose(
                config,
                cache.path.parent,
                f"refresh: {len(refs)} unique items from {raw_count} raw entries",
            )
        except PassCliError as exc:
            _debug_log(cache.path.parent, f"refresh failed: {exc}")
        _show_item_list(cache, state, config=config)
        return

    parts = parse_info(selected) or parse_info(info)
    if state.get_stage() == rofi_proto.STAGE_FIELDS:
        label = parts[0] if parts else selected.split("\x00", 1)[0]
        open_item = state.get_open_item()
        if open_item is None:
            last = state.get_last_copied()
            open_item = last[0] if last else None
        if open_item is not None:
            _copy_field(state, client, clipboard, open_item, label)
        return

    if len(parts) == 3 and parts[0] == "item":
        refs = cache.load()
        resolved = next(
            (ref for ref in refs if ref.share_id == parts[1] and ref.item_id == parts[2]),
            None,
        )
        if resolved is not None:
            if _open_item(cache, state, client, resolved, config=config):
                return
            _verbose(
                config,
                cache.path.parent,
                f"could not open cached item {resolved.title!r} — falling back to list",
            )
        else:
            _verbose(
                config,
                cache.path.parent,
                f"selected item not in cache (share={parts[1][:8]}… item={parts[2][:8]}…)"
                f" — cache has {len(refs)} items; it may be stale",
            )
        state.set_open_item(None)
        _show_item_list(cache, state, config=config)
        return

    if selected:
        stripped = selected.split("\x00", 1)[0]
        refs = [ref for ref in cache.load() if ref.title == stripped]
        if refs:
            for ref in refs:
                if _open_item(cache, state, client, ref, config=config):
                    return
    state.set_open_item(None)
    _show_item_list(cache, state, config=config)


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
    clipboard = clipboard or Clipboard(
        log=(lambda msg: _verbose(config, cache_dir, msg)) if config.verbose else None
    )

    if config.debug:
        _debug_log(cache_dir, f"invoked argv={argv} env RETV={env.get('ROFI_RETV')}")

    retv = rofi_proto.retv_from_env(env)
    _verbose(
        config,
        cache_dir,
        f"invoked argv={argv} ROFI_RETV={retv} stage={state.get_stage()}",
    )
    if rofi_proto.RETV_CUSTOM_KEY_BASE <= retv <= rofi_proto.RETV_CUSTOM_KEY_BASE + 18:
        _verbose(config, cache_dir, "custom key: back to list")
        state.clear_last_copied()
        state.set_open_item(None)
        state.set_stage(rofi_proto.STAGE_LIST)
        cache.ensure_fresh_async(lambda c: _spawn_rebuild(config))
        _show_item_list(cache, state, config=config)
        return 0
    if retv in (rofi_proto.RETV_SELECTED, rofi_proto.RETV_CUSTOM):
        selected = argv[0] if argv else ""
        _verbose(config, cache_dir, f"selected: {selected[:120]!r}")
        _on_select(config, cache, state, client, clipboard, selected, rofi_proto.info_from_env(env))
        return 0
    _initial(config, cache, state, client, clipboard)
    return 0


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
        for ref in refs[:20]:
            _verbose(
                config,
                cache_dir,
                f"rebuild item: title={ref.title!r} urls={list(ref.urls)} vault={ref.vault_name!r}",
            )
    except PassCliError as exc:
        _verbose(config, cache_dir, f"rebuild failed: {exc}")
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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="log decision points to stderr and the debug log",
    )
    parser.add_argument("entry", nargs="?", default="")
    args = parser.parse_args(argv)
    env = os.environ
    if args.verbose:
        env = dict(env)
        env["PASS_ROFI_VERBOSE"] = "1"
    if args.rebuild and "ROFI_RETV" not in env:
        return run_rebuild(env=env)
    return run_rofi_mode([a for a in (args.entry,) if a], env)


if __name__ == "__main__":
    raise SystemExit(main())
