# Battlestation

A gaming HUD for the [Omarchy](https://omarchy.org) 4 bar, plus **scenes**: named
display setups with actions attached. Hold a controller chord on the couch and
the TV wakes, the layout applies (with a safety Keep/Revert), and Steam Big
Picture opens fullscreen. Close Big Picture and it offers to put things back.

```
󰊗  4K·60  HDR  │  󰢮 62° 9.1G  │  󰊴 󰂀  │  144 fps
```

- **Display:** resolution and refresh rate, whether HDR is live or armed (auto), whether VRR is active or ready, and 8/10-bit output.
  The HDMI link budget is read from the EDID, so it tells you *why* HDR runs at 8-bit on an HDMI 2.0 link.
- **GPU (NVIDIA):** temperature, VRAM, load, power, clocks, throttle reasons and driver version, all from one streaming `nvidia-smi` process.
- **Controller:** whether an Xbox pad is connected, and its battery.
  xpadneo only reports a battery level, so the pill shows level icons rather than an invented percentage.
- **Game:** detects running Steam games, shows live FPS from MangoHud's log, and tracks session time.
- **Scenes:** switch from the panel, a keybind, IPC, the pill (middle-click), or a held controller chord (View + Menu by default).

## Install

```bash
omarchy plugin add https://github.com/SamuelJenkinsML/omarchy-battlestation --enable
# Put the helper on your PATH (Omarchy never runs plugin install hooks, so this is manual):
ln -s ~/.config/omarchy/plugins/io.github.samueljenkinsml.battlestation/bin/battlestation ~/.local/bin/battlestation
battlestation init
```

`init` writes `~/.config/battlestation/config.toml` from the displays you have
connected right now. It creates a desktop scene plus a gaming scene that opens
Big Picture, and the controller chord toggles between the two.

Optional extras:

| Want | Do |
| --- | --- |
| Live FPS | `sudo pacman -S mangohud lib32-mangohud`, then `battlestation setup mangohud` and restart Steam |
| Xbox pad over Bluetooth | `omarchy-install-gaming-xbox-controllers` (xpadneo). This blacklists `xpad`, so wired pads stop working |
| Xbox Wireless dongle | AUR `xone-dkms` + `xone-dongle-firmware` |
| Wake the TV / switch input | add a `[tv]` section, run `battlestation tv pair` and accept the prompt on the TV |
| Keybind | `o.bind("SUPER + CTRL + G", "Toggle gaming scene", "omarchy-shell io.github.samueljenkinsml.battlestation toggleScene")` in `~/.config/hypr/bindings.lua` |

Then run `battlestation doctor`. It checks every piece and tells you what's missing and how to fix it.

## Using it

| Where | Action |
| --- | --- |
| Pill: left-click | Open the panel: scenes, Keep/Revert, and display, GPU, controller and game cards |
| Pill: middle-click | Toggle between the two `chord_scenes` |
| Pill: right-click | Switch between the compact and full views |
| Controller | Hold **View + Menu** for 1 s to toggle scenes. During a Keep/Revert countdown it confirms Keep instead |
| Panel keys | Arrows pick a scene, Enter applies it, Esc closes, Tab moves to the next panel |

IPC (`omarchy-shell io.github.samueljenkinsml.battlestation <method>`):
`state`, `scene <name>`, `toggleScene`, `cycleScene`, `keep`, `revert`,
`tvWake`, `capture`, `refresh`. The panel also responds to
`omarchy-shell shell toggle io.github.samueljenkinsml.battlestation`.

## Configuration

`~/.config/battlestation/config.toml`. Saving the file reloads it.

```toml
[general]
default_scene = "tv-desktop"
chord_scenes = ["tv-desktop", "tv-gaming"]  # what the controller chord toggles
keep_seconds = 15                            # Keep/Revert window after a layout change
auto_scene_on_hotplug = false                # apply the best-matching scene when monitors change

[controller]
chord = ["BTN_SELECT", "BTN_START"]  # names: BTN_* or A B X Y LB RB VIEW MENU GUIDE LS RS
hold_ms = 1000
ignore_vendor = ["28de"]             # Steam's virtual gamepad

[tv]                                  # optional: Samsung Tizen TVs
host = "192.168.1.50"
mac = "aa:bb:cc:dd:ee:ff"
input_key = "KEY_HDMI1"
input_fallback = ["KEY_SOURCE", "KEY_RIGHT", "KEY_ENTER"]

[scene.tv-gaming]
label = "Gaming"
icon = "󰊗"
wake_tv = true                  # Wake-on-LAN, then wait for the display to appear
tv_input = false                # send input_key after waking
steam = "bigpicture"            # none | bigpicture
on_bigpicture_exit = "ask"      # ask | return | stay
on_enter = ["omarchy-toggle-idle stay-awake"]
on_exit = ["omarchy-toggle-idle allow-idle"]
disable_unlisted = false        # turn off connected monitors this scene doesn't list
monitors = [
  { match = "desc:Samsung Electric Company SAMSUNG 0x01000E00", mode = "3840x2160@60", fallback_modes = [], position = "0x0", scale = 1, vrr = 0, hdr = "auto", bitdepth = "auto" },
]
```

Monitor fields:

| Field | Values |
| --- | --- |
| `match` | `desc:<description>` (follows the monitor across ports) or a connector name such as `HDMI-A-1` |
| `mode` | `WIDTHxHEIGHT@HZ`, `preferred`, `highres` or `highrr`. Checked against what the display offers; `fallback_modes` are tried in order |
| `vrr` | 0 off, 1 always, 2 fullscreen, 3 fullscreen games and video |
| `hdr` | `off`; `auto` (desktop stays SDR and Hyprland switches to HDR for fullscreen HDR content); or `always` |
| `bitdepth` | `auto` (the highest the link can carry at that mode), `8` or `10` |

`battlestation scene capture <name>` adds the current layout as a new scene
(the panel's save button does the same). For a two-screen desk-and-couch
setup, start from [`examples/desk-couch.toml`](examples/desk-couch.toml).

## Streaming host

Stream this machine's games to a Steam Deck (or anything running
[Moonlight](https://moonlight-stream.org)) with the TV off, unplugged or in
the wrong mode. [Sunshine](https://github.com/LizardByte/Sunshine) captures a
virtual display that Battlestation keeps in Hyprland, so nothing depends on a
real screen.

```bash
battlestation setup stream                  # prints the one-time root steps; runs none of them
battlestation setup stream --write-config   # merges four keys into ~/.config/sunshine/sunshine.conf
```

Add a `[stream]` section to the config (an empty one is enough), set a login
at <https://localhost:47990>, then flip **Stream to Moonlight** in the panel
and pair Moonlight with the PIN it shows.

| State | What is true |
| --- | --- |
| **Off** | No virtual display, Sunshine stopped, no ports listening. The default. |
| **Ready** | Virtual display parked off to the side on workspace 99; Sunshine running. |
| **Streaming** | A client connected: the virtual display takes the client's resolution, the real displays switch off and your workspaces move over. They move back when it leaves. |

```toml
[stream]
default_mode = "1280x800@60"   # when the client does not say (Steam Deck native)
# max_width = 3840, max_height = 2160, max_fps = 120   # clamp what a client may ask for
# watchdog_idle_s = 45         # give the displays back this long after a client vanishes
# idle_inhibit = true          # stay awake while streaming
# on_attach = [], on_detach = []
```

`battlestation stream on|off|status` mirror the panel switch. If a stream ever
leaves the displays off, `battlestation stream detach` gives them back; bind it:

```lua
o.bind("SUPER + CTRL + SHIFT + G", "Give the desktop back", "omarchy-shell io.github.samueljenkinsml.battlestation streamDetach")
```

Things worth knowing:

- Needs Sunshine **2026.914.233613 or newer** (earlier builds cannot capture a
  Hyprland virtual output). Omarchy's own package may be older; `setup stream`
  points at LizardByte's pacman repo.
- Do not `systemctl --user enable` Sunshine. Battlestation starts it after the
  virtual display exists; started earlier it may capture the wrong screen.
- SDR only. Sunshine's wlroots capture carries no HDR metadata yet.
- The watchdog counts NVENC sessions to notice a client that vanished without
  Sunshine running its `undo`. Another NVENC user (a recording) delays that.
- For streaming away from home, use Tailscale and check `tailscale ping` says
  *direct*; never forward Sunshine's ports, 47990 is its admin page.
- An encrypted root cannot be unlocked remotely. Leave the host running, and
  take Suspend out of the menu with `omarchy-toggle-suspend`.

## How it works

- **The only Hyprland file it writes** is
  `~/.local/state/omarchy/toggles/hypr/battlestation-scene.lua`. Omarchy loads
  that directory after `~/.config/hypr/monitors.lua`, so your own config is never
  edited. Delete the file, or run `battlestation scene clear`, to fall back to
  `monitors.lua`.
- **Applying a layout:** it writes the file, runs `hyprctl reload`, checks
  `hyprctl configerrors`, and rolls back if Hyprland complains. The Keep/Revert
  deadline is saved to disk and a service timer enforces it, so a layout you
  can't see still reverts even if the panel closes or the shell restarts.
  Scenes that share a layout skip the reload entirely.
- **Controller:** the reader is a small Python process with no dependencies. It
  reads `/dev/input` passively and **never grabs the device**, so games and
  Steam Input are unaffected. No extra groups are needed; logind's uaccess ACL
  is enough.
- **FPS** comes from MangoHud's CSV log (in `$XDG_RUNTIME_DIR`, pruned daily),
  because MangoHud has no live FPS socket. `setup mangohud` installs a
  `steam.desktop` override that starts Steam with `MANGOHUD=1` and a small
  FPS-only HUD. (`no_display` would also stop the logging.)
- **Samsung TV:** Wake-on-LAN plus the Tizen websocket API on port 8002,
  implemented with the standard library. No cloud and no pip packages.

## Privileges and files

The plugin needs no root access, installs no packages or services, and
touches only these paths. With streaming switched on it also starts and stops
Sunshine's own user unit (`systemctl --user`), which it neither ships nor enables.

| Path | Purpose |
| --- | --- |
| `~/.config/battlestation/config.toml`, `tv-token` (0600), `mangohud.conf` | your config |
| `~/.local/state/battlestation/` | active scene, Keep/Revert stash |
| `~/.local/state/omarchy/toggles/hypr/battlestation-scene.lua` | the generated scene |
| `~/.local/state/omarchy/toggles/hypr/battlestation-stream.lua` | the streaming overlay; only while streaming is switched on |
| `~/.local/state/battlestation/stream-enabled` | the streaming switch |
| `~/.config/sunshine/sunshine.conf` | only with `setup stream --write-config`: `capture`, `encoder`, `output_name` and one `global_prep_cmd` entry; everything else in it is kept |
| `~/.local/share/applications/steam.desktop` | only after `setup mangohud`; marked, and removed by `setup mangohud --remove` |
| `$XDG_RUNTIME_DIR/battlestation/` | MangoHud logs, streaming state (tmpfs) |

## Remove

```bash
battlestation stream off             # stop Sunshine, remove the virtual display and its overlay
battlestation scene clear            # drop the scene overlay
battlestation setup mangohud --remove
omarchy plugin remove io.github.samueljenkinsml.battlestation
rm -rf ~/.config/battlestation ~/.local/state/battlestation
```

## Development

```bash
make test      # unit tests (stdlib unittest)
make lint      # qmllint + py_compile
make validate  # omarchy-plugin-validate
make dev       # copy into ~/.config/omarchy/plugins/<id>/ (the shell hot-reloads)
make logs      # plugin lines from the shell log
```

## TODO

Still open on the feature:

- Tailscale for streaming away from home.
- Three untested disconnect paths: pause and resume, the Deck powering off mid-stream, and connecting while the desktop is locked.
- Taking Suspend out of the system menu.
- HDR, once the upstream Sunshine change lands.

## Credits

Built from the best ideas in other Omarchy plugins; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). MIT licensed.
