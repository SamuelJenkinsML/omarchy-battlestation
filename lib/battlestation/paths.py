"""Filesystem locations, resolved from XDG variables at call time so tests can
point them somewhere else by setting the environment."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "").strip()
    return Path(value) if value else Path.home() / fallback


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "battlestation"


def config_file() -> Path:
    return config_dir() / "config.toml"


def tv_token_file() -> Path:
    return config_dir() / "tv-token"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "battlestation"


def state_file() -> Path:
    return state_dir() / "state.json"


def revert_stash() -> Path:
    return state_dir() / "revert-scene.lua"


def scene_lua() -> Path:
    """The only Hyprland file we write.

    Omarchy's default.hypr.toggles requires every *.lua in this directory after
    ~/.config/hypr/monitors.lua, so our rules win without touching the user's
    own monitor config.
    """
    return _xdg("XDG_STATE_HOME", ".local/state") / "omarchy" / "toggles" / "hypr" / "battlestation-scene.lua"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR", "").strip() or f"/run/user/{os.getuid()}"
    return Path(base) / "battlestation"


def mangohud_log_dir() -> Path:
    return runtime_dir() / "mangohud"


def mangohud_config() -> Path:
    return config_dir() / "mangohud.conf"


def steam_desktop_override() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "applications" / "steam.desktop"


def plugin_root() -> Path:
    # lib/battlestation/paths.py -> plugin root
    return Path(__file__).resolve().parents[2]
