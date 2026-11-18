# pass-tui

A fast, secure **terminal UI** for [Bitwarden](https://bitwarden.com), built on
the official [`bw` CLI](https://bitwarden.com/help/cli/) and pointed at the EU
cloud ([bitwarden.eu](https://bitwarden.eu)) by default. No GUI toolkit — just
run it from a terminal.

## Features

- Launching `pass-tui` opens a full-screen terminal list of items from **all
  vaults** (your personal vault plus every organization), sorted by title.
  Each item takes two rows: the first shows the item's **type emoji**, title,
  **type badge** (`login`, `note`, `card`, `identity`, `custom` — mapped from
  bw's numeric item `type`), **folder**, and **vault**; the vault column is
  hidden when only one vault exists. The second row shows the item's first
  url (when it has one). Typing a badge name (e.g. `card`) filters to exactly
  that type, ranked first.
  **Search is live**: the list filters as you type. Like rofi, an empty
  prompt shows no results — items appear once at least one character is in
  the search bar. Matches cover title, urls, vault name and folder.
- Items are listed from a local cache (`$XDG_CACHE_HOME/pass-tui/items.v4.json`)
  containing **title, item id, urls, vault name, item type, folder** and the
  **non-sensitive field values** (usernames, emails, notes, urls, Text extra
  fields, identity details, ...). The rebuild runs `bw sync` +
  `bw list items` (plus `bw list folders` / `bw list organizations` for the
  folder and vault names) and parses the JSON in-process. **Secret values are
  never written to disk**: hidden fields (passwords, PINs, card numbers, ...)
  are cached as label-only entries (name kept, value dropped), so the field
  menu can list them censored but copying one always fetches the value live
  from bw.
- Startup shows a splash screen immediately: the session check
  ("Checking session…") and any account-email / master-password prompt render
  inside the UI (passwords as one ● per character, in a box), so a slow bw
  never leaves a blank terminal. Esc or Ctrl-c cancels the boot at any point.
- Startup is fast: the list renders straight from the cache; when the cache
  is missing or older than `PASS_TUI_CACHE_TTL` seconds (default 600) a
  background rebuild refreshes it without blocking the UI — the list shows
  "Reloading items…" until the fresh items land.
- Navigation is snappy: opening an item and copying a non-sensitive field are
  served entirely from the cache — no bw call. Only copying a sensitive
  field (password, PIN, TOTP, ...) triggers a live `bw get item <id>` call;
  TOTP codes come from `bw get totp <id>`.
- Loading feedback: while any slow operation runs (initial cache refresh,
  `Ctrl-r` refresh, opening an item without cached fields, fetching a
  sensitive field, generating a TOTP code) the header shows what is happening
  ("Reloading items…", "Loading <title>…", "Fetching <field>…",
  "Generating TOTP code…").
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
- Selecting an item shows a field menu (instantly, from the cache); selecting
  a field copies its value to the clipboard. **Enter on a url field opens the
  url in your browser instead of copying it.** Sensitive fields are fetched
  on demand (`bw get item <id>`) at copy time. Field values are never printed
  to the screen — only copied (or opened).
- After **copying** a field or **opening a url**, relaunching within
  `PASS_TUI_REOPEN_SECONDS` (default 20) reopens the same item's field menu —
  once. The reopen memory is cleared on every program start, so if you close
  without copying and relaunch, you get the regular list. Merely opening an
  item never arms this.
- Items are deduped by item id (bw item ids are unique), so the same item
  listed twice appears once.
- Custom fields of an item are offered as `field: <name>` entries; hidden
  custom fields are censored like passwords.

## Requirements

- Python >= 3.10 (standard library only — curses ships with CPython)
- [`bw`](https://bitwarden.com/help/cli/) — the official Bitwarden CLI
  (`npm install -g @bitwarden/cli`). Log in once with `bw login`
  (interactive/SSO/2FA), or set `BW_CLIENTID` + `BW_CLIENTSECRET` in the
  environment for API-key login. pass-tui points bw at `https://bitwarden.eu`
  automatically on startup (`bw config server`; override with
  `PASS_TUI_BW_SERVER`, e.g. `https://vault.bitwarden.com` or a self-hosted
  URL) and refuses to send any credential if the configured region cannot be
  confirmed. bw refuses server changes while a session is active, so if bw is
  already logged in on a different region, pass-tui exits with a hint: run
  `bw logout` first and start pass-tui again (it will configure the server
  and log you in), or set `PASS_TUI_BW_SERVER` to the region bw is on.
- `wl-copy`/`wl-paste`, `xclip` or `xsel` for clipboard integration
- A terminal with curses support

## Install

```sh
git clone https://github.com/bananmellan/pass-tui
cd pass-tui
pip install .            # or: pipx install .
```

Or run from the checkout:

```sh
./bin/pass-tui
```

Bind `pass-tui` (or `bin/pass-tui`) to a key in your window manager or shell.

## Usage

- The list starts empty: type to filter items (matches on title, urls, vault
  name and folder; substring first, then fuzzy subsequence for tokens of
  3+ characters). Results appear as soon as the prompt has at least one
  character. Special characters (å, ä, ö, ...) type normally.
- `↑`/`↓` (or `PgUp`/`PgDn`, `Home`/`End`) move the selection; `Enter` opens
  the selected item's field menu. Emacs-style binds also work: `Ctrl+n`/`Ctrl+p`
  move down/up, `Ctrl+m` (or `Ctrl+j`) is enter, `Ctrl+l` is back/quit.
- The list renders fixed-width columns per row (emoji, title, type badge,
  folder, vault), colored when the terminal supports colors; the search bar
  and titles are centered.
- The field menu uses the same style: fixed-width (kind, value) columns with a
  short dimmed preview of each value after it. **Hidden fields are censored**:
  passwords, PINs, card numbers, CVVs, private keys, SSN/passport/license
  numbers, TOTP, and any extra/custom field whose content kind is hidden show
  a fixed-length `••••••` mask (so neither the value nor its length leaks).
  Usernames, emails, urls, notes, and text fields are shown in full. Copying
  always copies the real value.
- In the field menu, `Enter` copies the field's value to the clipboard and
  **quits the program** (like rofi) — unless the field is a url, in which case
  the url is opened in the browser instead. The last-copied/last-opened
  item's menu reopens on the next launch within `PASS_TUI_REOPEN_SECONDS`
  (default 20 s). `Esc` goes back to the item list; `Esc` again (or with an
  empty query) quits.
- When the clipboard holds a URL, the matching items are listed with the host
  prefilled as the query. Host-like queries match precisely (substring only —
  no fuzzy subsequence), so unrelated items never appear; once you edit the
  query past the host, normal fuzzy matching resumes.
- Every copyable field of every item type is offered: note, login
  username/password/TOTP, urls, credit-card fields, identity fields, custom
  sections, and extra fields.
- `Esc` in the list first clears the search query, then quits.
- `Ctrl-r` forces a synchronous cache rebuild. `Ctrl-c` quits immediately.

### Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `PASS_TUI_BW_BIN` | `bw` | bw executable (`PASS_TUI_PASS_CLI` still honored as a legacy alias) |
| `PASS_TUI_BW_SERVER` | `https://bitwarden.eu` | Bitwarden server/region (`bw config server`); e.g. `https://vault.bitwarden.com` or a self-hosted URL |
| `PASS_TUI_CACHE_DIR` | `$XDG_CACHE_HOME/pass-tui` | cache/state directory |
| `PASS_TUI_CACHE_TTL` | `600` | cache freshness in seconds |
| `PASS_TUI_REOPEN_SECONDS` | `20` | reopen the last item's menu after a copy/open |
| `PASS_TUI_DEBUG` | unset | set to `1` to log to `debug.log` |
| `PASS_TUI_VERBOSE` | unset | set to `1` (or pass `--verbose`) to log decision points (clipboard content, extracted host, cache stats, match count) to stderr and `debug.log` |

The legacy `PASS_ROFI_*` environment variables are still honored as fallbacks,
and an existing `$XDG_CACHE_HOME/pass-rofi` cache directory is reused if no
`pass-tui` directory exists yet.

Debugging: run `pass-tui --verbose` (or `PASS_TUI_VERBOSE=1 pass-tui`) with a
URL in the clipboard and watch stderr — or `tail -f
$XDG_CACHE_HOME/pass-tui/debug.log` — to see the clipboard text, the extracted
host, how many cached items have urls, and how many matched.

## Security model

- The cache stores only non-sensitive data: metadata (titles, ids, urls, vault
  names, item types, folders) plus non-sensitive field values (usernames,
  emails, notes, urls, Text extra fields, identity details). Items are
  resolved by their exact item id, so duplicate titles can never open the
  wrong item.
- Secrets are never cached and never written to disk. Hidden field *names*
  are cached (so the menu can list them), but their *values* are dropped and
  fetched live from bw only when copied, passed through pipes, and written
  straight to the clipboard — never printed to the screen. The field view
  censors hidden fields (passwords, PINs, card numbers, CVVs, private keys,
  SSN/passport/license numbers, TOTP, and hidden extra fields) behind a
  fixed-length mask.
- Cache and state files are written atomically with `0600` permissions.
- Field values are never logged; debug logging records only titles, ids and
  error messages.
- The bw **session key** (the `bw unlock --raw` / `bw login --raw` output) is
  kept in memory only and injected into every bw child as the `BW_SESSION`
  environment variable — never via argv (visible in the process list) and
  never written to disk.
- The **master password** is handed to `bw unlock` / `bw login` through a
  child-only environment variable referenced by `--passwordenv` — never via
  argv, never printed, never logged, never written to disk by pass-tui.
- pass-tui's startup gate distinguishes three bw states (`bw status`):
  **unlocked** (start immediately), **locked** (prompt for the master
  password, hidden input with one ● per character; a wrong password
  re-prompts), and **logged out** (if `BW_CLIENTID` + `BW_CLIENTSECRET` are
  set, log in with the API key via `bw login --apikey` and fall through to
  the unlock prompt — an API key cannot derive the vault key; otherwise
  prompt for the account email (visible) and the master password (hidden) and
  run `bw login <email>`). Accounts with 2FA/SSO cannot complete `bw login`
  non-interactively — log in once with `bw login` in a terminal first, then
  start pass-tui.
- pass-tui refuses to continue if bw cannot be pointed at the configured
  server, so the master password is never sent to the wrong region.

## Development

```sh
python3 -m pytest
```

Layout:

```
bin/pass-tui             launcher for running from a checkout
src/pass_tui/
  cli.py                 entry point: arg parsing, --rebuild, tty check
  auth.py                startup auth gate: unlocked / locked (master
                         password) / API-key or email+password login
  app.py                 UI-agnostic controller (screens + key handling)
  tui.py                 curses rendering, key translation, boot splash
  search.py              live filtering (token substring/subsequence ranking)
  types.py               item-type normalization and list badges/emojis
  bitwarden.py           bw subprocess wrapper + JSON parsing + item adapter
  cache.py               item cache (atomic, 0600, TTL, background refresh;
                         stores metadata + non-sensitive field values only)
  state.py               last-copied / open-item state (one-shot reopen)
  clipboard.py           wl-clipboard/xclip/xsel abstraction, URL host extraction
  fields.py              item-view JSON -> field labels and values;
                         safe-field capture + cache-view reconstruction
  config.py              environment-based configuration
  models.py              ItemRef dataclass
  dedup.py               item dedup by item_uuid / item_id
tests/                   pytest suite (app controller, search, tui, pty smoke)
```
