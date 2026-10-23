# pass-rofi

A fast, secure [rofi](https://github.com/davatorium/rofi) frontend for
[Proton Pass](https://proton.me/pass), built on the official
[`pass-cli`](https://protonpass.github.io/pass-cli/).

This is a rewrite of the original two-file bash/fish prototype in Python,
split into small, testable modules.

## Features

- Launching `pass-rofi` opens a rofi window listing all items from **all vaults**.
- Items are listed from a local cache (`$XDG_CACHE_HOME/pass-rofi/items.v1.json`)
  containing only **title, item id, share id, urls, vault name and item type** —
  never passwords, notes or other field values.
- Startup is fast: rofi shows the cached list immediately; when the cache is
  older than `PASS_ROFI_CACHE_TTL` seconds (default 600) a background rebuild is
  spawned without blocking the UI.
- Vault listing is parallelized across vaults (thread pool), so even large
  accounts rebuild quickly.
- If the clipboard contains an `http(s)://` URL (`wl-paste`, `xclip` or `xsel`),
  items whose cached urls contain the clipboard hostname (fuzzy: path, query,
  trailing slashes and case are ignored) are surfaced: a single match opens it
  directly, multiple matches are listed, zero matches falls back to the full
  list. URL matching only ever reads the cache, and runs on every launch while
  the clipboard holds a URL.
- Going back to the item list (Ctrl+l, unknown selection, refresh row) renders
  straight from the cache and never blocks on `pass-cli`; a stale cache triggers
  a background rebuild instead.
- Rows are rendered with pango markup (bold title, dimmed url/vault) via the
  script-mode `markup-rows` option. The row's *entry text* is an opaque token
  (`item<US><share_id><US><item_id>`); the human-readable text is attached with
  the row `display` property, searchable text with `meta`, and the token is
  mirrored into `info` (`ROFI_INFO`), so selection resolves the exact item
  even with duplicate titles.
- Custom keybindings (`Ctrl+l` etc.) are enabled via the script-mode
  `use-hot-keys` option; without it rofi swallows them in script mode.
- Selecting an item fetches its secrets on demand (`pass-cli item view`) and
  shows a field menu; selecting a field copies its value to the clipboard.
  TOTP codes are generated via `pass://<share>/<item>/totp`.
- After **copying** a field, relaunching within `PASS_ROFI_REOPEN_SECONDS`
  (default 20) reopens the same item's field menu. Merely opening an item and
  closing rofi does not arm this — the reopen state is separate from the
  currently open item.
- Search works on title, urls and vault name: each row carries a `meta`
  property with the searchable text (rofi only token-matches the entry text,
  which is an opaque id token).
- `Ctrl+l` (rofi custom keybinding 1, enabled via the script-mode
  `use-hot-keys` option) goes back to / refreshes the item list; the first
  row ("🔄 Refresh item list") forces a synchronous rebuild.

## Requirements

- Python >= 3.10 (standard library only)
- `pass-cli` (logged in: `pass-cli login`)
- `rofi`
- `wl-copy`/`wl-paste`, `xclip` or `xsel` for clipboard integration
- `fish` (only for the `bin/pass-rofi` launcher) and `dconf` (theme detection)

## Install

```sh
git clone https://github.com/bananmellan/rofi-proton-pass-cli
cd rofi-proton-pass-cli
pip install .            # or: pipx install .
```

Or run from the checkout:

```sh
./bin/pass-rofi          # fish launcher -> rofi -modi pass:./bin/pass-rofi-script
```

Bind `pass-rofi` (or `bin/pass-rofi`) to a key in your window manager.

## Usage

- Type to filter items; press `Enter` on an item to open its field menu.
- In the field menu, press `Enter` on a field to copy its value.
- `Ctrl+l` or the refresh row rebuilds the cache.
- Esc closes rofi.

### Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `PASS_ROFI_PASS_CLI` | `pass-cli` | pass-cli executable |
| `PASS_ROFI_CACHE_DIR` | `$XDG_CACHE_HOME/pass-rofi` | cache/state directory |
| `PASS_ROFI_CACHE_TTL` | `600` | cache freshness in seconds |
| `PASS_ROFI_REOPEN_SECONDS` | `20` | reopen last item's menu after a copy |
| `PASS_ROFI_DEBUG` | unset | set to `1` to log to `debug.log` |
| `PASS_ROFI_ROFI` | `rofi` | rofi executable (launcher only) |
| `PASS_ROFI_SCRIPT` | auto | override the script-mode backend path |

## Security model

- The cache stores only non-sensitive metadata (titles, ids, urls, vault names).
  Items are re-identified by `share_id` + `item_id` via rofi's `info` row
  property, so duplicate titles can never open the wrong item.
- Secrets are fetched only when an item is opened, passed through pipes, and
  written straight to the clipboard — never written to disk by this tool.
- Cache and state files are written atomically with `0600` permissions.
- Field values are never logged; debug logging records only titles, ids and
  error messages.

## Development

```sh
python3 -m pytest
```

Layout:

```
bin/pass-rofi            fish launcher (rofi -modi pass:...)
bin/pass-rofi-script     python entry point for rofi script mode
src/pass_rofi/
  cli.py                 rofi script-mode state machine
  launcher.py            python launcher (alternative to the fish script)
  passcli.py             pass-cli subprocess wrapper + JSON parsing
  cache.py               metadata cache (atomic, 0600, TTL, background refresh)
  state.py               stage / last-copied / open-item state
  display.py             rofi row formatting (pango + info payloads)
  clipboard.py           wl-clipboard/xclip/xsel abstraction, URL host extraction
  fields.py              item-view JSON -> field labels and values
  rofi.py                ROFI_RETV / ROFI_INFO protocol constants
  config.py              environment-based configuration
  models.py              Vault / ItemRef dataclasses
tests/                   pytest suite (67 tests)
```
