from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _script_entry() -> str:
    override = os.environ.get("PASS_ROFI_SCRIPT")
    if override:
        return override
    packaged = HERE.parent.parent / "bin" / "pass-rofi-script"
    if packaged.exists():
        return str(packaged)
    local = HERE.parent.parent.parent / "bin" / "pass-rofi-script"
    if local.exists():
        return str(local)
    return "pass-rofi-script"


def detect_theme() -> str:
    try:
        out = subprocess.run(
            ["dconf", "read", "/org/gnome/desktop/interface/color-scheme"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        out = ""
    return "Dark" if "prefer-dark" in out else "Arc"


def build_rofi_argv(script: str) -> list[str]:
    theme = detect_theme()
    argv = [
        "rofi",
        "-normal-window",
        "-kb-custom-1",
        "Ctrl+l,Alt+b,BackSpace",
        "-kb-mode-complete",
        "",
        "-modi",
        f"pass:{script}",
        "-show",
        "pass",
        "-theme-str",
        "window { width: 720px; }",
    ]
    for base in (
        Path.home() / ".linuxbrew/.linuxbrew/share/rofi/themes",
        Path("/usr/share/rofi/themes"),
    ):
        candidate = base / f"{theme}.rasi"
        if candidate.exists():
            argv.extend(["-theme", theme])
            break
    return argv


def main(argv: list[str] | None = None) -> int:
    rofi = os.environ.get("PASS_ROFI_ROFI", "rofi")
    if shutil.which(rofi) is None:
        print(f"error: `{rofi}` not found in PATH", file=sys.stderr)
        return 1
    script = _script_entry()
    proc = subprocess.run(build_rofi_argv(script))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
