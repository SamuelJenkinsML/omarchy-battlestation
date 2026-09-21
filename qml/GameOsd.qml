import QtQuick
import Quickshell
import Quickshell.Wayland
import Quickshell.Hyprland
import qs.Commons
import qs.Ui

// In-game overlay: FPS, HDR and VRR in a corner card above fullscreen games.
// Visual only, like Omarchy's OSD: an empty input region and no keyboard
// focus, so the game keeps every click and key. It sits on the focused monitor.
Item {
  id: root

  required property var svc
  property bool shown: false

  readonly property string screenName: Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : ""
  readonly property var display: svc ? svc.display.summaryFor(screenName) : null
  readonly property var game: svc ? svc.game : null
  readonly property bool hasFps: !!game && game.gameRunning && game.fps >= 0

  readonly property color fg: Color.popups.text
  readonly property color dim: Util.alpha(fg, 0.5)
  readonly property color accent: Color.accent
  readonly property int pad: Style.space(12)
  readonly property int labelWidth: Math.ceil(labelMetrics.advanceWidth)

  function hdrText(d) {
    if (!d) return "—"
    if (d.hdrSignalled) return "On" + (d.colorspace && d.colorspace !== "Default" ? " · " + d.colorspace.replace(/_/g, " ") : "")
    if (d.hdrLive) return "On"
    if (!d.hdrCapable) return "SDR only"
    if (d.hdrMode === "auto") return "Armed (auto)"
    return "Off"
  }

  function vrrText(d) {
    if (!d) return "—"
    if (d.vrrLive) return "Active"
    if (d.vrrMode > 0) return "Ready"
    return d.vrrCapable ? "Off" : "Not supported"
  }

  function screenFor(name) {
    var screens = Quickshell.screens
    for (var i = 0; i < screens.length; i++) if (screens[i].name === name) return screens[i]
    return null
  }

  TextMetrics {
    id: labelMetrics
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
    font.bold: true
    text: "FPS"
  }

  PanelWindow {
    id: window
    visible: root.shown && root.svc !== null
    screen: root.screenFor(root.screenName)
    anchors { top: true; left: true }
    margins { top: Style.space(16); left: Style.space(16) }
    implicitWidth: Math.ceil(card.width)
    implicitHeight: Math.ceil(card.height)
    color: "transparent"
    WlrLayershell.namespace: "battlestation-osd"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    // Normal keeps it below the bar on the desktop; in game the bar is covered anyway.
    exclusionMode: ExclusionMode.Normal
    mask: Region {}

    BorderSurface {
      id: card
      width: card.borderLeft + root.pad + rows.implicitWidth + root.pad + card.borderRight
      height: card.borderTop + root.pad + rows.implicitHeight + root.pad + card.borderBottom
      // Opaque: on a colour-managed 10-bit output, even 92% alpha lets game
      // detail bleed through enough to hurt legibility.
      color: Color.popups.background
      borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(1)))
      radius: Style.cornerRadius

      Column {
        id: rows
        x: card.borderLeft + root.pad
        y: card.borderTop + root.pad
        spacing: Style.space(6)

        // ---- FPS ----
        Row {
          spacing: Style.space(10)
          Text {
            width: root.labelWidth
            anchors.baseline: fpsValue.baseline
            text: "FPS"
            color: root.dim
            font: labelMetrics.font
          }
          Text {
            id: fpsValue
            text: root.hasFps ? String(Math.round(root.game.fps)) : "—"
            color: root.hasFps ? root.accent : root.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.displayLarge
            font.bold: true
          }
          Column {
            anchors.verticalCenter: fpsValue.verticalCenter
            spacing: Style.space(2)
            Text {
              text: {
                if (!root.game || !root.game.gameRunning) return "no game"
                if (!root.hasFps) return "no MangoHud data"
                return root.game.frametime > 0 ? root.game.frametime.toFixed(1) + " ms" : ""
              }
              color: root.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            Sparkline {
              visible: root.hasFps
              width: Style.space(72)
              height: Style.space(16)
              values: root.game ? root.game.fpsHistory : []
              color: root.accent
            }
          }
        }

        // ---- HDR ----
        Row {
          spacing: Style.space(10)
          Text {
            width: root.labelWidth
            text: "HDR"
            color: root.dim
            font: labelMetrics.font
          }
          Text {
            text: root.hdrText(root.display)
            color: root.display && root.display.hdrLive ? root.accent : root.fg
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: root.display !== null && root.display.hdrLive
          }
          Text {
            visible: root.display !== null
            text: root.display && root.display.tenBit ? "10-bit" : "8-bit"
            color: root.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
        }

        // ---- VRR ----
        Row {
          spacing: Style.space(10)
          Text {
            width: root.labelWidth
            text: "VRR"
            color: root.dim
            font: labelMetrics.font
          }
          Text {
            text: root.vrrText(root.display)
            color: root.display && root.display.vrrLive ? root.accent : root.fg
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: root.display !== null && root.display.vrrLive
          }
          Text {
            visible: root.display !== null
            text: {
              var d = root.display
              if (!d) return ""
              var bits = [d.hz + " Hz"]
              if (d.tearing) bits.push("tearing")
              if (d.directScanout) bits.push("scanout")
              return bits.join(" · ")
            }
            color: root.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
        }
      }
    }
  }
}
