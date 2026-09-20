import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// The bar pill. One instance per monitor; all state lives in Service.qml.
// Split widget/panel pattern follows Omarchy's weather widget.
BarWidget {
  id: root
  moduleName: "io.github.samueljenkinsml.battlestation"

  property var svc: null
  readonly property bool ready: svc !== null
  readonly property string screenName: root.QsWindow.window && root.QsWindow.window.screen ? root.QsWindow.window.screen.name : ""

  readonly property string view: String(setting("view", "full"))
  readonly property bool compact: view === "compact" || vertical
  readonly property bool showDisplay: setting("showDisplay", true) !== false
  readonly property bool showGpu: setting("showGpu", true) !== false
  readonly property bool showController: setting("showController", true) !== false
  readonly property bool showFps: setting("showFps", true) !== false
  readonly property bool showStream: setting("showStream", true) !== false
  readonly property int gpuTempWarn: Number(setting("gpuTempWarn", 80))

  readonly property color fg: bar ? bar.barForeground : Color.foreground
  readonly property color dim: Util.alpha(fg, 0.45)
  readonly property color accent: Color.accent
  readonly property color urgent: bar ? bar.urgent : Color.urgent

  readonly property var display: ready ? svc.display.summaryFor(screenName) : null
  readonly property var sceneInfo: ready ? svc.activeSceneInfo : null
  readonly property bool pending: ready && svc.pendingRevert !== null
  readonly property bool gameRunning: ready && svc.game.gameRunning
  readonly property real fps: ready ? svc.game.fps : -1

  function resolveService() {
    if (bar && bar.shell && typeof bar.shell.serviceFor === "function")
      svc = bar.shell.serviceFor(moduleName)
  }

  onBarChanged: resolveService()
  Timer {
    interval: 1000
    repeat: true
    running: !root.ready
    onTriggered: root.resolveService()
  }

  // ---- glyph helpers ----
  function batteryGlyph() {
    var c = svc.controller
    if (!c.connected) return ""
    if (c.battery.charging) return "󰂄"
    var steps = c.batterySteps
    if (steps < 0) return c.bus === "usb" ? "󰚥" : "󰂑"
    return ["󰁺", "󰁼", "󰁾", "󰂀", "󰁹"][steps]
  }

  function gpuMemText() {
    var g = svc.gpu
    return (g.memUsed / 1024).toFixed(1) + "G"
  }

  function tooltip() {
    if (!ready) return "Battlestation: starting"
    var lines = []
    lines.push((sceneInfo ? sceneInfo.label : "No scene") + (pending ? "  ·  keep layout? " + svc.revertRemaining + "s" : ""))
    if (display) {
      var hdr = display.hdrLive ? "HDR on" : (display.hdrMode === "auto" ? "HDR auto (armed)" : (display.hdrCapable ? "HDR off" : "SDR"))
      var vrr = display.vrrLive ? "VRR active" : (display.vrrMode > 0 ? "VRR ready" : (display.vrrCapable ? "VRR off" : "no VRR"))
      lines.push(display.name + "  " + display.width + "×" + display.height + " @ " + display.hzExact.toFixed(2) + " Hz")
      lines.push(hdr + "  ·  " + vrr + "  ·  " + (display.tenBit ? "10-bit" : "8-bit"))
    }
    if (svc.gpu.available)
      lines.push(svc.gpu.shortName + "  " + Math.round(svc.gpu.temp) + "°C  " + Math.round(svc.gpu.util) + "%  "
        + gpuMemText() + " / " + (svc.gpu.memTotal / 1024).toFixed(0) + "G  ·  driver " + svc.gpu.driver)
    if (svc.controller.connected)
      lines.push(svc.controller.name + "  " + svc.controller.batteryText() + (svc.controller.battery.charging ? " (charging)" : ""))
    if (gameRunning && svc.game.currentGame)
      lines.push(svc.game.currentGame.title + (fps >= 0 ? "  " + Math.round(fps) + " fps" : ""))
    if (svc.streamEnabled)
      lines.push(svc.streamAttached ? "Streaming to a client  ·  displays off" : "Streaming ready for Moonlight")
    lines.push("Click: panel  ·  Middle: toggle scene  ·  Right: compact")
    return lines.join("\n")
  }

  function toggleView() {
    var entry = { id: moduleName }
    for (var k in settings) if (k !== "id") entry[k] = settings[k]
    entry.view = compact && !vertical ? "full" : "compact"
    settings = entry
    if (bar && bar.shell && typeof bar.shell.updateEntryInline === "function")
      bar.shell.updateEntryInline(moduleName, entry)
  }

  // ---- panel wiring (weather widget pattern) ----
  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = pill
    if ("hostWidget" in target) target.hostWidget = root
    if ("svc" in target) target.svc = root.svc
    if ("screenName" in target) target.screenName = root.screenName
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function open() { if (panelLoader.item) panelLoader.item.openFromHotkey() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function toggle() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  onSettingsChanged: injectPanel()
  onSvcChanged: injectPanel()
  onScreenNameChanged: injectPanel()

  Loader {
    id: panelLoader
    active: root.ready
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
  }

  implicitWidth: pill.implicitWidth
  implicitHeight: pill.implicitHeight

  Item {
    id: pill
    anchors.fill: parent
    implicitWidth: root.vertical ? root.barSize : row.implicitWidth + Style.space(16)
    implicitHeight: root.vertical ? row.implicitHeight + Style.space(12) : root.barSize

    function triggerPress(button) {
      if (root.bar) root.bar.hideTooltip(pill)
      if (!root.ready) return
      if (button === Qt.RightButton) root.toggleView()
      else if (button === Qt.MiddleButton) root.svc.toggleScene()
      else root.toggle()
    }

    Component.onCompleted: if (root.bar && root.bar.registerClickTarget) root.bar.registerClickTarget(pill)
    Component.onDestruction: if (root.bar && root.bar.unregisterClickTarget) root.bar.unregisterClickTarget(pill)

    Flow {
      id: row
      anchors.centerIn: parent
      flow: root.vertical ? Flow.TopToBottom : Flow.LeftToRight
      spacing: Style.space(root.compact ? 6 : 9)

      // Scene / busy / pending revert
      Seg {
        text: !root.ready ? "󰊗" : (root.svc.busy ? "󰔟" : (root.sceneInfo ? root.sceneInfo.icon : "󰍹"))
        color: root.pending ? root.urgent : root.fg
        spin: root.ready && root.svc.busy
      }
      Seg {
        visible: root.pending && !root.vertical
        text: "keep? " + (root.ready ? root.svc.revertRemaining : 0) + "s"
        color: root.urgent
        bold: true
      }

      // Streaming host: shown only while switched on; accent while a client is attached.
      Seg {
        visible: root.showStream && root.ready && root.svc.streamEnabled && !root.pending
        text: "󰑈"
        color: root.svc && root.svc.streamAttached ? Color.accent : root.fg
        opacity: root.svc && (root.svc.streamAttached || root.svc.streamPhase === "armed") ? 1 : 0.5
      }

      // Display
      Seg {
        visible: root.showDisplay && !root.compact && !root.pending && root.display !== null
        text: root.display ? root.display.resolution + "·" + root.display.hz : ""
      }
      Seg {
        visible: root.showDisplay && !root.compact && !root.pending && root.display !== null
          && (root.display.hdrLive || root.display.hdrMode !== "off")
        text: "HDR"
        small: true
        bold: root.display !== null && root.display.hdrLive
        color: root.display !== null && root.display.hdrLive ? root.accent : root.fg
      }
      Seg {
        visible: root.showDisplay && !root.compact && !root.pending && root.display !== null
          && (root.display.vrrLive || root.display.vrrMode > 0)
        text: "VRR"
        small: true
        bold: root.display !== null && root.display.vrrLive
        color: root.display !== null && root.display.vrrLive ? root.accent : root.dim
      }

      // GPU
      Seg {
        visible: root.showGpu && root.ready && root.svc.gpu.available && root.svc.gpu.temp >= 0
        text: root.ready ? (root.compact ? Math.round(root.svc.gpu.temp) + "°"
                                         : "󰢮 " + Math.round(root.svc.gpu.temp) + "° " + root.gpuMemText()) : ""
        color: root.ready && root.svc.gpu.temp >= root.gpuTempWarn ? root.urgent : root.fg
      }

      // Controller
      Seg {
        visible: root.showController && root.ready && root.svc.controller.connected
        text: root.ready ? "󰊴" + (root.compact ? "" : " " + root.batteryGlyph()) : ""
        color: root.ready && root.svc.controller.low ? root.urgent : root.fg
      }

      // FPS
      Seg {
        visible: root.showFps && root.gameRunning && root.fps >= 0
        text: Math.round(root.fps) + (root.compact ? "" : " fps")
        color: root.accent
        bold: true
      }
    }

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: function(mouse) { pill.triggerPress(mouse.button) }
      onEntered: if (root.bar) root.bar.showTooltip(pill, root.tooltip())
      onExited: if (root.bar) root.bar.hideTooltip(pill)
    }
  }

  component Seg: Text {
    property bool small: false
    property bool bold: false
    property bool spin: false
    textFormat: Text.PlainText
    color: root.fg
    font.family: root.bar ? root.bar.fontFamily : Style.font.family
    font.pixelSize: small ? Style.font.caption : Style.font.body
    font.bold: bold
    font.letterSpacing: small ? 0.6 : 0
    renderType: Text.NativeRendering
    verticalAlignment: Text.AlignVCenter
    height: root.vertical ? implicitHeight : root.barSize
    onSpinChanged: if (!spin) rotation = 0
    RotationAnimation on rotation {
      running: spin
      loops: Animation.Infinite
      from: 0; to: 360; duration: 1200
    }
    Behavior on color { ColorAnimation { duration: 160 } }
  }
}
