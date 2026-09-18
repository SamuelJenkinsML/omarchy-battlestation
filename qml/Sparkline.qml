import QtQuick
import qs.Commons

// Filled line graph of recent samples.
Canvas {
  id: root

  property var values: []
  property real maxValue: 0     // 0 = scale to the data
  property color color: Color.accent

  onValuesChanged: requestPaint()
  onColorChanged: requestPaint()
  onWidthChanged: requestPaint()
  onHeightChanged: requestPaint()

  onPaint: {
    var ctx = getContext("2d")
    ctx.reset()
    var n = values ? values.length : 0
    if (n < 2 || width <= 0 || height <= 0) return
    var top = maxValue > 0 ? maxValue : Math.max.apply(null, values) * 1.15
    if (top <= 0) top = 1
    var step = width / (n - 1)
    var y = function(v) { return height - Math.max(0, Math.min(1, v / top)) * (height - 2) - 1 }

    ctx.beginPath()
    ctx.moveTo(0, height)
    for (var i = 0; i < n; i++) ctx.lineTo(i * step, y(values[i]))
    ctx.lineTo(width, height)
    ctx.closePath()
    ctx.fillStyle = Qt.rgba(color.r, color.g, color.b, 0.16)
    ctx.fill()

    ctx.beginPath()
    for (var j = 0; j < n; j++) {
      if (j === 0) ctx.moveTo(0, y(values[0]))
      else ctx.lineTo(j * step, y(values[j]))
    }
    ctx.lineWidth = Math.max(1, Style.space(2) / 1.5)
    ctx.strokeStyle = color
    ctx.stroke()
  }
}
