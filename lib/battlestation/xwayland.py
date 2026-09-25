"""XWayland's RandR primary output: the screen Wine games take for the main one.

The parked stream output is lit (a Hyprland with no output at all falls back to
a placeholder, and coming back from that on NVIDIA has frozen the display), so
XWayland lists it as a monitor too. Unless something else is marked primary,
Wine may pick it and offer only the Deck's modes. Marking the real display
primary keeps games on it; while a client is attached the stream output is the
only one on, and it is marked primary instead.

xrandr against XWayland warns on stderr and exits 0 even for an output it does
not know, so success is read back rather than trusted.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

_PRIMARY_RE = re.compile(r"^(\S+) connected primary\b", re.M)
_SOCKET_RE = re.compile(r"^X(\d+)$")


def display(env=None, sockets: Path = Path("/tmp/.X11-unix")) -> str:
    """$DISPLAY, or the lowest X socket there is: Sunshine's prep-cmd may not inherit one."""
    env = os.environ if env is None else env
    value = str(env.get("DISPLAY") or "").strip()
    if value:
        return value
    try:
        numbers = sorted(int(m.group(1)) for p in sockets.iterdir() if (m := _SOCKET_RE.match(p.name)))
    except OSError:
        numbers = []
    return f":{numbers[0]}" if numbers else ""


def _xrandr(args: list[str]) -> str | None:
    disp = display()
    if not disp or not shutil.which("xrandr"):
        return None
    try:
        proc = subprocess.run(["xrandr", *args], capture_output=True, text=True, timeout=3,
                              env={**os.environ, "DISPLAY": disp})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def parse_primary(query: str) -> str | None:
    match = _PRIMARY_RE.search(query or "")
    return match.group(1) if match else None


def primary() -> str | None:
    return parse_primary(_xrandr(["--query"]) or "")


def set_primary(name: str, wait: float = 1.0) -> bool:
    """Mark `name` primary. A just-enabled output takes XWayland a moment to
    learn about, so keep trying for up to `wait` seconds. True once it holds."""
    deadline = time.monotonic() + wait
    while True:
        if _xrandr(["--output", name, "--primary"]) is None:
            return False  # no xrandr or no X server: nothing to retry
        if primary() == name:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
