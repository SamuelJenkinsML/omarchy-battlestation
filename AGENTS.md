# Battlestation — Agent Instructions

Battlestation (`io.github.samueljenkinsml.battlestation`) is an Omarchy 4 shell
plugin (Quickshell / QML) backed by a zero-dependency Python 3 CLI helper
(`bin/battlestation`, `lib/battlestation/`).

## Architecture

- **QML shell layer** (`Service.qml`, `BarWidget.qml`, `Panel.qml`, `qml/*.qml`):
  - `Service.qml` is the single owner of state, child processes, and IPC
    (`omarchy-shell io.github.samueljenkinsml.battlestation <method>`).
  - `BarWidget.qml` is instantiated once per monitor and reads only from
    `Service.qml` (`bar.shell.serviceFor`); never duplicate polling in bar widgets.
  - `qml/DisplayState.qml` watches Hyprland events + polls `hyprctl monitors all -j`
    and triggers `scene ensure` / `scene auto` on topology or hotplug changes.
- **Python helper** (`bin/battlestation`, `lib/battlestation/*.py`):
  - Most subcommands print a single JSON object (`{"ok": true, ...}`) to stdout
    for `Service.qml`. Streaming daemons (`pad-daemon`, `fps-tail`, `live-tail`)
    emit JSON lines.
  - Key modules: `scenes.py` (resolution/apply/keep/revert/ensure), `luagen.py`
    (Hyprland Lua generation), `hypr.py` (`hyprctl` + live instance discovery),
    `edid.py` (CTA-861 / HF-VSDB HDMI 2.1 link budget), `drmprops.py` (raw KMS
    ioctls), `stream.py` (Sunshine + `BS-STREAM` virtual output), `tv.py`
    (Samsung WoL + Tizen WS).

## Non-Negotiable Invariants

1. **Python standard library only**: Do not add pip or third-party dependencies.
   Everything in `lib/battlestation/` must run on stock Arch/Omarchy Python 3.
2. **No symlinks anywhere in the repo**: `omarchy-plugin-validate` fails if any
   symlink exists outside `.git`. Never create symlinks in the repository.
3. **Never write `__pycache__` into the installed plugin directory**:
   `bin/battlestation` sets `sys.dont_write_bytecode = True` because Quickshell
   watches `~/.config/omarchy/plugins/<id>/` and hot-reloads on file changes.
4. **Never edit `~/.config/hypr/monitors.lua`**: Battlestation only writes
   generated Lua overlays atomically (`fsutil.atomic_write`) into
   `~/.local/state/omarchy/toggles/hypr/battlestation-{scene,stream}.lua`.
5. **Key monitor rules by EDID (`desc:...`)**: Always prefer `scenes.selector(mon)`
   over bare connector names (`HDMI-A-1`, `DP-1`) so rules follow the panel, not
   the port. Never include the virtual streaming output (`BS-STREAM`) in scenes.
6. **Display & input safety**:
   - Never disable every connected monitor (`scenes.resolve` raises `SceneError`).
   - `stream.detach()` must work without a valid `config.toml` (it restores real
     displays on stream exit/crash).
   - `pad.py` must read `/dev/input` passively and never `EVIOCGRAB` a device.
   - Always talk to Hyprland through `hypr.py` (which discovers the live
     compositor instance when `HYPRLAND_INSTANCE_SIGNATURE` is stale).
7. **Keep `Makefile` `dev` excludes clean**: `make dev` copies the plugin into
   `~/.config/omarchy/plugins/<id>/`; exclude dev-only files (`.git`, `.agents`,
   `AGENTS.md`, `tests`, `__pycache__`, `*.pyc`).

## Verification & Workflow

Run all checks before finishing any code change:

```bash
make check     # runs: make test, make lint, make validate
```

- **Unit tests**: `make test` (`python3 -m unittest discover -s tests`). Add
  regression tests in `tests/test_core.py` and binary/JSON fixtures in
  `tests/fixtures/` when fixing hardware, EDID, Hyprland, or scene bugs.
- **Lint**: `make lint` (`qmllint` + `py_compile`).
- **Manifest validation**: `make validate` (`omarchy-plugin-validate .`).
- **Deploy locally**: `make dev` syncs into `~/.config/omarchy/plugins/<id>/`
  (Quickshell hot-reloads automatically); inspect shell output with `make logs`.

## Available Repo Skills

Load these on demand when relevant:
- `debug-display-scenes`: RCA runbook for Hyprland monitor, EDID, HDR/VRR,
  hotplug, and scene-switching issues.
- `plugin-release-check`: End-to-end validation, local deploy (`make dev`),
  shell log check, and live IPC smoke test.
