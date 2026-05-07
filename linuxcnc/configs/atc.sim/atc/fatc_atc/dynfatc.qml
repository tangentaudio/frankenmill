import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Shapes 1.15

// DynFATC — FrankenMill ATC visualization widget
// Two-panel layout:
//   Top:    Top-down view — spindle nose, linear track, carousel ring
//   Bottom: Side profile  — spindle cross-section, Z position, tool holder

Rectangle {
    id: root
    color: bg_color
    clip: true

    // =====================================================================
    // Properties set from Python
    // =====================================================================
    property color  bg_color: "#1a1a2e"
    property int    pocketCount: 12
    property real   animDurationMs: 600   // carousel rotation anim speed
    property bool   mirrorLayout: false   // true = right-hand ATC

    // Live state (updated by Python signals)
    property real   carouselAngle: 0      // absolute degrees
    property bool   armExtended: false
    property int    spindleTool: 0        // 0 = empty
    property bool   isHomed: false
    property int    stateInt: 0           // fatc state enum
    property string stateText: "INIT"
    property bool   drawbarClamped: true
    property bool   drawbarUnclamped: false
    property real   zPosition: 0          // machine Z in mm
    property real   zSafeHeight: 0
    property real   zTcHeight: -1.5

    // Derived
    property bool   drawbarTransit: !drawbarClamped && !drawbarUnclamped
    property color  drawbarColor: drawbarClamped ? "#2ecc71"
                                : drawbarUnclamped ? "#e74c3c"
                                : "#f39c12"
    property color  stateColor: stateInt === 2  ? "#2ecc71"   // IDLE
                              : stateInt === 99 ? "#e74c3c"   // ERROR
                              : "#f39c12"                     // busy

    // Carousel geometry
    property real   carouselRadius: Math.min(topSection.height * 0.42,
                                             topSection.width * 0.25)
    property real   forkLength: carouselRadius * 0.18
    property real   forkWidth: 8
    property real   pocketCircleR: carouselRadius * 0.09
    property real   toolCircleR: carouselRadius * 0.09

    // =====================================================================
    // Top section — Top-down view (65% height)
    // =====================================================================
    Item {
        id: topSection
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: parent.height * 0.60

        // Transform for L/R hand orientation
        transform: Scale {
            xScale: mirrorLayout ? -1 : 1
            origin.x: topSection.width / 2
        }

        // --- Spindle nose (top-down, left side) ---
        Item {
            id: spindleNose
            width: 80
            height: 80
            anchors.left: parent.left
            anchors.leftMargin: 30
            anchors.verticalCenter: parent.verticalCenter

            // Un-mirror text if layout is mirrored
            transform: Scale {
                xScale: mirrorLayout ? -1 : 1
                origin.x: spindleNose.width / 2
            }

            // Housing ring
            Rectangle {
                anchors.fill: parent
                radius: width / 2
                color: "transparent"
                border.color: "#667788"
                border.width: 3
            }
            // Collet ring (inner)
            Rectangle {
                width: 52; height: 52
                anchors.centerIn: parent
                radius: width / 2
                color: "transparent"
                border.color: drawbarColor
                border.width: 3
            }
            // Tool indicator
            Rectangle {
                width: 36; height: 36
                anchors.centerIn: parent
                radius: width / 2
                color: spindleTool > 0 ? "#3498db" : "transparent"
                border.color: spindleTool > 0 ? "#5dade2" : "#445566"
                border.width: 2
                opacity: spindleTool > 0 ? 1.0 : 0.3

                Text {
                    anchors.centerIn: parent
                    text: spindleTool > 0 ? "T" + spindleTool : ""
                    color: "white"
                    font.pixelSize: 11
                    font.bold: true
                }
            }
            // Label
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.bottom
                anchors.topMargin: 4
                text: "SPINDLE"
                color: "#889999"
                font.pixelSize: 9
                font.bold: true
            }
        }

        // --- Linear track (rails) ---
        Item {
            id: trackArea
            anchors.left: spindleNose.right
            anchors.leftMargin: 10
            anchors.right: parent.right
            anchors.rightMargin: 20
            anchors.verticalCenter: parent.verticalCenter
            height: 20

            // Top rail
            Rectangle {
                width: parent.width; height: 2
                anchors.top: parent.top
                color: "#556677"
            }
            // Bottom rail
            Rectangle {
                width: parent.width; height: 2
                anchors.bottom: parent.bottom
                color: "#556677"
            }
            // Center line (dashed)
            Row {
                anchors.centerIn: parent
                spacing: 8
                Repeater {
                    model: Math.floor(trackArea.width / 14)
                    Rectangle {
                        width: 6; height: 1
                        color: "#334455"
                    }
                }
            }
        }

        // --- Carousel assembly (slides along track) ---
        Item {
            id: carouselAssembly
            width: carouselRadius * 2 + 40
            height: carouselRadius * 2 + 40
            anchors.verticalCenter: parent.verticalCenter

            // Slide position: retracted = right end of track, extended = toward spindle
            x: armExtended
               ? (spindleNose.x + spindleNose.width + 30)
               : (trackArea.x + trackArea.width - width + 10)

            Behavior on x {
                NumberAnimation { duration: 500; easing.type: Easing.InOutQuad }
            }

            // Carousel ring container (rotates)
            Item {
                id: carouselRing
                anchors.centerIn: parent
                width: carouselRadius * 2
                height: carouselRadius * 2
                rotation: -(carouselAngle + 180)  // active pocket faces left toward spindle

                Behavior on rotation {
                    RotationAnimation {
                        duration: animDurationMs
                        direction: RotationAnimation.Shortest
                        easing.type: Easing.InOutCubic
                    }
                }

                // Carousel body — simple circle
                Canvas {
                    id: ringCanvas
                    anchors.fill: parent
                    onPaint: {
                        var ctx = getContext("2d");
                        ctx.clearRect(0, 0, width, height);
                        var cx = width / 2;
                        var cy = height / 2;
                        var r = carouselRadius * 0.82;

                        // Main ring circle
                        ctx.beginPath();
                        ctx.arc(cx, cy, r, 0, Math.PI * 2);
                        ctx.strokeStyle = "#556677";
                        ctx.lineWidth = 3;
                        ctx.stroke();
                    }
                    Component.onCompleted: requestPaint()
                }

                // Pocket circles — empty = dashed outline, occupied = filled
                Repeater {
                    id: toolRepeater
                    model: pocketCount
                    Item {
                        id: pocketItem
                        property int pocketNum: index + 1
                        property int toolNum: 0
                        property real angle: index * 360 / pocketCount
                        property real pocketR: carouselRadius * 0.82

                        x: carouselRing.width/2 + pocketR * Math.cos(angle * Math.PI/180) - pocketCircleR
                        y: carouselRing.height/2 + pocketR * Math.sin(angle * Math.PI/180) - pocketCircleR
                        width: pocketCircleR * 2
                        height: pocketCircleR * 2

                        // Empty pocket — gray circle outline
                        Rectangle {
                            anchors.fill: parent
                            radius: width / 2
                            color: "transparent"
                            border.color: "#556677"
                            border.width: 1.5
                            visible: pocketItem.toolNum === 0
                            opacity: 0.6

                            Text {
                                anchors.centerIn: parent
                                rotation: carouselAngle + 180
                                text: "P" + pocketItem.pocketNum
                                color: "#778899"
                                font.pixelSize: 11
                            }
                        }

                        // Occupied pocket — filled blue circle
                        Rectangle {
                            anchors.fill: parent
                            radius: width / 2
                            color: "#3498db"
                            border.color: "#5dade2"
                            border.width: 1.5
                            visible: pocketItem.toolNum > 0
                            opacity: 0.9

                            Text {
                                anchors.centerIn: parent
                                rotation: carouselAngle + 180
                                text: "T" + pocketItem.toolNum
                                color: "white"
                                font.pixelSize: 11
                                font.bold: true
                            }
                        }
                    }
                }
            }

            // Active pocket indicator (points toward spindle)
            Rectangle {
                id: activeIndicator
                width: 8; height: 8
                radius: 4
                color: "#2ecc71"
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left
                anchors.leftMargin: -4
                visible: isHomed

                SequentialAnimation on opacity {
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.3; duration: 800 }
                    NumberAnimation { to: 1.0; duration: 800 }
                }
            }
        }

        // --- UNREFERENCED overlay ---
        Rectangle {
            anchors.fill: parent
            color: "#000000"
            opacity: isHomed ? 0 : 0.6
            visible: !isHomed

            Behavior on opacity {
                NumberAnimation { duration: 300 }
            }

            Text {
                anchors.centerIn: parent
                text: "UNREFERENCED"
                color: "#ff6b6b"
                font.pixelSize: 28
                font.bold: true
                font.letterSpacing: 4
                opacity: 1.0

                // Un-mirror if layout is mirrored
                transform: Scale {
                    xScale: mirrorLayout ? -1 : 1
                    origin.x: parent ? parent.width / 2 : 0
                }
            }
        }

        // Divider
        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: "#334455"
        }
    }

    // =====================================================================
    // Bottom section — Side profile view (40% height)
    // =====================================================================
    Item {
        id: bottomSection
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: topSection.bottom
        anchors.bottom: parent.bottom

        // Side profile aligned with spindle nose position above
        Item {
            id: sideProfile
            width: 160
            anchors.left: parent.left
            anchors.leftMargin: mirrorLayout
                ? (parent.width - spindleNose.x - spindleNose.width - 60)
                : (spindleNose.x - 10)
            anchors.top: parent.top
            anchors.topMargin: 10
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 10

            // Z reference lines
            property real zRange: Math.abs(zSafeHeight - zTcHeight)
            property real zScale: (height - 60) / (zRange > 0 ? zRange : 1)

            // Spindle housing (moves with Z)
            Item {
                id: spindleHousing
                width: parent.width
                height: 100
                x: 0
                y: 20 + (zSafeHeight - zPosition) * sideProfile.zScale

                Behavior on y {
                    NumberAnimation { duration: 400; easing.type: Easing.InOutQuad }
                }

                // Housing body
                Rectangle {
                    width: 50; height: 30
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    color: "#445566"
                    border.color: "#667788"
                    border.width: 1
                    radius: 2
                }

                // Taper / collet
                Canvas {
                    id: colletCanvas
                    width: 50; height: 35
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    anchors.topMargin: 28

                    onPaint: {
                        var ctx = getContext("2d");
                        ctx.clearRect(0, 0, width, height);

                        // Tapered collet body
                        ctx.beginPath();
                        ctx.moveTo(5, 0);
                        ctx.lineTo(15, height);
                        ctx.lineTo(35, height);
                        ctx.lineTo(45, 0);
                        ctx.closePath();
                        ctx.fillStyle = "#556677";
                        ctx.fill();
                        ctx.strokeStyle = "#778899";
                        ctx.lineWidth = 1;
                        ctx.stroke();
                    }
                    Component.onCompleted: requestPaint()
                }

                // Tool holder (visible when tool present)
                Rectangle {
                    width: 14; height: 25
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    anchors.topMargin: 60
                    color: spindleTool > 0 ? "#3498db" : "transparent"
                    border.color: spindleTool > 0 ? "#5dade2" : "#445566"
                    border.width: 1
                    radius: 2
                    opacity: spindleTool > 0 ? 1.0 : 0.3
                }

                // Tool tip (visible when tool present)
                Canvas {
                    width: 14; height: 12
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    anchors.topMargin: 83
                    visible: spindleTool > 0

                    onPaint: {
                        var ctx = getContext("2d");
                        ctx.clearRect(0, 0, width, height);
                        ctx.beginPath();
                        ctx.moveTo(0, 0);
                        ctx.lineTo(width/2, height);
                        ctx.lineTo(width, 0);
                        ctx.closePath();
                        ctx.fillStyle = "#3498db";
                        ctx.fill();
                    }
                    Component.onCompleted: requestPaint()
                }

                // Drawbar state indicator
                Rectangle {
                    width: 60; height: 16
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    anchors.topMargin: -18
                    radius: 3
                    color: "transparent"
                    border.color: drawbarColor
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: drawbarClamped ? "CLAMPED"
                            : drawbarUnclamped ? "OPEN"
                            : "..."
                        color: drawbarColor
                        font.pixelSize: 8
                        font.bold: true
                    }
                }

                // Tool label
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    anchors.topMargin: 98
                    text: spindleTool > 0 ? "T" + spindleTool : "Empty"
                    color: spindleTool > 0 ? "#5dade2" : "#667788"
                    font.pixelSize: 10
                    font.bold: true
                    visible: true
                }
            }

            // Z height reference marks
            // Safe height mark
            Rectangle {
                x: parent.width - 30
                y: 20
                width: 25; height: 1
                color: "#2ecc71"
                opacity: 0.6
            }
            Text {
                x: sideProfile.width - 5
                y: 14
                text: "Z0"
                color: "#2ecc71"
                font.pixelSize: 8
                opacity: 0.6
            }
            // TC height mark
            Rectangle {
                x: parent.width - 30
                y: 20 + sideProfile.zScale * sideProfile.zRange
                width: 25; height: 1
                color: "#e74c3c"
                opacity: 0.6
            }
            Text {
                x: sideProfile.width - 5
                y: 14 + sideProfile.zScale * sideProfile.zRange
                text: "TC"
                color: "#e74c3c"
                font.pixelSize: 8
                opacity: 0.6
            }
        }

        // Touch-off placeholder area (right of side profile)
        Rectangle {
            anchors.left: sideProfile.right
            anchors.leftMargin: 20
            anchors.right: parent.right
            anchors.rightMargin: 20
            anchors.top: parent.top
            anchors.topMargin: 10
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 10
            color: "transparent"
            border.color: "#334455"
            border.width: 1
            radius: 4

            Text {
                anchors.centerIn: parent
                text: "Touch-off Controls\n(future)"
                color: "#445566"
                font.pixelSize: 11
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    // =====================================================================
    // Functions called from Python via signals
    // =====================================================================
    function setCarouselAngle(angle) {
        carouselAngle = angle;
    }

    function setArmExtended(ext) {
        armExtended = ext;
    }

    function updatePocket(pocket, toolNum) {
        if (pocket >= 1 && pocket <= pocketCount) {
            toolRepeater.itemAt(pocket - 1).toolNum = toolNum;
        }
    }

    function setSpindleToolNum(toolNum) {
        spindleTool = toolNum;
    }

    function setDrawbarState(clamped, unclamped) {
        drawbarClamped = clamped;
        drawbarUnclamped = unclamped;
    }

    function setZPosition(z) {
        zPosition = z;
    }

    function setZHeights(safe, tc) {
        zSafeHeight = safe;
        zTcHeight = tc;
    }

    function setHomedState(homed) {
        isHomed = homed;
    }

    function setMachineState(si, text) {
        stateInt = si;
        stateText = text;
    }

    function setPocketCount(count) {
        pocketCount = count;
        ringCanvas.requestPaint();
    }

    function setMirror(mirror) {
        mirrorLayout = mirror;
    }

    // Repaint carousel when pocket count changes
    onPocketCountChanged: ringCanvas.requestPaint()
}
