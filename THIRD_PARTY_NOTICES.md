# Third-party notices

Battlestation adapts code and designs from these MIT-licensed Omarchy plugins.
Each is used under the MIT License (text below); the copyright lines are theirs.

| Project | Copyright | What Battlestation uses | Where |
|---|---|---|---|
| [edbron/omarchy-monitor-placement-refresh-rate](https://github.com/edbron/omarchy-monitor-placement-refresh-rate) | (c) 2026 edbron | DRM ioctl reader for `vrr_capable` / `VRR_ENABLED` (adapted code) | `lib/battlestation/drmprops.py` |
| [perminder-klair/omarchy-controller-control](https://github.com/perminder-klair/omarchy-controller-control) | (c) 2026 Parminder Klair | Pure-stdlib evdev bindings (adapted code); node cache and select-with-deadline loop (design) | `lib/battlestation/evdev.py`, `lib/battlestation/pad.py` |
| [IM0001GT/omarchy-screens](https://github.com/IM0001GT/omarchy-screens) | (c) 2026 IM0001GT | EDID CTA walk and HDR Lua fields (adapted, with byte-order fixes); apply → reload → configerrors → rollback; persisted Keep/Revert deadline | `lib/battlestation/edid.py`, `lib/battlestation/luagen.py`, `lib/battlestation/scenes.py` |
| [candycrabmusic/gpu-monitor](https://github.com/candycrabmusic/gpu-monitor) | (c) 2026 vichu | nvidia-smi field set, throttle decoding, right-click view persistence (design) | `qml/GpuSampler.qml`, `BarWidget.qml` |
| [DanSmith888/omarchy-gpu](https://github.com/DanSmith888/omarchy-gpu) | (c) 2026 Daniel Smith | Hide-when-absent GPU segment (design) | `qml/GpuSampler.qml` |
| [atoslins/omarchy-plugin-dualsense](https://github.com/atoslins/omarchy-plugin-dualsense) | (c) 2026 Atos Lins | Controller glyph, dimming and charging treatment (design) | `qml/ControllerState.qml`, `BarWidget.qml` |
| [perfektnacht/controller-launcher](https://github.com/perfektnacht/controller-launcher) | (c) 2026 perfektnacht | Gamepad detection rule `BTN_SOUTH` + `ABS_X/Y` (design) | `lib/battlestation/pad.py` |
| [nathanp/omarchy-game-awake](https://github.com/nathanp/omarchy-game-awake) | (c) 2026 Nathan Parikh | Hyprland raw-event parsing and startup rescan for game windows (design) | `qml/GameWatcher.qml` |
| [silvaio/gamemode-switcher](https://github.com/silvaio/gamemode-switcher) | (c) 2026 silvaio | Steam Big Picture launch split, title pattern, Lua fullscreen dispatch (design) | `lib/battlestation/steam.py`, `Service.qml` |
| [Mario-Mohar/omarchy-monitor-profiles](https://github.com/Mario-Mohar/omarchy-monitor-profiles) | (c) 2026 themo | "Most specific connected profile wins" auto selection (design) | `lib/battlestation/scenes.py` |
| [PedroSilvaAlves/omarchy-monitor-handoff](https://github.com/PedroSilvaAlves/omarchy-monitor-handoff) | (c) 2026 Pedro Alves | Re-enabling outputs needs a reload, never disable the last display (design) | `lib/battlestation/scenes.py` |

The Samsung TV remote protocol (port 8002 websocket, token pairing, key
payload) is documented by [xchwarze/samsung-tv-ws-api](https://github.com/xchwarze/samsung-tv-ws-api)
(LGPL-3.0). Battlestation does not include or copy that code;
`lib/battlestation/tv.py` and `lib/battlestation/ws.py` are an independent
standard-library implementation.

## MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
