"""Minimal pure-stdlib bindings for the Linux evdev ABI (read side only).

Adapted from perminder-klair/omarchy-controller-control engine/linux_input.py
(MIT, (c) 2026 Parminder Klair); the uinput half is dropped because Battlestation
only ever reads a controller passively and never grabs or injects.
"""

from __future__ import annotations

import ctypes
import fcntl
import glob
import os
import struct

# --- ioctl request encoding (asm-generic/ioctl.h) ---------------------------

_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _ioc(direction: int, letter: str, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(letter) << 8) | number


# --- event types ------------------------------------------------------------

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
EV_MSC = 0x04

SYN_REPORT = 0

# --- button/axis codes we care about ---------------------------------------

BTN_MISC = 0x100
BTN_JOYSTICK = 0x120
BTN_GAMEPAD = 0x130
BTN_THUMBR = 0x13E
BTN_TRIGGER_HAPPY = 0x2C0
BTN_TRIGGER_HAPPY40 = 0x2E7

ABS_X = 0x00
ABS_Z = 0x02
ABS_RZ = 0x05
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11
ABS_MAX = 0x3F

KEY_MAX = 0x2FF
ABS_CNT = ABS_MAX + 1
KEY_CNT = KEY_MAX + 1

# --- evdev ioctls -----------------------------------------------------------

EVIOCGVERSION = _ioc(_IOC_READ, "E", 0x01, 4)
EVIOCGID = _ioc(_IOC_READ, "E", 0x02, 8)

def EVIOCGNAME(length: int) -> int:
    return _ioc(_IOC_READ, "E", 0x06, length)


def EVIOCGPHYS(length: int) -> int:
    return _ioc(_IOC_READ, "E", 0x07, length)


def EVIOCGUNIQ(length: int) -> int:
    return _ioc(_IOC_READ, "E", 0x08, length)


def EVIOCGBIT(ev_type: int, length: int) -> int:
    return _ioc(_IOC_READ, "E", 0x20 + ev_type, length)


def EVIOCGABS(axis: int) -> int:
    return _ioc(_IOC_READ, "E", 0x40 + axis, struct.calcsize("iiiiii"))


BUS_USB = 0x03
BUS_BLUETOOTH = 0x05

# --- struct input_event -----------------------------------------------------

# struct timeval is two C longs, followed by __u16 type, __u16 code, __s32 value.
_EVENT_FMT = "@llHHi"
EVENT_SIZE = struct.calcsize(_EVENT_FMT)
_event_struct = struct.Struct(_EVENT_FMT)

unpack_event = _event_struct.unpack_from

# --- helpers ----------------------------------------------------------------


def bit_is_set(buf: bytes, bit: int) -> bool:
    index = bit // 8
    return index < len(buf) and bool(buf[index] & (1 << (bit % 8)))


def _ioctl_string(fd: int, request_for_length, length: int = 256) -> str:
    buf = ctypes.create_string_buffer(length)
    try:
        fcntl.ioctl(fd, request_for_length(length), buf)
    except OSError:
        return ""
    return buf.value.decode("utf-8", "replace")


def read_name(fd: int) -> str:
    return _ioctl_string(fd, EVIOCGNAME)


def read_phys(fd: int) -> str:
    return _ioctl_string(fd, EVIOCGPHYS, 128)


def read_uniq(fd: int) -> str:
    return _ioctl_string(fd, EVIOCGUNIQ, 128)


def read_id(fd: int) -> dict:
    buf = bytearray(8)
    try:
        fcntl.ioctl(fd, EVIOCGID, buf)
    except OSError:
        return {"bustype": 0, "vendor": 0, "product": 0, "version": 0}
    bustype, vendor, product, version = struct.unpack("HHHH", buf)
    return {
        "bustype": bustype,
        "vendor": vendor,
        "product": product,
        "version": version,
    }


def read_bits(fd: int, ev_type: int, count: int) -> bytes:
    buf = bytearray((count + 7) // 8)
    try:
        fcntl.ioctl(fd, EVIOCGBIT(ev_type, len(buf)), buf)
    except OSError:
        return b""
    return bytes(buf)


def read_abs_info(fd: int, axis: int) -> dict | None:
    buf = bytearray(struct.calcsize("iiiiii"))
    try:
        fcntl.ioctl(fd, EVIOCGABS(axis), buf)
    except OSError:
        return None
    value, minimum, maximum, fuzz, flat, resolution = struct.unpack("iiiiii", buf)
    return {
        "value": value,
        "min": minimum,
        "max": maximum,
        "fuzz": fuzz,
        "flat": flat,
        "resolution": resolution,
    }




def event_device_paths() -> list[str]:
    paths = glob.glob("/dev/input/event*")
    paths.sort(key=lambda p: int(p.rsplit("event", 1)[-1] or 0))
    return paths


def open_event_device(path: str) -> int:
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK)
