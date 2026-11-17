from __future__ import annotations

import os
from dataclasses import dataclass

# Default Bitwarden cloud region (EU). Override with PASS_TUI_BW_SERVER
# (e.g. https://vault.bitwarden.com or a self-hosted URL).
DEFAULT_BW_SERVER = "https://bitwarden.eu"


def _env(env, new_key: str, legacy_key: str, default: str | None = None) -> str | None:
    value = env.get(new_key)
    if value:
        return value
    value = env.get(legacy_key)
    if value:
        return value
    return default


@dataclass(frozen=True)
class Config:
    bw_bin: str = "bw"
    bw_server: str = DEFAULT_BW_SERVER
    cache_dir: str = ""
    cache_ttl: int = 600
    reopen_seconds: int = 20
    debug: bool = False
    verbose: bool = False

    @classmethod
    def from_env(cls, env) -> "Config":
        cache_home = env.get("XDG_CACHE_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache"
        )
        cache_dir = _env(env, "PASS_TUI_CACHE_DIR", "PASS_ROFI_CACHE_DIR")
        if not cache_dir:
            cache_dir = os.path.join(cache_home, "pass-tui")
            legacy = os.path.join(cache_home, "pass-rofi")
            if not os.path.isdir(cache_dir) and os.path.isdir(legacy):
                cache_dir = legacy
        return cls(
            bw_bin=_env(env, "PASS_TUI_BW_BIN", "PASS_TUI_PASS_CLI", "bw"),
            bw_server=_env(env, "PASS_TUI_BW_SERVER", "", DEFAULT_BW_SERVER) or DEFAULT_BW_SERVER,
            cache_dir=cache_dir,
            cache_ttl=int(_env(env, "PASS_TUI_CACHE_TTL", "PASS_ROFI_CACHE_TTL", "600")),
            reopen_seconds=int(
                _env(env, "PASS_TUI_REOPEN_SECONDS", "PASS_ROFI_REOPEN_SECONDS", "20")
            ),
            debug=_env(env, "PASS_TUI_DEBUG", "PASS_ROFI_DEBUG", "") == "1",
            verbose=_env(env, "PASS_TUI_VERBOSE", "PASS_ROFI_VERBOSE", "") == "1",
        )
