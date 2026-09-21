"""Config loading, validation and (minimal) TOML writing.

The config lives in ~/.config/battlestation/config.toml. Scenes are data: a
list of monitor rules plus the actions that go with them. Nothing about a
particular desk is hard-coded.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

HDR_MODES = ("off", "auto", "always")
STEAM_MODES = ("none", "bigpicture")
EXIT_MODES = ("ask", "return", "stay")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_POS_RE = re.compile(r"^(auto|-?\d+x-?\d+)$")
_MODE_RE = re.compile(r"^(preferred|highres|highrr|\d+x\d+(@[\d.]+)?)$")
_MAC_RE = re.compile(r"^[0-9a-f]{2}([:-][0-9a-f]{2}){5}$", re.I)
_OUTPUT_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")  # same alphabet as hypr.CONNECTOR_RE


class ConfigError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class MonitorRule:
    match: str
    mode: str = "preferred"
    fallback_modes: list[str] = field(default_factory=list)
    position: str = "auto"
    scale: float = 1.0
    transform: int = 0
    vrr: int = 0
    hdr: str = "off"
    bitdepth: str = "auto"  # "auto" | "8" | "10"
    sdr_brightness: float = 1.0


@dataclass
class Scene:
    name: str
    label: str
    icon: str = "󰍹"
    monitors: list[MonitorRule] = field(default_factory=list)
    disable_unlisted: bool = False
    wake_tv: bool = False
    tv_input: bool = False
    steam: str = "none"
    on_bigpicture_exit: str = "ask"
    on_enter: list[str] = field(default_factory=list)
    on_exit: list[str] = field(default_factory=list)


@dataclass
class TvConfig:
    host: str = ""
    mac: str = ""
    broadcast: str = "255.255.255.255"
    input_key: str = ""
    input_fallback: list[str] = field(default_factory=list)
    wake_timeout_s: int = 25

    @property
    def configured(self) -> bool:
        return bool(self.host)


SUNSHINE_UNIT = "app-dev.lizardbyte.app.Sunshine.service"


@dataclass
class StreamConfig:
    """Headless streaming host. Having a [stream] section makes the feature
    available; whether it is switched on lives in paths.stream_flag()."""

    configured: bool = False
    output: str = "BS-STREAM"
    default_mode: str = "1920x1080@60"  # used when the client does not say
    max_width: int = 3840
    max_height: int = 2160
    max_fps: int = 120
    scale: float = 1.0
    unit: str = SUNSHINE_UNIT
    manage_unit: bool = True
    idle_inhibit: bool = True
    watchdog_idle_s: int = 45
    on_attach: list[str] = field(default_factory=list)
    on_detach: list[str] = field(default_factory=list)


@dataclass
class ControllerConfig:
    chord: list[str] = field(default_factory=lambda: ["BTN_SELECT", "BTN_START"])
    hold_ms: int = 1000
    ignore_vendor: list[str] = field(default_factory=lambda: ["28de"])


@dataclass
class Config:
    default_scene: str = ""
    chord_scenes: list[str] = field(default_factory=list)
    keep_seconds: int = 15
    auto_scene_on_hotplug: bool = False
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    tv: TvConfig = field(default_factory=TvConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    scenes: dict[str, Scene] = field(default_factory=dict)

    def scene(self, name: str) -> Scene:
        if name not in self.scenes:
            raise ConfigError([f"unknown scene '{name}' (have: {', '.join(self.scenes) or 'none'})"])
        return self.scenes[name]


# -- parsing -----------------------------------------------------------------


def _str_list(value, where: str, problems: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        problems.append(f"{where} must be a list of strings")
        return []
    return list(value)


def _monitor(raw, where: str, problems: list[str]) -> MonitorRule | None:
    if not isinstance(raw, dict):
        problems.append(f"{where} must be a table")
        return None
    match = str(raw.get("match", "")).strip()
    if not match:
        problems.append(f"{where}.match is required (a connector like HDMI-A-1 or desc:<description>)")
        return None
    rule = MonitorRule(match=match)
    rule.mode = str(raw.get("mode", rule.mode))
    if not _MODE_RE.match(rule.mode):
        problems.append(f"{where}.mode '{rule.mode}' is not WIDTHxHEIGHT@HZ, preferred, highres or highrr")
    rule.fallback_modes = _str_list(raw.get("fallback_modes"), f"{where}.fallback_modes", problems)
    rule.position = str(raw.get("position", rule.position))
    if not _POS_RE.match(rule.position):
        problems.append(f"{where}.position '{rule.position}' must be auto or XxY")
    try:
        rule.scale = float(raw.get("scale", rule.scale))
        if not 0.25 <= rule.scale <= 4:
            raise ValueError
    except (TypeError, ValueError):
        problems.append(f"{where}.scale must be a number between 0.25 and 4")
    rule.transform = int(raw.get("transform", 0)) if str(raw.get("transform", 0)).isdigit() else -1
    if not 0 <= rule.transform <= 7:
        problems.append(f"{where}.transform must be 0-7")
    try:
        rule.vrr = int(raw.get("vrr", 0))
        if rule.vrr not in (0, 1, 2, 3):
            raise ValueError
    except (TypeError, ValueError):
        problems.append(f"{where}.vrr must be 0 (off), 1 (always), 2 (fullscreen) or 3 (fullscreen games/video)")
    rule.hdr = str(raw.get("hdr", "off")).lower()
    if rule.hdr not in HDR_MODES:
        problems.append(f"{where}.hdr must be one of {', '.join(HDR_MODES)}")
    rule.bitdepth = str(raw.get("bitdepth", "auto")).lower()
    if rule.bitdepth not in ("auto", "8", "10"):
        problems.append(f"{where}.bitdepth must be auto, 8 or 10")
    try:
        rule.sdr_brightness = float(raw.get("sdr_brightness", 1.0))
    except (TypeError, ValueError):
        problems.append(f"{where}.sdr_brightness must be a number")
    return rule


def _scene(name: str, raw, problems: list[str]) -> Scene | None:
    where = f"scene.{name}"
    if not _NAME_RE.match(name):
        problems.append(f"{where}: scene names must be lowercase letters, digits, '-' or '_'")
    if not isinstance(raw, dict):
        problems.append(f"{where} must be a table")
        return None
    scene = Scene(name=name, label=str(raw.get("label") or name.replace("-", " ").title()))
    scene.icon = str(raw.get("icon", scene.icon))
    scene.disable_unlisted = bool(raw.get("disable_unlisted", False))
    scene.wake_tv = bool(raw.get("wake_tv", False))
    scene.tv_input = bool(raw.get("tv_input", False))
    scene.steam = str(raw.get("steam", "none"))
    if scene.steam not in STEAM_MODES:
        problems.append(f"{where}.steam must be one of {', '.join(STEAM_MODES)}")
    scene.on_bigpicture_exit = str(raw.get("on_bigpicture_exit", "ask"))
    if scene.on_bigpicture_exit not in EXIT_MODES:
        problems.append(f"{where}.on_bigpicture_exit must be one of {', '.join(EXIT_MODES)}")
    scene.on_enter = _str_list(raw.get("on_enter"), f"{where}.on_enter", problems)
    scene.on_exit = _str_list(raw.get("on_exit"), f"{where}.on_exit", problems)
    monitors = raw.get("monitors", [])
    if not isinstance(monitors, list):
        problems.append(f"{where}.monitors must be an array of tables")
        monitors = []
    for i, m in enumerate(monitors):
        rule = _monitor(m, f"{where}.monitors[{i}]", problems)
        if rule:
            scene.monitors.append(rule)
    return scene


def _stream(raw, st: StreamConfig, problems: list[str]) -> None:
    if not isinstance(raw, dict):
        problems.append("[stream] must be a table")
        return
    st.configured = True
    st.output = str(raw.get("output", st.output)).strip()
    if not _OUTPUT_RE.match(st.output):
        problems.append("stream.output must be a plain name (letters, digits, '.', '_' or '-')")
    st.default_mode = str(raw.get("default_mode", st.default_mode))
    if not re.match(r"^\d+x\d+(@[\d.]+)?$", st.default_mode):
        problems.append(f"stream.default_mode '{st.default_mode}' is not WIDTHxHEIGHT@HZ")
    for key, low, high in (("max_width", 640, 7680), ("max_height", 480, 4320), ("max_fps", 24, 240),
                           ("watchdog_idle_s", 15, 600)):
        try:
            value = int(raw.get(key, getattr(st, key)))
            if not low <= value <= high:
                raise ValueError
            setattr(st, key, value)
        except (TypeError, ValueError):
            problems.append(f"stream.{key} must be a whole number between {low} and {high}")
    try:
        st.scale = float(raw.get("scale", st.scale))
        if not 0.25 <= st.scale <= 4:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("stream.scale must be a number between 0.25 and 4")
    st.unit = str(raw.get("unit", st.unit)).strip()
    if not re.match(r"^[A-Za-z0-9@._:-]+\.service$", st.unit):
        problems.append("stream.unit must be a systemd user unit name ending in .service")
    st.manage_unit = bool(raw.get("manage_unit", st.manage_unit))
    st.idle_inhibit = bool(raw.get("idle_inhibit", st.idle_inhibit))
    st.on_attach = _str_list(raw.get("on_attach"), "stream.on_attach", problems)
    st.on_detach = _str_list(raw.get("on_detach"), "stream.on_detach", problems)


def parse(data: dict) -> Config:
    problems: list[str] = []
    cfg = Config()

    general = data.get("general", {}) or {}
    cfg.keep_seconds = int(general.get("keep_seconds", cfg.keep_seconds))
    if not 5 <= cfg.keep_seconds <= 120:
        problems.append("general.keep_seconds must be between 5 and 120")
    cfg.auto_scene_on_hotplug = bool(general.get("auto_scene_on_hotplug", False))

    ctl = data.get("controller", {}) or {}
    cfg.controller.chord = _str_list(ctl.get("chord", cfg.controller.chord), "controller.chord", problems) or cfg.controller.chord
    cfg.controller.hold_ms = int(ctl.get("hold_ms", cfg.controller.hold_ms))
    if not 200 <= cfg.controller.hold_ms <= 5000:
        problems.append("controller.hold_ms must be between 200 and 5000")
    cfg.controller.ignore_vendor = [v.lower() for v in _str_list(ctl.get("ignore_vendor", cfg.controller.ignore_vendor), "controller.ignore_vendor", problems)]

    tv = data.get("tv", {}) or {}
    cfg.tv.host = str(tv.get("host", "")).strip()
    cfg.tv.mac = str(tv.get("mac", "")).strip()
    if cfg.tv.mac and not _MAC_RE.match(cfg.tv.mac):
        problems.append("tv.mac must look like aa:bb:cc:dd:ee:ff")
    cfg.tv.broadcast = str(tv.get("broadcast", cfg.tv.broadcast))
    cfg.tv.input_key = str(tv.get("input_key", "")).strip()
    cfg.tv.input_fallback = _str_list(tv.get("input_fallback"), "tv.input_fallback", problems)
    cfg.tv.wake_timeout_s = int(tv.get("wake_timeout_s", cfg.tv.wake_timeout_s))

    if "stream" in data:
        _stream(data.get("stream"), cfg.stream, problems)

    scenes = data.get("scene", {}) or {}
    if not isinstance(scenes, dict):
        problems.append("[scene.<name>] tables expected")
        scenes = {}
    for name, raw in scenes.items():
        scene = _scene(name, raw, problems)
        if scene:
            cfg.scenes[name] = scene

    cfg.default_scene = str(general.get("default_scene", next(iter(cfg.scenes), "")))
    if cfg.scenes and cfg.default_scene not in cfg.scenes:
        problems.append(f"general.default_scene '{cfg.default_scene}' is not a defined scene")
    cfg.chord_scenes = _str_list(general.get("chord_scenes"), "general.chord_scenes", problems)
    if not cfg.chord_scenes:
        cfg.chord_scenes = list(cfg.scenes)[:2]
    for name in cfg.chord_scenes:
        if name not in cfg.scenes:
            problems.append(f"general.chord_scenes references unknown scene '{name}'")
    for scene in cfg.scenes.values():
        if (scene.wake_tv or scene.tv_input) and not cfg.tv.configured:
            problems.append(f"scene.{scene.name} uses the TV but [tv] host is not set")

    if problems:
        raise ConfigError(problems)
    return cfg


def load(path: Path | None = None) -> Config:
    path = path or paths.config_file()
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError([f"{path} does not exist; run `battlestation init`"])
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"{path}: {exc}"])
    return parse(data)


# -- writing -----------------------------------------------------------------


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def monitor_toml(rule: MonitorRule) -> str:
    parts = [f"match = {_toml_value(rule.match)}", f"mode = {_toml_value(rule.mode)}"]
    if rule.fallback_modes:
        parts.append(f"fallback_modes = {_toml_value(rule.fallback_modes)}")
    parts += [
        f"position = {_toml_value(rule.position)}",
        f"scale = {_toml_value(float(rule.scale))}",
    ]
    if rule.transform:
        parts.append(f"transform = {rule.transform}")
    parts += [f"vrr = {rule.vrr}", f"hdr = {_toml_value(rule.hdr)}"]
    if rule.bitdepth != "auto":
        parts.append(f"bitdepth = {_toml_value(rule.bitdepth)}")
    return "{ " + ", ".join(parts) + " }"


def scene_toml(scene: Scene) -> str:
    lines = [f"[scene.{scene.name}]", f"label = {_toml_value(scene.label)}", f"icon = {_toml_value(scene.icon)}"]
    if scene.disable_unlisted:
        lines.append("disable_unlisted = true")
    if scene.wake_tv:
        lines.append("wake_tv = true")
    if scene.tv_input:
        lines.append("tv_input = true")
    if scene.steam != "none":
        lines.append(f"steam = {_toml_value(scene.steam)}")
        lines.append(f"on_bigpicture_exit = {_toml_value(scene.on_bigpicture_exit)}")
    if scene.on_enter:
        lines.append(f"on_enter = {_toml_value(scene.on_enter)}")
    if scene.on_exit:
        lines.append(f"on_exit = {_toml_value(scene.on_exit)}")
    lines.append("monitors = [")
    lines += [f"  {monitor_toml(m)}," for m in scene.monitors]
    lines.append("]")
    return "\n".join(lines) + "\n"
