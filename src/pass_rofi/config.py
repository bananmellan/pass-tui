from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    pass_cli_bin: str = "pass-cli"
    cache_dir: str = ""
    cache_ttl: int = 600
    reopen_seconds: int = 20
    clear_seconds: int = 45
    lock_seconds: int = 300
    retry_delay: float = 2.0
    debug: bool = False

    @classmethod
    def from_env(cls, env) -> "Config":
        cache_home = env.get("XDG_CACHE_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache"
        )
        return cls(
            pass_cli_bin=env.get("PASS_ROFI_PASS_CLI", "pass-cli"),
            cache_dir=env.get("PASS_ROFI_CACHE_DIR")
            or os.path.join(cache_home, "pass-rofi"),
            cache_ttl=int(env.get("PASS_ROFI_CACHE_TTL", "600")),
            reopen_seconds=int(env.get("PASS_ROFI_REOPEN_SECONDS", "20")),
            clear_seconds=int(env.get("PASS_ROFI_CLIPBOARD_CLEAR_SECONDS", "45")),
            debug=env.get("PASS_ROFI_DEBUG", "") == "1",
        )
