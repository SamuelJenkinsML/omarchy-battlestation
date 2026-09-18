"""Steam client helpers: is it running, and open Big Picture.

Launch split (running client needs the steam:// URI, a cold start takes
-gamepadui) and the Big Picture title pattern follow
silvaio/gamemode-switcher (MIT, (c) 2026 silvaio).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from . import mangohud

BIG_PICTURE_TITLE = re.compile(r"Big Picture|Gamepad UI|Steam Deck", re.I)


def pid() -> int | None:
    try:
        value = int(Path.home().joinpath(".steam", "steam.pid").read_text().strip())
    except (OSError, ValueError):
        value = None
    if value and os.path.exists(f"/proc/{value}"):
        try:
            cmdline = Path(f"/proc/{value}/cmdline").read_bytes()
        except OSError:
            cmdline = b""
        if b"steam" in cmdline:
            return value
    try:
        out = subprocess.run(["pgrep", "-n", "-f", r"/ubuntu12_32/steam( |$)"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        return int(out) if out else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def running() -> bool:
    return pid() is not None


def open_big_picture() -> str:
    if not shutil.which("steam"):
        raise RuntimeError("steam is not installed")
    if running():
        subprocess.Popen(["steam", "steam://open/bigpicture"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "uri"
    env = dict(os.environ)
    env.update(mangohud.steam_env())
    launcher = ["uwsm-app", "--"] if shutil.which("uwsm-app") else []
    subprocess.Popen([*launcher, "steam", "-gamepadui"], env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return "cold-start"


def is_big_picture(window_class: str, title: str) -> bool:
    return window_class.lower() == "steam" and bool(BIG_PICTURE_TITLE.search(title or ""))
