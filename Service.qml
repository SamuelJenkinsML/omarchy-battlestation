import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "qml"

// Battlestation service: the single owner of all state, child processes and
// the IPC target. The bar widget is instantiated once per monitor and only
// reads from here (bar.shell.serviceFor), so nothing polls twice.
Item {
  id: root

  property var shell: null
  property var manifest: null

  readonly property string pluginId: "io.github.samueljenkinsml.battlestation"
  readonly property string pluginDir: decodeURIComponent(String(Qt.resolvedUrl(".")).replace(/^file:\/\//, ""))
  readonly property string cli: pluginDir + "bin/battlestation"
  readonly property string configPath: Quickshell.env("HOME") + "/.config/battlestation/config.toml"
  readonly property string statePath: Quickshell.env("HOME") + "/.local/state/battlestation/state.json"
  readonly property string streamStatePath: Quickshell.env("XDG_RUNTIME_DIR") + "/battlestation/stream.json"

  // ---- scene state (from `battlestation state`) ----
  property var sceneState: ({})
  readonly property string activeScene: sceneState.active || ""
  readonly property string previousScene: sceneState.previous || ""
  readonly property var scenes: sceneState.scenes || []
  readonly property var chordScenes: sceneState.chordScenes || []
  readonly property var pendingRevert: sceneState.pendingRevert || null
  readonly property var configErrors: sceneState.configErrors || []
  readonly property bool configured: scenes.length > 0
  property int revertRemaining: 0
  property bool busy: false
  property string busyLabel: ""
  property string lastError: ""
  property bool launchedBigPicture: false
  property int bigPictureReturnId: 0

  readonly property var activeSceneInfo: sceneByName(activeScene)

  // ---- streaming host (from the "stream" key of `battlestation state`) ----
  readonly property var stream: sceneState.stream || ({})
  readonly property bool streamAvailable: stream.available === true
  readonly property bool streamEnabled: stream.enabled === true
  readonly property bool streamAttached: stream.attached === true
  // Tailscale: whether it is on is tailscaled's state, so this follows `tailscale up|down` too.
  readonly property var remote: stream.remote || ({})
  readonly property bool remoteAvailable: remote.available === true
  readonly property bool remoteOn: remote.on === true
  readonly property string streamPhase: streamProc.running && streamProc.label !== "" ? "working" : (stream.phase || "off")

  // ---- live hardware state ----
  readonly property alias gpu: gpuSampler
  readonly property alias display: displayState
  readonly property alias controller: controllerState
  readonly property alias game: gameWatcher

  function sceneByName(name) {
    for (var i = 0; i < scenes.length; i++) if (scenes[i].name === name) return scenes[i]
    return null
  }

  function notify(title, body, urgency, glyph, execArgs) {
    var argv = ["omarchy-notification-send", "-g", glyph || "󰊗", "-u", urgency || "normal", title]
    if (body) argv.push(body)
    if (execArgs && execArgs.length) argv = argv.concat(["--exec"]).concat(execArgs)
    Util.execArgv(argv)
  }

  // ---- state refresh ----
  function refreshState() {
    if (!stateProc.running) stateProc.running = true
  }

  Process {
    id: stateProc
    command: [root.cli, "state"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(text)
          if (parsed.ok) {
            root.sceneState = parsed
            displayState.layout = parsed.layout || {}
          }
        } catch (e) {}
      }
    }
  }

  FileView {
    path: root.statePath
    watchChanges: true
    printErrors: false
    onFileChanged: stateDebounce.restart()
  }

  FileView {
    path: root.configPath
    watchChanges: true
    printErrors: false
    onFileChanged: {
      stateDebounce.restart()
      controllerState.restartDaemon()
    }
  }

  FileView {
    path: root.streamStatePath
    watchChanges: true
    printErrors: false
    onFileChanged: stateDebounce.restart()
  }

  Timer {
    id: stateDebounce
    interval: 250
    onTriggered: root.refreshState()
  }

  // ---- streaming host ----
  // Its own process, not actionProc: arming runs on every shell start and must
  // neither trip the busy gate nor raise a toast when there is nothing to arm.
  Process {
    id: streamProc
    property string label: ""   // empty = quiet (arm on start)
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var msg
        try { msg = JSON.parse(String(text || "").trim().split("\n").pop()) } catch (e) { msg = null }
        if (msg && !msg.ok && streamProc.label !== "")
          root.notify("Streaming: " + streamProc.label + " failed", (msg.error || "failed") + "\nRun `battlestation doctor`.", "critical", "󰅚")
      }
    }
    onExited: { streamProc.label = ""; root.refreshState() }
  }

  function runStream(label, args) {
    if (streamProc.running) return false
    streamProc.label = label
    streamProc.command = [cli, "stream"].concat(args)
    streamProc.running = true
    return true
  }

  function streamToggle() {
    if (!streamAvailable) {
      notify("Streaming is not set up", "Run `battlestation setup stream`.", "normal", "󰑈")
      return
    }
    if (streamEnabled) runStream("switching off", ["off"])
    else runStream("switching on", ["on"])
  }

  function remoteToggle() {
    if (!remoteAvailable) {
      notify("Tailscale is not set up", remote.hint || "Run `battlestation setup stream`.", "normal", "󰖂")
      return
    }
    if (remoteOn) runStream("leaving the tailnet", ["remote-off"])
    else runStream("joining the tailnet", ["remote-on"])
  }

  // The panic button: gives the desktop back to the real displays.
  function streamDetach() {
    Util.execArgv([cli, "stream", "detach", "--reason", "asked to"])
    stateDebounce.restart()
  }

  Process {
    id: watchdogProc
    command: [root.cli, "stream", "watchdog"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var msg = JSON.parse(text)
          if (msg.action === "detach") root.notify("Stream ended", "Displays restored (" + (msg.reason || "client left") + ").", "low", "󰑈")
          if (msg.action === "detach" || msg.action === "attach") root.refreshState()
        } catch (e) {}
      }
    }
  }

  // Sunshine skips `undo` when it crashes or the client just vanishes.
  Timer {
    interval: 15000
    repeat: true
    running: root.streamEnabled
    onTriggered: if (!watchdogProc.running && !streamProc.running) watchdogProc.running = true
  }

  // A display that goes away hands its workspaces and focus to whatever is
  // left, the parked virtual output included, and a TV back from standby does
  // not get them all back.
  Process {
    id: settleProc
    command: [root.cli, "stream", "settle"]
  }

  Timer {
    id: settleDebounce
    interval: 1000
    // A scene switch settles by itself, and arm is the one creating the output.
    onTriggered: {
      if (actionProc.running || streamProc.running) restart()
      else if (!settleProc.running) settleProc.running = true
    }
  }

  // ---- scene actions ----
  Process {
    id: actionProc
    property string label: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.actionFinished(actionProc.label, text)
    }
    onExited: {
      root.busy = false
      root.busyLabel = ""
      root.refreshState()
    }
  }

  function run(label, args) {
    if (actionProc.running) {
      notify("Battlestation is busy", root.busyLabel || "Try again in a moment", "low", "󰔟")
      return false
    }
    busy = true
    busyLabel = label
    lastError = ""
    actionProc.label = label
    actionProc.command = [cli].concat(args)
    actionProc.running = true
    return true
  }

  function actionFinished(label, text) {
    var msg
    try { msg = JSON.parse(String(text || "").trim().split("\n").pop()) } catch (e) { msg = null }
    if (!msg) return
    if (!msg.ok) {
      lastError = msg.error || "failed"
      notify("Battlestation: " + label + " failed", lastError, "critical", "󰅚")
      return
    }
    if (msg.pendingRevert) {
      var secs = msg.pendingRevert.seconds || 15
      notify("Keep this display layout?", "Reverting in " + secs + "s. Click to keep, or hold the controller chord.",
             "critical", "󰍹", ["omarchy-shell", root.pluginId, "keep"])
    }
    var steps = msg.steps || []
    for (var i = 0; i < steps.length; i++) {
      if (steps[i].step === "steam" && !steps[i].error) root.launchedBigPicture = true
      if (steps[i].step === "tv-input" && steps[i].error) notify("TV input not switched", steps[i].error, "normal", "󰔂")
    }
  }

  function enterScene(name) {
    if (!name) return
    var info = sceneByName(name)
    run("Switching to " + (info ? info.label : name), ["scene", "enter", name])
  }

  function toggleScene() {
    if (pendingRevert) { keep(); return }
    var pair = chordScenes
    if (pair.length === 0) return
    var target = pair.length > 1 && activeScene === pair[0] ? pair[1] : pair[0]
    if (target === activeScene && pair.length > 1) target = pair[1]
    enterScene(target)
  }

  function cycleScene() {
    if (scenes.length === 0) return
    var idx = -1
    for (var i = 0; i < scenes.length; i++) if (scenes[i].name === activeScene) idx = i
    enterScene(scenes[(idx + 1) % scenes.length].name)
  }

  function keep() {
    Util.execArgv([cli, "scene", "keep"])
    stateDebounce.restart()
  }

  function revert() {
    Util.execArgv([cli, "scene", "revert"])
    stateDebounce.restart()
  }

  function captureScene() {
    var name = "layout-" + Qt.formatDateTime(new Date(), "MMdd-hhmm")
    run("Saving layout", ["scene", "capture", name])
    notify("Saved current layout as '" + name + "'", "Rename it and set its actions in " + configPath, "normal", "󰆓")
  }

  function initConfig() {
    run("Creating config", ["init"])
  }

  function tvWake() {
    run("Waking TV", ["tv", "wake"])
  }

  function openConfig() {
    Util.execArgv(["omarchy-launch-editor", configPath])
  }

  function openDoctor() {
    Util.execArgv(["omarchy-launch-floating-terminal-with-presentation",
      "bash -c " + Util.shellQuote(cli + " doctor; echo; read -n1 -rsp 'Press any key to close'")])
  }

  // ---- Keep/Revert countdown (survives the panel closing; the deadline is on disk) ----
  Timer {
    interval: 1000
    repeat: true
    triggeredOnStart: true
    running: root.pendingRevert !== null
    onTriggered: {
      var left = Math.ceil(Number(root.pendingRevert.deadline) - Date.now() / 1000)
      root.revertRemaining = Math.max(0, left)
      if (left <= 0 && !revertProc.running) revertProc.running = true
    }
  }

  Process {
    id: revertProc
    command: [root.cli, "scene", "revert-if-due"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var msg = JSON.parse(text)
          if (msg.reverted) root.notify("Display layout reverted", "Not confirmed in time.", "normal", "󰑓")
        } catch (e) {}
        root.refreshState()
      }
    }
  }

  // ---- hardware ----
  GpuSampler { id: gpuSampler }

  DisplayState {
    id: displayState
    cli: root.cli
    ignoreOutput: root.stream.output || ""
    onOutputsChanged: if (root.streamEnabled && !root.streamAttached) settleDebounce.restart()
    onTopologyChanged: {
      // Attaching a stream switches the displays off, which is not a hotplug.
      if (root.streamAttached) return
      if (root.sceneState.autoSceneOnHotplug && !root.pendingRevert && !root.busy)
        root.run("Matching scene", ["scene", "auto"])
      // TV gone: light the parked stream output for Sunshine; TV back: switch it off.
      if (root.streamEnabled) root.runStream("", ["arm"])
    }
  }

  ControllerState {
    id: controllerState
    cli: root.cli
    onChord: function(pad) {
      if (root.pendingRevert) {
        root.keep()
        root.notify("Layout kept", "", "low", "󰄬")
        return
      }
      if (root.chordScenes.length === 0) {
        root.notify("No chord scenes configured", "Set chord_scenes in " + root.configPath, "normal", "󰊗")
        return
      }
      var pair = root.chordScenes
      var target = pair.length > 1 && root.activeScene === pair[0] ? pair[1] : pair[0]
      var info = root.sceneByName(target)
      root.notify("Switching to " + (info ? info.label : target), pad && pad.name ? "From " + pad.name : "", "low", info ? info.icon : "󰊗")
      root.enterScene(target)
    }
  }

  GameWatcher {
    id: gameWatcher
    cli: root.cli
    onBigPictureOpened: function(address) {
      var info = root.activeSceneInfo
      // While streaming the scene stays what it was, but Big Picture is the point.
      if (!root.streamAttached && (!info || info.steam !== "bigpicture")) return
      // Omarchy floats every class:steam window; Big Picture wants the whole screen.
      Util.execArgv(["hyprctl", "dispatch",
        "hl.dsp.window.fullscreen({ mode = \"fullscreen\", action = \"set\", layout_aware = false, window = \"address:0x" + address + "\" })"])
    }
    onBigPictureClosed: function(address) {
      var info = root.activeSceneInfo
      if (!info || info.steam !== "bigpicture" || !root.launchedBigPicture) return
      root.launchedBigPicture = false
      var back = root.previousScene && root.previousScene !== root.activeScene ? root.previousScene : ""
      if (!back) {
        var pair = root.chordScenes
        for (var i = 0; i < pair.length; i++) if (pair[i] !== root.activeScene) back = pair[i]
      }
      if (!back) return
      var backInfo = root.sceneByName(back)
      var label = backInfo ? backInfo.label : back
      if (info.onBigPictureExit === "return") {
        root.enterScene(back)
      } else if (info.onBigPictureExit === "ask") {
        root.notify("Back to " + label + "?", "Big Picture closed. Click to switch.", "normal",
                    backInfo ? backInfo.icon : "󰍹", ["omarchy-shell", root.pluginId, "scene", back])
      }
    }
  }

  // ---- in-game overlay (SUPER + ALT + H) ----
  // Pinned on or off; remembered in its own file so toggling it never
  // triggers a scene-state refresh.
  property bool osdVisible: false
  readonly property string osdPath: Quickshell.env("HOME") + "/.local/state/battlestation/osd.json"

  function setOsd(on) {
    osdVisible = on
    osdFile.setText(JSON.stringify({ visible: on }) + "\n")
  }

  FileView {
    id: osdFile
    path: root.osdPath
    printErrors: false
    onLoaded: {
      try { root.osdVisible = JSON.parse(text()).visible === true } catch (e) {}
    }
  }

  GameOsd {
    svc: root
    shown: root.osdVisible
  }

  Binding { target: displayState; property: "live"; value: root.osdVisible }

  // ---- IPC: omarchy-shell io.github.samueljenkinsml.battlestation <method> ----
  IpcHandler {
    target: root.pluginId

    function state(): string {
      var d = displayState.summaryFor("")
      return JSON.stringify({
        scene: root.activeScene,
        pendingRevert: root.pendingRevert,
        revertRemaining: root.revertRemaining,
        display: d,
        gpu: gpuSampler.available ? { name: gpuSampler.name, driver: gpuSampler.driver, temp: gpuSampler.temp,
               util: gpuSampler.util, memUsedMiB: gpuSampler.memUsed, memTotalMiB: gpuSampler.memTotal,
               powerW: gpuSampler.power } : null,
        controller: { connected: controllerState.connected, name: controllerState.name, battery: controllerState.battery },
        game: gameWatcher.currentGame, fps: gameWatcher.fps,
        osd: root.osdVisible
      })
    }
    function scene(name: string): void { root.enterScene(name) }
    function toggleScene(): void { root.toggleScene() }
    function cycleScene(): void { root.cycleScene() }
    function keep(): void { root.keep() }
    function revert(): void { root.revert() }
    function tvWake(): void { root.tvWake() }
    function capture(): void { root.captureScene() }
    function streamToggle(): void { root.streamToggle() }
    function streamOn(): void { if (!root.streamEnabled) root.streamToggle() }
    function streamOff(): void { if (root.streamEnabled) root.streamToggle() }
    function streamDetach(): void { root.streamDetach() }
    function remoteToggle(): void { root.remoteToggle() }
    function remoteOn(): void { if (!root.remoteOn) root.remoteToggle() }
    function remoteOff(): void { if (root.remoteOn) root.remoteToggle() }
    function streamState(): string { return JSON.stringify(root.stream) }
    function osdToggle(): void { root.setOsd(!root.osdVisible) }
    function osdShow(): void { root.setOsd(true) }
    function osdHide(): void { root.setOsd(false) }
    function osdState(): string { return root.osdVisible ? "visible" : "hidden" }
    function refresh(): void { root.refreshState(); displayState.refresh(); displayState.refreshCaps(); gameWatcher.rescan() }
  }

  Component.onCompleted: {
    refreshState()
    runStream("", ["arm"])  // no-op unless streaming is switched on; never ends a live stream
  }
}
