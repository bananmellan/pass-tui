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
    for candidate in (
        HERE.parent.parent / "bin" / "pass-rofi-script",
        HERE.parent.parent.parent / "bin" / "pass-rofi-script",
    ):
        if candidate.exists():
            return str(candidate)
    return "pass-rofi-script"


def _script_env() -> dict:
    """Environment for the script-mode backend.

    Editable installs put pass_rofi on sys.path but not necessarily in the
    subprocess environment, so point the backend at the source tree when the
    package isn't importable. The shim also honors PASS_ROFI_SRC itself.
    """
    env = dict(os.environ)
    if os.environ.get("PASS_ROFI_SRC"):
        return env
    try:
        import pass_rofi  # noqa: F401

        return env
    except ImportError:
        pass
    src = HERE.parent.parent / "src"
    if (src / "pass_rofi").is_dir():
        env["PASS_ROFI_SRC"] = str(src)
    return env


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
        "Ctrl+l",
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
    args = list(sys.argv[1:] if argv is None else argv)
    verbose = "--verbose" in args or "-v" in args
    rofi = os.environ.get("PASS_ROFI_ROFI", "rofi")
    if shutil.which(rofi) is None:
        print(f"error: `{rofi}` not found in PATH", file=sys.stderr)
        return 1
    script = _script_entry()
    if verbose:
        script = f"{script} --verbose"
    proc = subprocess.run(build_rofi_argv(script), env=_script_env())
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
