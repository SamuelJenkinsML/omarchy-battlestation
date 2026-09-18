"""Passive gamepad reader: presence events and a hold-to-fire button chord.

Runs as a child process of Service.qml and speaks one JSON object per line on
stdout. It never grabs a device (games and Steam keep working) and needs no
extra groups: logind's uaccess ACL already lets the seated user read joystick
nodes.

Device detection follows perfektnacht/controller-launcher (MIT, (c) 2026
perfektnacht): a pad has BTN_SOUTH plus ABS_X/ABS_Y. The node cache and
select-with-deadline loop follow perminder-klair/omarchy-controller-control
(MIT, (c) 2026 Parminder Klair).
"""

from __future__ import annotations

import errno
import json
import os
import select
import signal
import sys
import time
from dataclasses import dataclass, field

from . import evdev as li

BTN = {
    "BTN_SOUTH": 0x130, "BTN_EAST": 0x131, "BTN_NORTH": 0x133, "BTN_WEST": 0x134,
    "BTN_TL": 0x136, "BTN_TR": 0x137, "BTN_TL2": 0x138, "BTN_TR2": 0x139,
    "BTN_SELECT": 0x13A, "BTN_START": 0x13B, "BTN_MODE": 0x13C,
    "BTN_THUMBL": 0x13D, "BTN_THUMBR": 0x13E,
}
ALIASES = {
    "A": "BTN_SOUTH", "B": "BTN_EAST", "X": "BTN_NORTH", "Y": "BTN_WEST",
    "LB": "BTN_TL", "RB": "BTN_TR", "VIEW": "BTN_SELECT", "BACK": "BTN_SELECT",
    "SELECT": "BTN_SELECT", "MENU": "BTN_START", "START": "BTN_START",
    "GUIDE": "BTN_MODE", "XBOX": "BTN_MODE", "HOME": "BTN_MODE", "MODE": "BTN_MODE",
    "LS": "BTN_THUMBL", "RS": "BTN_THUMBR",
}
BTN_SOUTH, ABS_X, ABS_Y = 0x130, 0x00, 0x01


def chord_codes(names: list[str]) -> frozenset[int]:
    codes = set()
    for raw in names:
        name = str(raw).strip().upper()
        name = ALIASES.get(name, name)
        if name not in BTN:
            raise ValueError(f"unknown button {raw!r} (use e.g. BTN_SELECT, VIEW, MENU, GUIDE, A)")
        codes.add(BTN[name])
    if not codes:
        raise ValueError("chord needs at least one button")
    return frozenset(codes)


class ChordEngine:
    """Fires once when every chord button has been held for hold_s.

    All chord buttons must be released before it can fire again, so holding
    the chord never repeats.
    """

    def __init__(self, codes: frozenset[int], hold_s: float):
        self.codes = codes
        self.hold_s = hold_s
        self.held: set[int] = set()
        self.deadline: float | None = None
        self.armed = True

    def feed(self, code: int, pressed: bool, now: float) -> bool:
        if code not in self.codes:
            return False
        if pressed:
            self.held.add(code)
        else:
            self.held.discard(code)
        if self.held == self.codes:
            if self.armed and self.deadline is None:
                self.deadline = now + self.hold_s
        else:
            self.deadline = None
            if not self.held:
                self.armed = True
        return self.tick(now)

    def tick(self, now: float) -> bool:
        if self.deadline is not None and now >= self.deadline:
            self.deadline = None
            self.armed = False
            return True
        return False

    def reset(self) -> None:
        self.held.clear()
        self.deadline = None
        self.armed = True


@dataclass
class PadInfo:
    path: str
    name: str
    uniq: str
    phys: str
    vendor: int
    product: int
    bustype: int
    key_codes: set[int] = field(default_factory=set)

    def describe(self) -> dict:
        bus = {li.BUS_USB: "usb", li.BUS_BLUETOOTH: "bluetooth"}.get(self.bustype, str(self.bustype))
        return {
            "path": self.path, "name": self.name, "uniq": self.uniq,
            "vendor": f"{self.vendor:04x}", "product": f"{self.product:04x}", "bus": bus,
        }


def inspect(path: str) -> PadInfo | None:
    try:
        fd = li.open_event_device(path)
    except OSError:
        return None
    try:
        keys = li.read_bits(fd, li.EV_KEY, li.KEY_CNT)
        abses = li.read_bits(fd, li.EV_ABS, li.ABS_CNT)
        if not (li.bit_is_set(keys, BTN_SOUTH) and li.bit_is_set(abses, ABS_X) and li.bit_is_set(abses, ABS_Y)):
            return None
        ids = li.read_id(fd)
        return PadInfo(
            path=path, name=li.read_name(fd), uniq=li.read_uniq(fd), phys=li.read_phys(fd),
            vendor=ids["vendor"], product=ids["product"], bustype=ids["bustype"],
            key_codes={c for c in BTN.values() if li.bit_is_set(keys, c)},
        )
    except OSError:
        return None
    finally:
        os.close(fd)


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


class Daemon:
    def __init__(self, chord: list[str], hold_ms: int, ignore_vendor: list[str], rescan_s: float = 2.0):
        self.codes = chord_codes(chord)
        self.hold_s = hold_ms / 1000.0
        self.ignore = {int(v, 16) for v in ignore_vendor if v}
        self.rescan_s = rescan_s
        self.open: dict[str, tuple[int, PadInfo, ChordEngine]] = {}
        self.seen: dict[str, tuple] = {}  # path -> stat stamp, for non-pads too
        self.running = True

    @staticmethod
    def _stamp(path: str):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_ino, st.st_rdev, st.st_mtime_ns)

    def scan(self) -> None:
        live = set(li.event_device_paths())
        for path in list(self.open):
            if path not in live:
                self._drop(path)
        for path in list(self.seen):
            if path not in live:
                del self.seen[path]
        for path in sorted(live):
            stamp = self._stamp(path)
            if stamp is None or self.seen.get(path) == stamp:
                continue
            self.seen[path] = stamp
            if path in self.open:
                continue
            info = inspect(path)
            if not info or info.vendor in self.ignore:
                continue
            missing = [c for c in self.codes if c not in info.key_codes]
            try:
                fd = li.open_event_device(path)
            except OSError:
                continue
            self.open[path] = (fd, info, ChordEngine(self.codes, self.hold_s))
            emit({"event": "connected", **info.describe(), "chordSupported": not missing})

    def _drop(self, path: str) -> None:
        fd, info, _ = self.open.pop(path)
        try:
            os.close(fd)
        except OSError:
            pass
        self.seen.pop(path, None)
        emit({"event": "disconnected", **info.describe()})

    def _read(self, path: str) -> None:
        fd, info, engine = self.open[path]
        while True:
            try:
                data = os.read(fd, li.EVENT_SIZE * 64)
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno in (errno.ENODEV, errno.EBADF, errno.EIO):
                    self._drop(path)
                    return
                if exc.errno == errno.EAGAIN:
                    return
                raise
            if not data:
                self._drop(path)
                return
            now = time.monotonic()
            for off in range(0, len(data) - li.EVENT_SIZE + 1, li.EVENT_SIZE):
                _s, _us, ev_type, code, value = li.unpack_event(data, off)
                if ev_type == li.EV_KEY and value in (0, 1):
                    if engine.feed(code, value == 1, now):
                        emit({"event": "chord", **info.describe()})
            if len(data) < li.EVENT_SIZE * 64:
                return

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        emit({"event": "ready", "chord": sorted(self.codes), "holdMs": int(self.hold_s * 1000)})
        next_scan = 0.0
        while self.running:
            now = time.monotonic()
            if now >= next_scan:
                self.scan()
                next_scan = now + self.rescan_s
            timeout = next_scan - now
            for fd, info, engine in list(self.open.values()):
                if engine.deadline is not None:
                    timeout = min(timeout, max(0.0, engine.deadline - now))
            fds = {fd: path for path, (fd, _, _) in self.open.items()}
            try:
                ready, _, _ = select.select(list(fds), [], [], max(0.0, timeout))
            except InterruptedError:
                continue
            for fd in ready:
                if fds[fd] in self.open:
                    self._read(fds[fd])
            now = time.monotonic()
            for path, (fd, info, engine) in list(self.open.items()):
                if engine.tick(now):
                    emit({"event": "chord", **info.describe()})
        for path in list(self.open):
            fd, _, _ = self.open.pop(path)
            os.close(fd)
