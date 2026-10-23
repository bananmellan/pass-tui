from __future__ import annotations

import os

RETV_INITIAL = 0
RETV_SELECTED = 1
RETV_CUSTOM = 2
RETV_CUSTOM_KEY_BASE = 10

STAGE_LIST = "list"
STAGE_FIELDS = "fields"

ACTION_REFRESH = "refresh"


def retv_from_env(env) -> int:
    try:
        return int(env.get("ROFI_RETV", "0"))
    except ValueError:
        return RETV_INITIAL


def info_from_env(env) -> str:
    return env.get("ROFI_INFO", "")


def mode_option(key: str, value: str) -> str:
    return f"\x00{key}\x1f{value}\n"


def emit(rows: list[str]) -> None:
    if rows:
        print("\n".join(rows))
