import QtQuick
import Quickshell.Io
import Quickshell.Hyprland

// Live monitor state from Hyprland plus EDID/DRM capabilities from the helper.
// HDR is read from colorManagementPreset (hdr/hdredid), not from a 10-bit
// scanout format, which 10-bit SDR also reports.
Item {
  id: root

  required property string cli
  property var monitors: []
  property var caps: ({})
  property var layout: ({})   // connector -> {hdr, vrr, bpc, mode} from the active scene

  signal topologyChanged()
  signal outputAdded()

  function refresh() {
    if (!monProc.running) monProc.running = true
  }

  function refreshCaps() {
    if (!capsProc.running) capsProc.running = true
  }

  function resolutionLabel(w, h) {
    if (w === 3840 && h === 2160) return "4K"
    if (w === 7680 && h === 4320) return "8K"
    if (h === 1440 && w >= 3440) return "UW"
    if (h === 1440) return "1440p"
    if (h === 1080 && w >= 2560) return "UW"
    if (h === 1080) return "1080p"
    if (h === 2160 && w >= 5120) return "5K2K"
    return w + "×" + h
  }

  function summaryFor(name) {
    var mon = null
    for (var i = 0; i < monitors.length; i++) {
      if (monitors[i].name === name) { mon = monitors[i]; break }
    }
    if (!mon && monitors.length > 0) {
      for (var j = 0; j < monitors.length; j++) {
        if (!monitors[j].disabled) { mon = monitors[j]; break }
      }
    }
    if (!mon) return null
    var c = caps[mon.name] || {}
    var l = layout[mon.name] || {}
    var preset = String(mon.colorManagementPreset || "")
    var hdrLive = preset === "hdr" || preset === "hdredid"
    var hdrMode = l.hdr || (hdrLive ? "always" : "off")
    var vrrMode = l.vrr !== undefined ? Number(l.vrr) : (mon.vrr ? 1 : 0)
    var tenBit = String(mon.currentFormat || "").indexOf("2101010") >= 0
    return {
      name: mon.name,
      description: mon.description || "",
      width: mon.width,
      height: mon.height,
      hz: Math.round(Number(mon.refreshRate || 0)),
      hzExact: Number(mon.refreshRate || 0),
      resolution: resolutionLabel(mon.width, mon.height),
      scale: mon.scale,
      disabled: !!mon.disabled,
      format: mon.currentFormat || "",
      tenBit: tenBit,
      hdrCapable: !!c.hdr,
      hdrLive: hdrLive,
      hdrMode: hdrMode,
      vrrCapable: !!c.vrrCapable || !!c.freesync || !!c.vrr_max,
      vrrLive: !!mon.vrr || c.vrrEnabled === true,
      vrrMode: vrrMode,
      maxBpc: c.maxBpcAtMode || 8,
      link: Object.keys(c).length === 0 ? "Virtual" : c.is_hdmi ? (c.max_frl_gbps ? ("HDMI 2.1 · " + c.max_frl_gbps + " Gbps FRL") : ("HDMI · " + (c.max_tmds_mhz || 340) + " MHz TMDS")) : "DisplayPort",
      tearing: !!mon.activelyTearing,
      directScanout: String(mon.directScanoutTo || "") !== "" && String(mon.directScanoutTo) !== "0"
    }
  }

  Process {
    id: monProc
    command: ["hyprctl", "monitors", "all", "-j"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(text)
          var before = root.monitors.map(function(m) { return m.name + ":" + m.disabled }).join(",")
          root.monitors = parsed
          var after = parsed.map(function(m) { return m.name + ":" + m.disabled }).join(",")
          if (before !== after) {
            root.refreshCaps()
            root.topologyChanged()
          }
        } catch (e) {}
      }
    }
  }

  Process {
    id: capsProc
    command: [root.cli, "caps"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(text)
          if (parsed.ok) root.caps = parsed.monitors || {}
        } catch (e) {}
      }
    }
  }

  Timer {
    id: debounce
    interval: 500
    onTriggered: { root.refresh(); root.refreshCaps() }
  }

  // HDR auto-switching and VRR engagement are not announced as events.
  Timer {
    interval: 3000
    running: true
    repeat: true
    onTriggered: root.refresh()
  }

  Connections {
    target: Hyprland
    function onRawEvent(event) {
      var n = String(event && event.name ? event.name : "")
      // Not folded into topologyChanged: a TV that drops out and back within
      // the debounce leaves the same topology, but its workspaces have moved.
      if (n === "monitoradded" || n === "monitoraddedv2") root.outputAdded()
      if (n === "monitoradded" || n === "monitorremoved" || n === "monitoraddedv2"
          || n === "monitorremovedv2" || n === "configreloaded" || n === "fullscreen")
        debounce.restart()
    }
  }

  Component.onCompleted: { refresh(); refreshCaps() }
}
