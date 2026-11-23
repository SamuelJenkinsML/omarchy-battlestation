---
name: debug-display-scenes
description: >-
  Diagnose and fix display, EDID, HDR, VRR, HDMI 2.1, hotplug, and scene-switching
  issues in omarchy-battlestation. Use when a monitor is stuck at the wrong
  refresh rate/resolution, HDR or VRR is not engaging, a scene fails to switch
  between displays (e.g. TV and ultrawide), or Hyprland rejects a generated overlay.
---

# Debugging Display & Scene Issues

Use this runbook to root-cause display and scene problems against live Hyprland,
DRM/EDID, and Battlestation state before editing code.

## 1. Collect Live State

Because shell/terminal sessions can inherit a stale `HYPRLAND_INSTANCE_SIGNATURE`,
discover the active compositor socket first (note `bin/battlestation` already
does this via `hypr.discover_instance()`):

```bash
ls -la "$XDG_RUNTIME_DIR/hypr/"
./bin/battlestation doctor
./bin/battlestation state
./bin/battlestation caps
```

Inspect the generated Hyprland overlays and saved scene state:

```bash
cat ~/.config/battlestation/config.toml
cat ~/.local/state/battlestation/state.json
cat ~/.local/state/omarchy/toggles/hypr/battlestation-scene.lua
cat ~/.local/state/omarchy/toggles/hypr/battlestation-stream.lua 2>/dev/null || true
cat ~/.config/hypr/monitors.lua
```

Compare what the active scene **would** render right now against what is on disk:

```bash
./bin/battlestation scene render <active-scene>
```

If `render` outputs a better mode/bitdepth/HDR than `battlestation-scene.lua`,
the overlay is stale (commonly caused by transient EDID/mode renegotiation on
HDMI 2.1 TV wake).

## 2. Inspect Live Hyprland, DRM, and Kernel Logs

Check live monitors, available modes, DRM connector properties, and EDID:

```bash
python3 -c 'import json, sys; sys.path.insert(0, "lib"); from battlestation import drmprops, edid, hypr; mons = hypr.monitors(); print(json.dumps(mons, indent=2)); print("DRM:", drmprops.read()); print({m["name"]: edid.read_caps(m["name"]).to_dict() for m in mons if m["name"] != "BS-STREAM"})'
```

Inspect Aquamarine/DRM hotplug and mode dumps in the active `hyprland.log` and
kernel EDID warnings:

```bash
SIG=$(python3 -c 'import sys; sys.path.insert(0, "lib"); from battlestation import hypr; print(hypr.instance_signature() or "")')
grep -iE "Connector |Dumping detected modes|Mode [0-9]+:|Modesetting|disabled|vrr" "$XDG_RUNTIME_DIR/hypr/$SIG/hyprland.log" | tail -100
journalctl -k --since "1 hour ago" | grep -iE "edid|drm|nvidia-modeset|hdmi" | tail -40
```

Check whether the installed plugin matches the repo working tree:

```bash
diff -ru --exclude=.git --exclude=.agents --exclude=AGENTS.md --exclude=tests --exclude=__pycache__ --exclude='*.pyc' . ~/.config/omarchy/plugins/io.github.samueljenkinsml.battlestation/
```

## 3. Common Root Causes

- **TV stuck at 4K@60 instead of 4K@120**: Samsung/HDMI 2.1 TVs often connect
  briefly with a 60 Hz-only mode list before dropping HPD and reconnecting with
  48 Gbps FRL + 120/144 Hz modes. Check `state.json` `notes` for a fallback
  message, and verify `scenes.ensure()` + `DisplayState.qml` hotplug/mode-list
  tracking + `Service.qml` `pendingTopologyCheck`.
- **HDR running at 8-bit on HDMI 2.1**: Inspect `edid.read_caps(connector)`.
  Check `max_frl_gbps` (HF-VSDB byte offsets in `lib/battlestation/edid.py`).
  If `max_frl_gbps == 0` on an HDMI 2.1 TV, verify the TV's enhanced HDMI
  setting (e.g. Samsung *Input Signal Plus*) and capture the raw EDID from
  `/sys/class/drm/card*-<connector>/edid` into `tests/fixtures/` for a unit test.
- **Transient `Unable to read EDID` from `nvidia-modeset`**: Ensure hotplug
  reconciliation does not strip HDR/10-bit when `edid.read_caps()` returns an
  empty `EdidCaps()`.
- **Scene not switching between TV and ultrawide**: Check `desc:` strings in
  `config.toml` against `hyprctl monitors all -j` `description` fields, and
  check whether `stream.attached` or `pendingRevert` gated `Service.qml`.

## 4. Verify the Fix

1. Add a unit test (and fixture if EDID/Hyprland payload changed) in
   `tests/test_core.py`.
2. Run `make check` (`test` + `lint` + `validate`).
3. Deploy with `make dev`, trigger `./bin/battlestation scene ensure`, and
   confirm `hyprctl monitors all -j` and `./bin/battlestation state` show the
   expected resolution, refresh rate, `XBGR2101010` (10-bit), HDR preset, and
   zero fallback notes.
