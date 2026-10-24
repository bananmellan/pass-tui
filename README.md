# pass-tui

A fast, secure **terminal UI** for [Proton Pass](https://proton.me/pass), built on
the official [`pass-cli`](https://protonpass.github.io/pass-cli/). No rofi, no
GUI toolkit — just run it from a terminal. (This is a rewrite of the original
bash/fish rofi prototype, and of the intermediate rofi-based Python version,
into a self-contained curses application.)

## Features

- Launching `pass-tui` opens a full-screen terminal list of items from **all
  vaults**, sorted by title. Every row shows the item's **type badge**
  (`login`, `note`, `alias` for hide-my-email addresses, `card`, `identity`,
  `ssh key`, `wifi`, `custom`), so aliases are never confused with logins.
  The badge comes from pass-cli's `ItemContent` enum (`Login`/`Note`/`Alias`/
  `CreditCard`/`Identity`/`SshKey`/`Wifi`/`Custom`, serialized externally
  tagged as `content.content = {"Login": {...}}` etc.). Typing a badge name
  (e.g. `alias`) filters to exactly that type, ranked first.
  **Search is live**: the list filters as you type.
- Items are listed from a local cache (`$XDG_CACHE_HOME/pass-tui/items.v1.json`)
  containing only **title, item id, share id, urls, vault name, item type and
  item uuid** — never passwords, notes or other field values. The rebuild runs
  `pass-cli item list --show-secrets` (required: without it pass-cli emits
  title/id summaries with no urls) but parses the JSON in-process and keeps
  only the non-sensitive fields listed above; secrets are never written to disk.
- Startup is fast: the list renders straight from the cache; when the cache is
  older than `PASS_TUI_CACHE_TTL` seconds (default 600) a background rebuild is
  spawned without blocking the UI.
- Vault listing is parallelized across vaults (thread pool), so even large
  accounts rebuild quickly.
- If the clipboard contains an `http(s)://` URL or a bare hostname (`wl-paste`,
  `xclip` or `xsel`), items whose cached urls contain the clipboard hostname
  (fuzzy: path, query, trailing slashes and case are ignored) are surfaced: a
  single match opens it directly, multiple matches are listed best-first with
  the hostname prefilled as the search query, zero matches falls back to the
  full list. URL matching only ever reads the cache, and runs on every launch
  while the clipboard holds a URL. Match ranking: exact host equality first,
  then subdomain, then host-contains; ties break toward titles that name the
  host (including camelCase initials, so "NextCloud" ranks for `nc.honung.org`),
  then items with fewer unrelated urls.
- Selecting an item fetches its secrets on demand (`pass-cli item view`) and
  shows a field menu; selecting a field copies its value to the clipboard.
  TOTP codes are generated via `pass://<share>/<item>/totp`. Field values are
  never printed to the screen — only copied.
- After **copying** a field, relaunching within `PASS_TUI_REOPEN_SECONDS`
  (default 20) reopens the same item's field menu — once. The reopen memory is
  cleared on every program start, so if you close without copying and relaunch,
  you get the regular list. Merely opening an item never arms this.
- Items are deduped by `item_uuid` when present (the content-level identity,
  stable across re-imports/sync conflicts), falling back to `item_id`
  (globally unique in Proton Pass), so a shared item visible through two
  vaults — or the same item listed twice with different ids — appears once.
- Alias items expose their address as a copyable `email:` field; the
  structural fields (mailboxes, prefix/suffix, flags) are hidden.

## Requirements

- Python >= 3.10 (standard library only — curses ships with CPython)
- `pass-cli` (logged in: `pass-cli login`)
- `wl-copy`/`wl-paste`, `xclip` or `xsel` for clipboard integration
- A terminal with curses support

## Install

```sh
git clone https://github.com/bananmellan/pass
cd pass
pip install .            # or: pipx install .
```

Or run from the checkout:

```sh
./bin/pass-tui
```

Bind `pass-tui` (or `bin/pass-tui`) to a key in your window manager or shell.

## Usage

- Type to filter items (matches on title, urls and vault name; substring first,
  then fuzzy subsequence for tokens of 3+ characters).
- `↑`/`↓` (or `PgUp`/`PgDn`, `Home`/`End`) move the selection; `Enter` opens
  the selected item's field menu. Emacs-style binds also work: `Ctrl+n`/`Ctrl+p`
  move down/up, `Ctrl+m` (or `Ctrl+j`) is enter, `Ctrl+g` is back/quit.
- The list renders fixed-width columns (title, type badge, url, vault), colored
  when the terminal supports colors.
- The field menu uses the same style: fixed-width (kind, value) columns with a
  short dimmed preview of each value after it (TOTP never shows a preview; its
  code is fetched live at copy time).
- In the field menu, `Enter` copies the field's value to the clipboard and
  **quits the program** (like rofi). The last-copied item's menu reopens on the
  next launch within `PASS_TUI_REOPEN_SECONDS` (default 20 s). `Esc` goes back
  to the item list; `Esc` again (or with an empty query) quits.
- When the clipboard holds a URL, the matching items are listed with the host
  prefilled as the query. Host-like queries match precisely (substring only —
  no fuzzy subsequence), so unrelated items never appear; once you edit the
  query past the host, normal fuzzy matching resumes.
- Every copyable field of every item type is offered: note, login
  email/username/password, TOTP, urls, alias addresses (hide-my-email items),
  credit-card number/PIN, identity fields, ssh keys, wifi passwords, custom
  sections, and extra fields.
- `Esc` in the list first clears the search query, then quits.
- `Ctrl-r` forces a synchronous cache rebuild. `Ctrl-c` quits immediately.

### Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `PASS_TUI_PASS_CLI` | `pass-cli` | pass-cli executable |
| `PASS_TUI_CACHE_DIR` | `$XDG_CACHE_HOME/pass-tui` | cache/state directory |
| `PASS_TUI_CACHE_TTL` | `600` | cache freshness in seconds |
| `PASS_TUI_REOPEN_SECONDS` | `20` | reopen last item's menu after a copy |
| `PASS_TUI_DEBUG` | unset | set to `1` to log to `debug.log` |
| `PASS_TUI_VERBOSE` | unset | set to `1` (or pass `--verbose`) to log decision points (clipboard content, extracted host, cache stats, match count) to stderr and `debug.log` |

The legacy `PASS_ROFI_*` variables from the previous rofi-based version are
still honored as fallbacks, and an existing `$XDG_CACHE_HOME/pass-rofi` cache
directory is reused if no `pass-tui` directory exists yet.

Debugging: run `pass-tui --verbose` (or `PASS_TUI_VERBOSE=1 pass-tui`) with a
URL in the clipboard and watch stderr — or `tail -f
$XDG_CACHE_HOME/pass-tui/debug.log` — to see the clipboard text, the extracted
host, how many cached items have urls, and how many matched.

## Security model

- The cache stores only non-sensitive metadata (titles, ids, urls, vault
  names, item type/uuid). Items are resolved by their exact `share_id` +
  `item_id`, so duplicate titles can never open the wrong item.
- Secrets are fetched only when an item is opened or a field is copied, passed
  through pipes, and written straight to the clipboard — never written to disk
  by this tool and never printed to the screen.
- Cache and state files are written atomically with `0600` permissions.
- Field values are never logged; debug logging records only titles, ids and
  error messages.

## Development

```sh
python3 -m pytest
```

Layout:

```
bin/pass-tui             launcher for running from a checkout
src/pass_tui/
  cli.py                 entry point: arg parsing, --rebuild, tty check
  app.py                 UI-agnostic controller (screens + key handling)
  tui.py                 curses rendering and key translation
  search.py              live filtering (token substring/subsequence ranking)
  types.py               item-type normalization and list badges
  passcli.py             pass-cli subprocess wrapper + JSON parsing
  cache.py               metadata cache (atomic, 0600, TTL, background refresh)
  state.py               last-copied / open-item state (one-shot reopen)
  clipboard.py           wl-clipboard/xclip/xsel abstraction, URL host extraction
  fields.py              item-view JSON -> field labels and values
  config.py              environment-based configuration
  models.py              Vault / ItemRef dataclasses
  dedup.py               item dedup by item_uuid / item_id
tests/                   pytest suite (app controller, search, tui, pty smoke)
```
