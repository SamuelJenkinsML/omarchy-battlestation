import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "qml"

// Detail panel: scene switcher with Keep/Revert, then display, GPU,
// controller and game cards. Opened from the pill, `omarchy-shell shell
// toggle io.github.samueljenkinsml.battlestation`, or a keybind.
Panel {
  id: root
  moduleName: "io.github.samueljenkinsml.battlestation"
  ipcTarget: moduleName
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var svc: null
  property string screenName: ""
  readonly property var barIdentity: hostWidget || root

  property int cursor: -1

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(fg, 1.45)
  readonly property color faint: Util.alpha(fg, 0.12)
  readonly property color accent: Color.accent
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var display: svc ? svc.display.summaryFor(screenName) : null
  readonly property var sceneInfo: svc ? svc.activeSceneInfo : null
  readonly property var gpu: svc ? svc.gpu : null
  readonly property var pad: svc ? svc.controller : null
  readonly property var game: svc ? svc.game : null

  function openFromHotkey() { root.controller.show() }
  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function hdrText(d) {
    if (!d) return "—"
    if (!d.hdrCapable) return "Not supported"
    if (d.hdrLive) return "On"
    if (d.hdrMode === "auto") return "Auto · armed for fullscreen HDR"
    return "Off"
  }

  function vrrText(d) {
    if (!d) return "—"
    if (d.vrrLive) return "Active"
    var modes = ["Off", "Always", "Fullscreen", "Fullscreen games & video"]
    if (d.vrrMode > 0) return modes[d.vrrMode] + (d.vrrCapable ? "" : " (display does not report VRR)")
    return d.vrrCapable ? "Off" : "Not supported on this link"
  }

  function duration(ms) {
    var s = Math.max(0, Math.floor(ms / 1000))
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
    return h > 0 ? h + "h " + m + "m" : m + "m " + (s % 60) + "s"
  }

  property real now: Date.now()
  Timer { interval: 1000; repeat: true; running: root.opened; onTriggered: root.now = Date.now() }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened && root.svc !== null
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (!root.svc || root.svc.scenes.length === 0) return
        var n = root.svc.scenes.length
        var d = dx !== 0 ? dx : dy
        root.cursor = root.cursor < 0 ? 0 : (root.cursor + d + n) % n
      }
      onActivateRequested: {
        if (root.svc && root.cursor >= 0 && root.cursor < root.svc.scenes.length)
          root.svc.enterScene(root.svc.scenes[root.cursor].name)
      }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
          id: column
          width: parent.width
          spacing: Style.space(14)

          // ---------- Hero ----------
          Item {
            width: parent.width
            implicitHeight: Math.max(heroIcon.implicitHeight, heroText.implicitHeight, heroBig.implicitHeight)

            Text {
              id: heroIcon
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: root.sceneInfo ? root.sceneInfo.icon : "󰊗"
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
            }
            Column {
              id: heroText
              anchors.left: heroIcon.right
              anchors.leftMargin: Style.space(14)
              anchors.right: heroBig.left
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)
              Text {
                text: root.sceneInfo ? root.sceneInfo.label : "Battlestation"
                color: root.fg
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
                elide: Text.ElideRight
                width: parent.width
              }
              Text {
                text: {
                  if (!root.svc) return ""
                  if (root.svc.busy) return root.svc.busyLabel.toUpperCase() + "…"
                  if (root.game && root.game.gameRunning && root.game.currentGame) return "PLAYING · " + root.game.currentGame.title.toUpperCase()
                  if (root.pad && root.pad.connected) return "CONTROLLER READY"
                  return "IDLE"
                }
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.2
                elide: Text.ElideRight
                width: parent.width
              }
            }
            Text {
              id: heroBig
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: {
                if (root.game && root.game.gameRunning && root.game.fps >= 0) return Math.round(root.game.fps) + " fps"
                if (root.gpu && root.gpu.available && root.gpu.temp >= 0) return Math.round(root.gpu.temp) + "°"
                return ""
              }
              color: root.game && root.game.gameRunning && root.game.fps >= 0 ? root.accent : root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.displayLarge
              font.bold: true
            }
          }

          // ---------- Setup banner ----------
          Card {
            visible: root.svc !== null && !root.svc.configured
            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.svc && root.svc.configErrors.length
                ? "Config needs attention:\n• " + root.svc.configErrors.join("\n• ")
                : "No scenes yet. Create a starter config from your current display layout."
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Row {
              spacing: Style.space(8)
              Button {
                text: "Create config"
                iconText: "󰐕"
                foreground: root.fg
                visible: root.svc && root.svc.configErrors.length === 1 && String(root.svc.configErrors[0]).indexOf("does not exist") >= 0
                onClicked: root.svc.initConfig()
              }
              Button { text: "Open config"; iconText: "󰏫"; foreground: root.fg; onClicked: root.svc.openConfig() }
              Button { text: "Doctor"; iconText: "󰓙"; foreground: root.fg; onClicked: root.svc.openDoctor() }
            }
          }

          // ---------- Keep / Revert ----------
          Card {
            visible: root.svc !== null && root.svc.pendingRevert !== null
            highlight: root.urgent
            Text {
              width: parent.width
              text: "Keep this display layout?  Reverting in " + (root.svc ? root.svc.revertRemaining : 0) + "s"
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }
            Row {
              spacing: Style.space(8)
              Button { text: "Keep"; iconText: "󰄬"; foreground: root.fg; onClicked: root.svc.keep() }
              Button { text: "Revert"; iconText: "󰑓"; foreground: root.fg; onClicked: root.svc.revert() }
            }
          }

          // ---------- Scenes ----------
          PanelSectionHeader {
            visible: root.svc !== null && root.svc.scenes.length > 0
            text: "SCENES"
            foreground: root.fg
            fontFamily: root.fontFamily
            width: parent.width
          }
          Flow {
            visible: root.svc !== null && root.svc.scenes.length > 0
            width: parent.width
            spacing: Style.space(8)
            Repeater {
              model: root.svc ? root.svc.scenes : []
              delegate: Button {
                required property var modelData
                required property int index
                text: modelData.label
                iconText: modelData.icon
                foreground: root.fg
                selected: root.svc && modelData.name === root.svc.activeScene
                hasCursor: root.cursor === index
                bordered: true
                tooltipText: (modelData.steam === "bigpicture" ? "Opens Big Picture. " : "")
                  + (modelData.wakeTv ? "Wakes the TV. " : "")
                  + (root.svc && root.svc.chordScenes.indexOf(modelData.name) >= 0 ? "Controller chord scene." : "")
                enabled: root.svc && !root.svc.busy
                onClicked: root.svc.enterScene(modelData.name)
              }
            }
          }
          Text {
            visible: root.svc !== null && root.svc.chordScenes.length > 0
            width: parent.width
            wrapMode: Text.WordWrap
            text: {
              if (!root.svc || !root.svc.sceneState.controller) return ""
              var chord = root.svc.sceneState.controller.chord.map(function(b) {
                return ({ BTN_SELECT: "View", BTN_START: "Menu", BTN_MODE: "Guide", BTN_SOUTH: "A", BTN_EAST: "B",
                          BTN_NORTH: "X", BTN_WEST: "Y", BTN_TL: "LB", BTN_TR: "RB" })[b] || b
              }).join(" + ")
              var secs = (root.svc.sceneState.controller.holdMs / 1000).toFixed(1).replace(/\.0$/, "")
              return "Hold " + chord + " for " + secs + "s to switch scene · middle-click the pill does the same"
            }
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          // ---------- Streaming ----------
          PanelSectionHeader {
            visible: root.svc !== null && root.svc.streamAvailable
            text: "STREAMING"
            foreground: root.fg
            fontFamily: root.fontFamily
            width: parent.width
          }
          Toggle {
            visible: root.svc !== null && root.svc.streamAvailable
            width: parent.width
            label: "Stream to Moonlight"
            description: root.streamHint()
            checked: root.svc !== null && root.svc.streamEnabled
            foreground: root.fg
            accent: root.accent
            fontFamily: root.fontFamily
            enabled: root.svc !== null && root.svc.streamPhase !== "working"
            onClicked: root.svc.streamToggle()
          }
          Card {
            visible: root.svc !== null && root.svc.streamAvailable && root.svc.streamEnabled
            highlight: root.svc && root.svc.streamAttached ? root.accent : "transparent"
            KV {
              key: "State"
              value: root.streamStateText()
              valueColor: root.svc && root.svc.streamAttached ? root.accent : root.fg
            }
            KV {
              key: "Client"
              visible: root.svc !== null && root.svc.streamAttached
              value: root.svc && root.svc.stream.mode ? String(root.svc.stream.mode).replace("x", "×").replace("@", " @ ") + " Hz" : ""
            }
            KV {
              key: "Tailscale"
              visible: root.svc !== null && !!root.svc.stream.tailscaleIp
              value: root.svc ? (root.svc.stream.tailscaleIp || "") : ""
            }
            Button {
              visible: root.svc !== null && root.svc.streamAttached
              text: "Give the desktop back"
              iconText: "󰍹"
              foreground: root.fg
              bordered: true
              tooltipText: "Turns the displays back on. The client keeps its session but sees an empty desktop."
              onClicked: root.svc.streamDetach()
            }
          }

          // ---------- Display ----------
          PanelSectionHeader { text: "DISPLAY"; foreground: root.fg; fontFamily: root.fontFamily; width: parent.width; visible: root.display !== null }
          Card {
            visible: root.display !== null
            KV { key: "Output"; value: root.display ? root.display.name + " · " + root.display.description : "" }
            KV {
              key: "Mode"
              value: root.display ? root.display.width + "×" + root.display.height + " @ " + root.display.hzExact.toFixed(2) + " Hz · scale " + root.display.scale : ""
            }
            KV {
              key: "HDR"
              value: root.hdrText(root.display)
              valueColor: root.display && root.display.hdrLive ? root.accent : root.fg
            }
            KV {
              key: "VRR"
              value: root.vrrText(root.display)
              valueColor: root.display && root.display.vrrLive ? root.accent : root.fg
            }
            KV {
              key: "Signal"
              value: root.display ? (root.display.tenBit ? "10-bit" : "8-bit") + " · " + root.display.link
                + (root.display.maxBpc < 10 && root.display.hdrCapable ? " · link too narrow for 10-bit here" : "") : ""
            }
            KV {
              key: "Path"
              visible: root.display && (root.display.tearing || root.display.directScanout)
              value: root.display ? [root.display.directScanout ? "direct scanout" : "", root.display.tearing ? "tearing" : ""].filter(function(s) { return s }).join(" · ") : ""
            }
          }

          // ---------- GPU ----------
          PanelSectionHeader { text: "GPU"; foreground: root.fg; fontFamily: root.fontFamily; width: parent.width; visible: root.gpu && root.gpu.available }
          Card {
            visible: root.gpu !== null && root.gpu.available
            KV { key: root.gpu ? root.gpu.shortName : ""; value: root.gpu ? "driver " + root.gpu.driver + " · " + root.gpu.pstate : "" }
            Meter {
              label: "Load"
              fraction: root.gpu ? root.gpu.util / 100 : 0
              valueText: root.gpu ? Math.round(root.gpu.util) + "%" : ""
              foreground: root.fg
              fontFamily: root.fontFamily
            }
            Meter {
              label: "VRAM"
              fraction: root.gpu ? root.gpu.memFraction : 0
              valueText: root.gpu ? (root.gpu.memUsed / 1024).toFixed(1) + " / " + (root.gpu.memTotal / 1024).toFixed(0) + " GiB" : ""
              foreground: root.fg
              warn: 0.9
              fontFamily: root.fontFamily
            }
            Meter {
              label: "Temp"
              fraction: root.gpu ? root.gpu.temp / 100 : 0
              valueText: root.gpu ? Math.round(root.gpu.temp) + " °C" : ""
              foreground: root.fg
              warn: root.hostWidget ? Number(root.hostWidget.gpuTempWarn) / 100 : 0.8
              fontFamily: root.fontFamily
            }
            Meter {
              label: "Power"
              fraction: root.gpu && root.gpu.powerLimit > 0 ? root.gpu.power / root.gpu.powerLimit : 0
              valueText: root.gpu ? Math.round(root.gpu.power) + " / " + Math.round(root.gpu.powerLimit) + " W · " + Math.round(root.gpu.clock) + " MHz" : ""
              foreground: root.fg
              fontFamily: root.fontFamily
            }
            Sparkline {
              width: parent.width
              height: Style.space(34)
              values: root.gpu ? root.gpu.utilHistory : []
              maxValue: 100
              color: root.accent
            }
            KV {
              key: "Throttle"
              visible: root.gpu && root.gpu.throttle.length > 0
              value: root.gpu ? root.gpu.throttle.join(", ") : ""
              valueColor: root.urgent
            }
          }

          // ---------- Controller ----------
          PanelSectionHeader { text: "CONTROLLER"; foreground: root.fg; fontFamily: root.fontFamily; width: parent.width }
          Card {
            KV {
              key: root.pad && root.pad.connected ? root.pad.name : "No controller"
              value: root.pad && root.pad.connected ? (root.pad.bus === "bluetooth" ? "Bluetooth" : (root.pad.bus === "usb" ? "USB" : "Connected")) : "Pair an Xbox pad over Bluetooth or plug one in"
            }
            Meter {
              visible: root.pad !== null && root.pad.connected && root.pad.batterySteps >= 0
              label: root.pad && root.pad.battery.charging ? "Charging" : "Battery"
              fraction: root.pad ? (root.pad.battery.percent >= 0 ? root.pad.battery.percent / 100 : (root.pad.batterySteps + 1) / 5) : 0
              valueText: root.pad ? root.pad.batteryText() : ""
              foreground: root.fg
              lowWarn: 0.25
              fontFamily: root.fontFamily
            }
            Text {
              visible: root.pad !== null && root.pad.daemonError !== ""
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.pad ? "Controller reader: " + root.pad.daemonError : ""
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          // ---------- Game ----------
          PanelSectionHeader {
            text: "GAME"
            foreground: root.fg
            fontFamily: root.fontFamily
            width: parent.width
            visible: root.game !== null && root.game.gameRunning
          }
          Card {
            visible: root.game !== null && root.game.gameRunning && root.game.currentGame !== null
            KV {
              key: root.game && root.game.currentGame ? root.game.currentGame.title : ""
              value: root.game && root.game.currentGame ? root.duration(root.now - root.game.currentGame.since) : ""
            }
            KV {
              key: "FPS"
              value: root.game && root.game.fps >= 0
                ? Math.round(root.game.fps) + " fps · " + root.game.frametime.toFixed(1) + " ms"
                : "No MangoHud data (launch Steam via the Battlestation launcher; see doctor)"
              valueColor: root.game && root.game.fps >= 0 ? root.accent : root.dim
            }
            Sparkline {
              visible: root.game !== null && root.game.fpsHistory.length > 1
              width: parent.width
              height: Style.space(34)
              values: root.game ? root.game.fpsHistory : []
              color: root.accent
            }
          }

          // ---------- Footer ----------
          PanelSeparator { width: parent.width; foreground: root.fg }
          Row {
            spacing: Style.space(6)
            PanelActionButton { iconText: "󰆓"; tooltipText: "Save current layout as a scene"; foreground: root.fg; onClicked: root.svc.captureScene() }
            PanelActionButton { iconText: "󰔂"; tooltipText: "Wake TV"; foreground: root.fg; visible: root.svc && root.svc.sceneState.tvConfigured === true; onClicked: root.svc.tvWake() }
            PanelActionButton { iconText: "󰏫"; tooltipText: "Edit config"; foreground: root.fg; onClicked: root.svc.openConfig() }
            PanelActionButton { iconText: "󰓙"; tooltipText: "Run doctor"; foreground: root.fg; onClicked: root.svc.openDoctor() }
          }
        }
      }
    }
  }

  function streamStateText() {
    if (!root.svc) return ""
    var phase = root.svc.streamPhase
    if (phase === "streaming") return "Streaming · displays off"
    if (phase === "armed") return "Ready for a client"
    if (phase === "working" || phase === "arming") return "Starting…"
    return "Off"
  }

  function streamHint() {
    if (!root.svc) return ""
    if (root.svc.streamAttached) return "Switching off now ends the running stream."
    if (root.svc.streamEnabled) return "Works with the TV off. Off stops Sunshine and closes its ports."
    return "Off: no virtual display, Sunshine stopped, nothing listening."
  }

  // ---------- small building blocks ----------
  component Card: Rectangle {
    default property alias content: inner.data
    property color highlight: "transparent"
    width: parent ? parent.width : 0
    implicitHeight: inner.implicitHeight + Style.space(20)
    radius: Style.cornerRadius
    color: Util.alpha(root.fg, 0.05)
    border.width: highlight.a > 0 ? Math.max(1, Style.space(2)) : 0
    border.color: highlight
    Column {
      id: inner
      x: Style.space(10)
      y: Style.space(10)
      width: parent.width - Style.space(20)
      spacing: Style.space(8)
    }
  }

  component KV: Item {
    property string key: ""
    property string value: ""
    property color valueColor: root.fg
    width: parent ? parent.width : 0
    implicitHeight: Math.max(k.implicitHeight, v.implicitHeight)
    Text {
      id: k
      anchors.left: parent.left
      width: Math.min(implicitWidth, parent.width * 0.42)
      text: parent.key
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }
    Text {
      id: v
      anchors.right: parent.right
      anchors.left: k.right
      anchors.leftMargin: Style.space(12)
      horizontalAlignment: Text.AlignRight
      text: parent.value
      color: parent.valueColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      wrapMode: Text.WordWrap
    }
  }
}
