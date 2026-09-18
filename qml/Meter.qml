import QtQuick
import qs.Commons

// Labelled horizontal meter, styled like the power panel's battery bar.
Item {
  id: root

  property string label: ""
  property string valueText: ""
  property real fraction: 0
  property real warn: 2          // fraction at/above which the fill turns urgent
  property real lowWarn: -1      // fraction at/below which the fill turns urgent
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  readonly property real clamped: Math.max(0, Math.min(1, fraction))
  readonly property bool hot: clamped >= warn || (lowWarn >= 0 && clamped <= lowWarn)

  width: parent ? parent.width : 0
  implicitHeight: labels.implicitHeight + Style.space(4) + track.height

  Item {
    id: labels
    width: parent.width
    implicitHeight: Math.max(l.implicitHeight, v.implicitHeight)
    Text {
      id: l
      text: root.label
      color: Qt.darker(root.foreground, 1.45)
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      id: v
      anchors.right: parent.right
      text: root.valueText
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }

  Rectangle {
    id: track
    anchors.top: labels.bottom
    anchors.topMargin: Style.space(4)
    width: parent.width
    height: Style.space(6)
    radius: height / 2
    color: Util.alpha(root.foreground, 0.12)

    Rectangle {
      height: parent.height
      radius: parent.radius
      width: Math.max(parent.height, parent.width * root.clamped)
      color: root.hot ? Color.urgent : Color.accent
      Behavior on width { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
      Behavior on color { ColorAnimation { duration: 220 } }
    }
  }
}
