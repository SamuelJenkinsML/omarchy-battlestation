"""Live signal state for the in-game overlay: what the kernel is actually
sending each display (VRR engaged, HDR metadata, colorspace), streamed as JSON
lines while the overlay is up. Hyprland's JSON only shows configuration, and
the auto HDR / fullscreen VRR switches are not announced as events.
"""

from __future__ import annotations

import json
import sys
import time

from . import drmprops


def snapshot(drm: dict) -> dict:
    """drmprops.read() -> {connector: {vrrEnabled, hdrMetadata, colorspace, maxBpc}}."""
    return {
        name: {
            "vrrEnabled": d.get("vrr_enabled"),
            "hdrMetadata": bool(d.get("hdr_metadata")),
            "colorspace": d.get("colorspace"),
            "maxBpc": d.get("max_bpc"),
        }
        for name, d in drm.items()
    }


def should_emit(prev: dict | None, cur: dict, since_last: float, heartbeat_s: float) -> bool:
    return prev != cur or since_last >= heartbeat_s


def tail(poll_s: float = 1.0, heartbeat_s: float = 10.0) -> None:
    """Emit {"event": "live", "monitors": ...} on change (and every heartbeat_s). Runs forever."""
    prev, last = None, 0.0
    while True:
        try:
            cur = snapshot(drmprops.read())
        except OSError:
            cur = {}
        now = time.monotonic()
        if should_emit(prev, cur, now - last, heartbeat_s):
            sys.stdout.write(json.dumps({"event": "live", "monitors": cur}, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            prev, last = cur, now
        time.sleep(poll_s)


def main_tail() -> int:
    try:
        tail()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0
