import QtQuick
import Quickshell.Io

// NVIDIA stats from one long-running `nvidia-smi -lms`, not a process per tick.
// Field set, thresholds and throttle decoding follow candycrabmusic/gpu-monitor
// (MIT, (c) 2026 vichu); the hide-when-absent behaviour follows
// DanSmith888/omarchy-gpu (MIT, (c) 2026 Daniel Smith).
Item {
  id: root

  property int intervalMs: 2000
  property bool available: false
  property bool probed: false
  property string name: ""
  property string driver: ""
  property real temp: -1
  property real util: 0
  property real memUsed: 0     // MiB
  property real memTotal: 0    // MiB
  property real power: 0       // W
  property real powerLimit: 0  // W
  property real clock: 0       // MHz
  property string pstate: ""
  property var throttle: []
  property var utilHistory: []
  property var tempHistory: []
  readonly property int maxSamples: 60
  readonly property real memFraction: memTotal > 0 ? memUsed / memTotal : 0
  readonly property string shortName: name.replace(/^NVIDIA\s+/, "").replace(/^GeForce\s+/, "")

  function num(v) {
    var n = parseFloat(v)
    return isNaN(n) ? 0 : n
  }

  function decodeThrottle(hex) {
    var bits = parseInt(String(hex || "0"), 16)
    if (isNaN(bits)) return []
    var names = []
    if (bits & 0x4) names.push("power cap")
    if (bits & 0x8) names.push("hw slowdown")
    if (bits & 0x20) names.push("thermal (sw)")
    if (bits & 0x40) names.push("thermal (hw)")
    if (bits & 0x80) names.push("power brake")
    return names
  }

  function push(list, value) {
    var next = list.slice(Math.max(0, list.length - maxSamples + 1))
    next.push(value)
    return next
  }

  function parse(line) {
    var f = String(line || "").split(",").map(function(s) { return s.trim() })
    if (f.length < 9) return
    temp = num(f[0])
    util = num(f[1])
    memUsed = num(f[2])
    memTotal = num(f[3])
    power = num(f[4])
    powerLimit = num(f[5])
    clock = num(f[6])
    pstate = f[7]
    throttle = decodeThrottle(f[8])
    utilHistory = push(utilHistory, util)
    tempHistory = push(tempHistory, temp)
  }

  Process {
    id: infoProc
    command: ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"]
    running: true
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var first = String(text || "").trim().split("\n")[0]
        if (!first) return
        var parts = first.split(",")
        root.name = parts[0].trim()
        root.driver = parts.length > 1 ? parts[1].trim() : ""
        root.available = root.name !== ""
      }
    }
    onExited: function(code) {
      root.probed = true
      if (code !== 0) root.available = false
      else if (root.available) streamProc.running = true
    }
  }

  Process {
    id: streamProc
    command: ["nvidia-smi",
      "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,power.limit,clocks.current.graphics,pstate,clocks_event_reasons.active",
      "--format=csv,noheader,nounits", "-lms", String(root.intervalMs)]
    stdout: SplitParser { onRead: function(line) { root.parse(line) } }
    onExited: if (root.available) restartTimer.restart()
  }

  Timer {
    id: restartTimer
    interval: 5000
    onTriggered: if (root.available && !streamProc.running) streamProc.running = true
  }

  Component.onDestruction: streamProc.running = false
}
