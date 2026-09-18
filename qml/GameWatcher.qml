import QtQuick
import Quickshell.Io
import Quickshell.Hyprland

// Running Steam games (steam_app_* window classes), Steam Big Picture, and live
// FPS from MangoHud logs while a game is up. Event parsing and the startup
// rescan follow nathanp/omarchy-game-awake (MIT, (c) 2026 Nathan Parikh).
Item {
  id: root

  required property string cli
  property var games: ({})          // address -> {cls, title, since}
  property string bigPictureAddress: ""
  property real fps: -1
  property real frametime: 0
  property var fpsHistory: []

  signal bigPictureOpened(string address)
  signal bigPictureClosed(string address)

  readonly property var gameList: Object.keys(games).map(function(k) { return games[k] })
  readonly property bool gameRunning: gameList.length > 0
  readonly property var currentGame: {
    var best = null
    for (var i = 0; i < gameList.length; i++)
      if (!best || gameList[i].since > best.since) best = gameList[i]
    return best
  }

  readonly property var bigPictureRe: /Big Picture|Gamepad UI|Steam Deck/i

  function normalize(addr) { return String(addr || "").toLowerCase().replace(/^0x/, "") }

  function parts(event, count) {
    try { if (event && event.parse) return event.parse(count) } catch (e) {}
    return String(event && event.data ? event.data : "").split(",")
  }

  function isGame(cls) { return /^steam_app_\d+$/i.test(String(cls || "")) }

  function isBigPicture(cls, title) {
    return String(cls || "").toLowerCase() === "steam" && bigPictureRe.test(String(title || ""))
  }

  function addWindow(addr, cls, title) {
    addr = normalize(addr)
    if (isGame(cls)) {
      var next = Object.assign({}, games)
      next[addr] = { cls: cls, title: title || cls, since: Date.now() }
      games = next
    } else if (isBigPicture(cls, title) && bigPictureAddress !== addr) {
      bigPictureAddress = addr
      root.bigPictureOpened(addr)
    }
  }

  function removeWindow(addr) {
    addr = normalize(addr)
    if (games[addr]) {
      var next = Object.assign({}, games)
      delete next[addr]
      games = next
    }
    if (addr === bigPictureAddress) {
      bigPictureAddress = ""
      root.bigPictureClosed(addr)
    }
  }

  function retitle(addr, title) {
    addr = normalize(addr)
    if (games[addr]) {
      var next = Object.assign({}, games)
      next[addr] = Object.assign({}, next[addr], { title: title })
      games = next
    } else if (addr === bigPictureAddress && !bigPictureRe.test(title)) {
      bigPictureAddress = ""
      root.bigPictureClosed(addr)
    } else if (addr !== bigPictureAddress && bigPictureRe.test(title) && windowClasses[addr] === "steam") {
      bigPictureAddress = addr
      root.bigPictureOpened(addr)
    }
  }

  property var windowClasses: ({})

  function rescan() { if (!clientsProc.running) clientsProc.running = true }

  Connections {
    target: Hyprland
    function onRawEvent(event) {
      var n = String(event && event.name ? event.name : "")
      if (n === "openwindow") {
        var o = root.parts(event, 4)
        var wc = Object.assign({}, root.windowClasses)
        wc[root.normalize(o[0])] = String(o[2] || "").toLowerCase()
        root.windowClasses = wc
        root.addWindow(o[0], o[2], o[3])
      } else if (n === "closewindow") {
        var c = root.parts(event, 1)
        root.removeWindow(c[0])
        var wc2 = Object.assign({}, root.windowClasses)
        delete wc2[root.normalize(c[0])]
        root.windowClasses = wc2
      } else if (n === "windowtitlev2") {
        var t = root.parts(event, 2)
        root.retitle(t[0], t[1])
      }
    }
  }

  Process {
    id: clientsProc
    command: ["hyprctl", "clients", "-j"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var list
        try { list = JSON.parse(text) } catch (e) { return }
        var g = ({})
        var wc = ({})
        var bp = ""
        for (var i = 0; i < list.length; i++) {
          var c = list[i]
          var addr = root.normalize(c.address)
          var cls = c["class"] || c.initialClass || ""
          wc[addr] = String(cls).toLowerCase()
          if (root.isGame(cls)) g[addr] = { cls: cls, title: c.title || cls, since: Date.now() }
          else if (root.isBigPicture(cls, c.title)) bp = addr
        }
        root.games = g
        root.windowClasses = wc
        root.bigPictureAddress = bp
      }
    }
  }

  Process {
    id: fpsProc
    command: [root.cli, "fps-tail"]
    running: false
    stdout: SplitParser {
      onRead: function(line) {
        var msg
        try { msg = JSON.parse(line) } catch (e) { return }
        if (msg.event !== "fps") return
        if (msg.fps === null || msg.fps === undefined) { root.fps = -1; return }
        root.fps = msg.fps
        root.frametime = msg.frametime || 0
        var h = root.fpsHistory.slice(Math.max(0, root.fpsHistory.length - 59))
        h.push(msg.fps)
        root.fpsHistory = h
      }
    }
  }

  onGameRunningChanged: {
    fpsProc.running = gameRunning
    if (!gameRunning) { fps = -1; fpsHistory = [] }
  }

  Component.onCompleted: rescan()
  Component.onDestruction: fpsProc.running = false
}
