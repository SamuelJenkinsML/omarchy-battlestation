import QtQuick
import Quickshell.Io
import Quickshell.Services.UPower

// Controller presence (from the pad daemon), battery (UPower first, sysfs
// fallback) and the hold-to-switch chord. The glyph/dim/charging treatment
// follows atoslins/omarchy-plugin-dualsense (MIT, (c) 2026 Atos Lins).
Item {
  id: root

  required property string cli
  property bool enabled: true
  property var pads: ({})          // path -> {name, bus, vendor, product, uniq}
  property var sysfsBatteries: []
  property string daemonError: ""

  signal chord(var pad)

  readonly property var padList: Object.keys(pads).map(function(k) { return pads[k] })
  readonly property var upowerPads: {
    var list = []
    var devs = UPower.devices ? UPower.devices.values : []
    for (var i = 0; i < devs.length; i++) {
      var d = devs[i]
      if (d && d.type === UPowerDeviceType.GamingInput && d.isPresent !== false) list.push(d)
    }
    return list
  }
  readonly property bool connected: padList.length > 0 || upowerPads.length > 0
  readonly property string name: {
    if (padList.length > 0) return padList[0].name
    if (upowerPads.length > 0) return upowerPads[0].model || "Controller"
    return ""
  }
  readonly property string bus: padList.length > 0 ? padList[0].bus : ""
  readonly property bool wired: bus === "usb" && battery.source === ""

  // {percent: 0-100 or -1, level: string, charging: bool, source: string}
  readonly property var battery: {
    if (upowerPads.length > 0) {
      var d = upowerPads[0]
      var p = Number(d.percentage)
      if (p <= 1.0) p = p * 100
      return {
        percent: Math.round(p),
        level: "",
        charging: d.state === UPowerDeviceState.Charging || d.state === UPowerDeviceState.PendingCharge,
        full: d.state === UPowerDeviceState.FullyCharged,
        source: "upower"
      }
    }
    if (sysfsBatteries.length > 0) {
      var b = sysfsBatteries[0]
      return {
        percent: b.percent === null || b.percent === undefined ? -1 : b.percent,
        level: b.level || "",
        charging: b.status === "charging",
        full: b.status === "full",
        source: "sysfs"
      }
    }
    return { percent: -1, level: "", charging: false, full: false, source: "" }
  }

  // Five-step glyph when only a level is known, so we never invent a percentage.
  readonly property int batterySteps: {
    var b = battery
    if (b.percent >= 0) return Math.max(0, Math.min(4, Math.round(b.percent / 25)))
    var map = { critical: 0, low: 1, normal: 2, high: 3, full: 4 }
    return map[b.level] !== undefined ? map[b.level] : -1
  }
  readonly property bool low: battery.source !== "" && !battery.charging && batterySteps >= 0 && batterySteps <= 1

  function batteryText() {
    var b = battery
    if (b.percent >= 0 && b.source === "upower") return b.percent + "%"
    if (b.percent >= 0) return b.percent + "%"
    if (b.level) return b.level.charAt(0).toUpperCase() + b.level.slice(1)
    return root.connected ? (root.bus === "usb" ? "Wired" : "—") : ""
  }

  function refreshSysfs() {
    if (!sysfsProc.running) sysfsProc.running = true
  }

  function restartDaemon() {
    daemon.running = false
    restartTimer.interval = 300
    restartTimer.restart()
  }

  function handle(line) {
    var msg
    try { msg = JSON.parse(line) } catch (e) { return }
    var next
    if (msg.event === "connected") {
      next = Object.assign({}, pads)
      next[msg.path] = msg
      pads = next
      refreshSysfs()
    } else if (msg.event === "disconnected") {
      next = Object.assign({}, pads)
      delete next[msg.path]
      pads = next
      refreshSysfs()
    } else if (msg.event === "chord") {
      root.chord(msg)
    } else if (msg.event === "ready") {
      daemonError = ""
    }
  }

  Process {
    id: daemon
    command: [root.cli, "pad-daemon"]
    stdout: SplitParser { onRead: function(line) { root.handle(line) } }
    stderr: StdioCollector { onStreamFinished: if (text.trim()) root.daemonError = text.trim().split("\n").pop() }
    onExited: {
      root.pads = ({})
      if (root.enabled) {
        restartTimer.interval = 3000
        restartTimer.restart()
      }
    }
  }

  Timer {
    id: restartTimer
    interval: 3000
    onTriggered: if (root.enabled && !daemon.running) daemon.running = true
  }

  Process {
    id: sysfsProc
    command: [root.cli, "controller"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(text)
          root.sysfsBatteries = parsed.batteries || []
        } catch (e) {}
      }
    }
  }

  // xpadneo only updates capacity_level occasionally; poll gently while connected.
  Timer {
    interval: 60000
    repeat: true
    running: root.padList.length > 0 && root.upowerPads.length === 0
    onTriggered: root.refreshSysfs()
  }

  Component.onCompleted: { if (enabled) daemon.running = true }
  Component.onDestruction: daemon.running = false
}
