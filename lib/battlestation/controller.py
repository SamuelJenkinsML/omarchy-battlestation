"""Controller battery and driver detection from sysfs.

UPower is the primary battery source in QML; this is the fallback and the
data source for `battlestation doctor`. xpadneo only reports a coarse
capacity_level, so callers must not invent a percentage from it.
"""

from __future__ import annotations

import glob
import os
import subprocess

_LEVEL_PERCENT = {"critical": 5, "low": 20, "normal": 55, "high": 85, "full": 100}


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def batteries() -> list[dict]:
    out = []
    patterns = ("/sys/class/power_supply/xpadneo_battery_*",
                "/sys/class/power_supply/hid-*-battery",
                "/sys/class/power_supply/gip*")
    for pattern in patterns:
        for ps in sorted(glob.glob(pattern)):
            if _read(os.path.join(ps, "scope")).lower() not in ("device", ""):
                continue
            capacity = _read(os.path.join(ps, "capacity"))
            level = _read(os.path.join(ps, "capacity_level")).lower()
            out.append({
                "source": os.path.basename(ps),
                "model": _read(os.path.join(ps, "model_name")),
                "percent": int(capacity) if capacity.isdigit() else None,
                "level": level or None,
                "approxPercent": int(capacity) if capacity.isdigit() else _LEVEL_PERCENT.get(level),
                "status": _read(os.path.join(ps, "status")).lower() or "unknown",
            })
    return out


def loaded_drivers() -> list[str]:
    try:
        with open("/proc/modules", encoding="utf-8") as fh:
            mods = {line.split()[0] for line in fh}
    except OSError:
        return []
    names = {"hid_xpadneo": "xpadneo", "xone_gip": "xone", "xone_dongle": "xone", "xpad": "xpad",
             "hid_microsoft": "hid-microsoft"}
    return sorted({v for k, v in names.items() if k in mods})


def xpadneo_shift_mode_disabled() -> bool | None:
    value = _read("/sys/module/hid_xpadneo/parameters/disable_shift_mode")
    if not value:
        return None
    return value in ("1", "Y", "y")


def installed_packages(names: list[str]) -> dict[str, bool]:
    try:
        proc = subprocess.run(["pacman", "-Q", *names], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {n: False for n in names}
    have = {line.split()[0] for line in proc.stdout.splitlines() if line.strip()}
    return {n: n in have for n in names}
