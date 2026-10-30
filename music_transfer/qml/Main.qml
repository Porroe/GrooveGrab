import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: win
    width: 1380; height: 900; minimumWidth: 1000; minimumHeight: 650
    visible: true; title: "GrooveGrab"
    property bool dark: backend.theme === "dark" || (backend.theme === "system" && Qt.styleHints.colorScheme === Qt.Dark)
    property color bg: dark ? "#11141b" : "#f4f6fa"
    property color panel: dark ? "#1c212c" : "#ffffff"
    property color ink: dark ? "#eef1fa" : "#202638"
    property color muted: dark ? "#a1abc0" : "#647089"
    property color accent: dark ? "#b0a2ff" : "#6550cc"
    property var selectedAlbum: null
    property var selectedTracks: []
    property string artistFilter: "All artists"
    property string conflictKind: "same_path"
    property string formatCandidatePath: ""
    property var formatCandidatePaths: []
    property bool folderMode: false
    property bool shuttingDown: false
    onClosing: function(event) { if (!backend.shutdownComplete) { event.accepted = false; shuttingDown = true; backend.shutdown() } }
    onArtistFilterChanged: backend.setFilter(search.text, artistFilter)
    property var expandedAlbums: ({})
    property var selectedAlbums: []
    property var collapsedFolders: ({})
    function toggleAlbumSelection(id) {
        let ids = win.selectedAlbums.slice()
        if (ids.indexOf(id) >= 0) ids = ids.filter(item => item !== id)
        else ids.push(id)
        win.selectedAlbums = ids
    }
    function showAlbumTracks(album) {
        win.selectedAlbum = album
        win.selectedTracks = []
        backend.openAlbum(album.id)
        albumDialog.open()
    }
    font.pixelSize: 14
    color: bg
    palette.window: bg; palette.windowText: ink; palette.base: panel; palette.text: ink
    palette.placeholderText: muted; palette.button: panel; palette.buttonText: ink; palette.highlight: accent; palette.highlightedText: bg

    component Action: Button {
        id: action
        padding: 11
        background: Rectangle { radius: 9; color: action.down ? win.accent : action.hovered ? (win.dark ? "#333b50" : "#e8e5f5") : win.panel; border.color: win.dark ? "#374057" : "#dce1ed" }
        contentItem: Text { text: action.text; color: action.enabled ? win.ink : win.muted; font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
    }
    component Sheet: Dialog {
        padding: 22
        background: Rectangle { radius: 16; color: win.panel; border.color: win.dark ? "#374057" : "#dce1ed" }
    }
    component Entry: TextField {
        padding: 12
        color: win.ink; placeholderTextColor: win.muted
        background: Rectangle { radius: 9; color: win.panel; border.color: parent.activeFocus ? win.accent : (win.dark ? "#374057" : "#dce1ed") }
    }
    component Picker: ComboBox {
        id: picker
        implicitWidth: 150
        implicitHeight: 40
        contentItem: Text {
            leftPadding: 10
            rightPadding: picker.indicator.width + picker.spacing
            text: picker.displayText
            font: picker.font
            color: win.ink
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle { radius: 9; color: win.panel; border.color: win.dark ? "#374057" : "#dce1ed" }
        delegate: ItemDelegate {
            objectName: "pickerOption"
            width: picker.width
            highlighted: picker.highlightedIndex === index
            contentItem: Text {
                objectName: "pickerOptionText"
                text: picker.textAt(index)
                color: win.ink
                font: picker.font
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                color: parent.highlighted
                    ? (win.dark ? "#39334f" : "#e8e5f5")
                    : win.panel
            }
        }
        popup: Popup {
            objectName: "pickerPopup"
            y: picker.height - 1
            width: picker.width
            implicitHeight: Math.min(contentItem.implicitHeight, 260)
            padding: 1
            contentItem: ListView {
                objectName: "pickerOptionsList"
                clip: true
                implicitHeight: contentHeight
                model: picker.popup.visible ? picker.delegateModel : null
                currentIndex: picker.highlightedIndex
                ScrollIndicator.vertical: ScrollIndicator {}
            }
            background: Rectangle {
                objectName: "pickerPopupBackground"
                color: win.panel
                border.color: win.dark ? "#59647b" : "#aeb8ca"
                radius: 3
            }
        }
    }
    component Meter: ProgressBar {
        implicitHeight: 6
        background: Rectangle { implicitHeight: 6; radius: 3; color: win.dark ? "#333b50" : "#e8e5f5" }
        contentItem: Item { implicitHeight: 6; Rectangle { width: parent.width * parent.parent.visualPosition; height: parent.height; radius: 3; color: win.accent } }
    }
    component Caption: Label { color: win.muted; font.pixelSize: 12; elide: Text.ElideMiddle }

    FolderDialog { id: sourcePicker; title: "Choose music library"; onAccepted: backend.chooseFolder(selectedFolder.toString(), true) }
    FolderDialog { id: targetPicker; title: "Choose destination"; onAccepted: backend.chooseFolder(selectedFolder.toString(), false) }
    Connections {
        target: backend
        function onLibraryReset() { win.selectedAlbums = []; win.selectedAlbum = null; win.selectedTracks = []; win.artistFilter = "All artists"; search.text = ""; artistPicker.currentIndex = 0 }
        function onNotify(message) { notice.text = message; toast.open() }
        function onConflict(path) { win.conflictKind = "same_path"; conflictPath.text = path; applyAll.checked = false; conflictDialog.open() }
        function onFormatConflict(kind, path, candidates) {
            var paths = []
            for (var i = 0; i < candidates.length; i++) paths.push(candidates[i])
            win.conflictKind = kind
            conflictPath.text = path
            win.formatCandidatePaths = paths
            win.formatCandidatePath = paths.length ? paths[0] : ""
            conflictDialog.open()
        }
        function onChanged() {
            if ((!backend.running || backend.conflictCount === 0) && conflictDialog.visible) conflictDialog.close()
        }
    }
    Connections {
        target: backend.artists
        function onCountChanged() { if (artistPicker.currentIndex < 0 && backend.artists.count > 0) artistPicker.currentIndex = 0 }
    }
    Component.onCompleted: { if (backend.source) backend.rescan() }

    ColumnLayout {
        enabled: !win.shuttingDown
        anchors.fill: parent; anchors.margins: 24; spacing: 18
        RowLayout {
            Layout.fillWidth: true
            Rectangle { width: 44; height: 44; radius: 13; color: win.accent; Label { anchors.centerIn: parent; text: "♫"; font.pixelSize: 28; color: win.bg } }
            ColumnLayout { spacing: 2
                Label { text: "GrooveGrab"; color: win.ink; font.pixelSize: 25; font.bold: true }
                Caption { text: "Your library. Ready to go." }
            }
            Item { Layout.fillWidth: true }
            Picker { objectName: "themePicker"; model: ["System theme", "Light theme", "Dark theme"]; currentIndex: backend.theme === "light" ? 1 : backend.theme === "dark" ? 2 : 0; onActivated: backend.setTheme(["system", "light", "dark"][currentIndex]) }
            Action { text: "Settings"; onClicked: settingsDialog.open() }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 14
            Rectangle { Layout.fillWidth: true; height: 76; radius: 12; color: win.panel
                RowLayout { anchors.fill: parent; anchors.margins: 14
                    ColumnLayout { Layout.fillWidth: true; Caption { text: "SOURCE LIBRARY · READ ONLY" } Label { Layout.fillWidth: true; text: backend.source || "Choose your music folder"; color: win.ink; elide: Text.ElideMiddle } }
                    Action { text: "Browse"; enabled: !backend.running && !backend.scanning; onClicked: sourcePicker.open() }
                }
            }
            Label { text: "→"; font.pixelSize: 24; color: win.muted }
            Rectangle { Layout.fillWidth: true; height: 76; radius: 12; color: win.panel
                RowLayout { anchors.fill: parent; anchors.margins: 14
                    ColumnLayout { Layout.fillWidth: true; Caption { text: "DESTINATION" } Label { Layout.fillWidth: true; text: backend.target || "Choose an SD card, USB drive, or folder"; color: win.ink; elide: Text.ElideMiddle } }
                    Action { text: "Browse"; enabled: !backend.running && !backend.scanning; onClicked: targetPicker.open() }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 20
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 14
                RowLayout {
                    Entry { id: search; implicitHeight: 40; Layout.fillWidth: true; placeholderText: "Search albums, artists, or songs…"; selectByMouse: true; onTextChanged: backend.setFilter(text, win.artistFilter) }
                    Picker {
                        id: artistPicker; objectName: "artistPicker"
                        Layout.preferredWidth: 160
                        model: backend.artists; textRole: "display"
                        onActivated: win.artistFilter = currentText
                    }
                    Action { text: win.folderMode ? "Albums" : "Folders"; onClicked: win.folderMode = !win.folderMode }
                    Action { text: "↻"; enabled: !backend.running && !backend.scanning; onClicked: backend.rescan(); ToolTip.text: "Rescan library"; ToolTip.visible: hovered }
                }
                RowLayout {
                    Action { visible: win.selectedAlbums.length > 0; text: "Send selected albums (" + win.selectedAlbums.length + ")"; enabled: !!backend.target; onClicked: { backend.sendAlbums(win.selectedAlbums); win.selectedAlbums = [] } }
                    Label { text: backend.albums.count + " albums"; color: win.muted }
                }
                RowLayout {
                    Layout.fillWidth: true
                    BusyIndicator { running: backend.scanning && backend.libraryTotal === 0; visible: running; implicitWidth: 26; implicitHeight: 26 }
                    Caption { Layout.fillWidth: true; text: backend.libraryStatus; elide: Text.ElideRight }
                    Action { text: "Cancel refresh"; visible: backend.scanning; onClicked: backend.cancelScan() }
                }
                Meter { Layout.fillWidth: true; visible: backend.scanning && backend.libraryTotal > 0; value: backend.libraryProgress }
                GridView {
                    id: grid; objectName: "albumGrid"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; visible: !win.folderMode
                    cacheBuffer: 0
                    cellWidth: Math.floor(width / Math.max(1, Math.floor(width / 190))); cellHeight: cellWidth + 70
                    model: backend.albums
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Item {
                        required property var modelData
                        width: grid.cellWidth; height: grid.cellHeight
                        Component.onCompleted: backend.requestCover(modelData.id)
                        onModelDataChanged: if (!modelData.cover) backend.requestCover(modelData.id)
                        Rectangle {
                            anchors.fill: parent; anchors.rightMargin: 14; anchors.bottomMargin: 14; radius: 14; color: win.panel
                            Column {
                                anchors.fill: parent; anchors.margins: 10; spacing: 7
                                Rectangle {
                                    width: parent.width; height: width; radius: 9
                                    color: win.dark ? "#2a3143" : "#eeebf8"; clip: true
                                    Label { anchors.centerIn: parent; text: "♫"; font.pixelSize: 62; color: win.accent; visible: !modelData.cover }
                                    Image { anchors.fill: parent; source: modelData.cover || ""; fillMode: Image.PreserveAspectFit; asynchronous: true }
                                    MouseArea {
                                        objectName: "albumArtworkClickArea"
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: win.toggleAlbumSelection(modelData.id)
                                        ToolTip.visible: containsMouse
                                        ToolTip.text: "Select album"
                                        hoverEnabled: true
                                    }
                                }
                                Item {
                                    width: parent.width; height: albumLabels.implicitHeight
                                    Column {
                                        id: albumLabels
                                        width: parent.width; spacing: 2
                                        Label { width: parent.width; text: modelData.title; color: win.ink; font.bold: true; elide: Text.ElideRight }
                                        Caption { width: parent.width; text: modelData.artist }
                                        Caption { objectName: "albumTrackCount"; width: parent.width; text: modelData.track_count + (modelData.track_count === 1 ? " track" : " tracks") }
                                    }
                                    MouseArea {
                                        objectName: "albumDetailsClickArea"
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: win.showAlbumTracks(modelData)
                                        ToolTip.visible: containsMouse
                                        ToolTip.text: "Show tracks"
                                        hoverEnabled: true
                                    }
                                }
                            }
                            CheckBox {
                                objectName: "albumSelectionCheckbox"
                                anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8
                                checked: win.selectedAlbums.indexOf(modelData.id) >= 0
                                onToggled: win.toggleAlbumSelection(modelData.id)
                            }
                        border.width: activeFocus || win.selectedAlbums.indexOf(modelData.id) >= 0 ? 2 : 0; border.color: win.accent
                        activeFocusOnTab: true
                        Keys.onReturnPressed: win.showAlbumTracks(modelData)
                        }
                    }
                    Label { anchors.centerIn: parent; visible: !backend.albums.count; text: backend.scanning ? "Reading music tags…" : "Choose a music library to see your albums"; color: win.muted }
                }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; visible: win.folderMode; clip: true
                    model: backend.folders
                    spacing: 6
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: ListView.view.width; height: 48; color: win.panel; radius: 8
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 8
                            ToolButton { text: modelData.expanded ? "▾" : "▸"; onClicked: backend.toggleFolder(modelData.path) }
                            Label { Layout.fillWidth: true; leftPadding: modelData.depth * 16; text: modelData.name; color: win.ink; elide: Text.ElideMiddle; ToolTip.text: modelData.path; ToolTip.visible: folderHover.hovered; HoverHandler { id: folderHover } }
                            Action { text: "Send folder"; enabled: !!backend.target; onClicked: backend.sendFolder(modelData.path) }
                        }
                    }
                }
            }
            Rectangle {
                Layout.preferredWidth: 330; Layout.fillHeight: true; radius: 16; color: win.panel
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 12
                    RowLayout { Label { text: "Transfer queue"; font.pixelSize: 19; font.bold: true; color: win.ink; Layout.fillWidth: true } Action { text: "Clear"; enabled: backend.running; onClicked: backend.cancelAll() } }
                    Caption { Layout.fillWidth: true; text: backend.status; wrapMode: Text.Wrap; elide: Text.ElideNone }
                    Meter { Layout.fillWidth: true; value: backend.progress; visible: backend.running }
                    Action { text: "Resolve conflicts (" + backend.conflictCount + ")"; visible: backend.conflictCount > 0; onClicked: backend.showConflicts() }
                    Action { text: "Resume transfer"; visible: backend.paused; onClicked: backend.resume() }
                    ListView {
                        id: queueList; objectName: "queueList"
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                        model: backend.queue; spacing: 8
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Column {
                            required property var modelData
                            width: ListView.view.width; spacing: 6
                            RowLayout {
                                width: parent.width
                                ToolButton { visible: modelData.kind === "album"; text: modelData.expanded ? "▾" : "▸"; onClicked: backend.toggleQueueAlbum(modelData.album_id) }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Label { Layout.fillWidth: true; text: modelData.title; color: win.ink; font.bold: modelData.kind === "album"; font.pixelSize: modelData.kind === "album" ? 14 : 12; elide: Text.ElideRight; leftPadding: modelData.kind === "track" ? 12 : 0 }
                                    Caption { Layout.fillWidth: true; visible: modelData.kind === "album"; text: modelData.subtitle || "" }
                                }
                                Caption { visible: modelData.kind === "track"; text: modelData.state || "" }
                                ToolButton { text: "×"; onClicked: backend.remove(modelData.kind === "album" ? modelData.album_id : modelData.id, modelData.kind === "album"); ToolTip.text: modelData.kind === "album" ? "Remove album" : "Cancel track"; ToolTip.visible: hovered }
                            }
                            Meter { width: parent.width; visible: modelData.kind === "album" || modelData.state === "Copying"; value: modelData.fraction }
                        }
                        Caption { visible: !backend.queue.count; width: parent.width; text: "Send an album or song to start.\nYour upcoming transfers will appear here."; wrapMode: Text.Wrap; elide: Text.ElideNone }
                    }
                    Action { Layout.fillWidth: true; text: "Transfer history (" + backend.results.count + ")"; onClicked: historyDialog.open() }
                }
            }
        }
        Caption { text: "Original files stay untouched  ·  Audio copied without conversion" }
    }

    Sheet {
        id: albumDialog; objectName: "albumDialog"; anchors.centerIn: parent
        width: Math.min(win.width - 64, 680); height: Math.min(win.height - 64, 820); modal: true
        ColumnLayout {
            anchors.fill: parent
            Rectangle {
                objectName: "albumHeader"
                Layout.fillWidth: true; Layout.preferredHeight: 140
                radius: 14
                color: win.dark ? "#252b39" : "#f0eef8"
                RowLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 18
                    Rectangle {
                        objectName: "albumHeaderArtworkFrame"
                        Layout.preferredWidth: 112; Layout.preferredHeight: 112
                        radius: 10; clip: true
                        color: win.dark ? "#353d52" : "#e2dff0"
                        Label {
                            anchors.centerIn: parent
                            text: "♫"; font.pixelSize: 44; color: win.accent
                            visible: albumHeaderArtwork.status !== Image.Ready
                        }
                        Image {
                            id: albumHeaderArtwork
                            objectName: "albumHeaderArtwork"
                            anchors.fill: parent
                            source: backend.selectedAlbumCover
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 7
                        Label {
                            objectName: "albumHeaderTitle"
                            Layout.fillWidth: true
                            text: win.selectedAlbum ? win.selectedAlbum.title : ""
                            color: win.ink; font.pixelSize: 23; font.bold: true
                            elide: Text.ElideRight
                        }
                        Label {
                            objectName: "albumHeaderArtist"
                            Layout.fillWidth: true
                            text: win.selectedAlbum ? win.selectedAlbum.artist : ""
                            color: win.muted; font.pixelSize: 15
                            elide: Text.ElideRight
                        }
                        Caption {
                            text: backend.tracks.count + (backend.tracks.count === 1 ? " track" : " tracks")
                        }
                    }
                }
            }
            ListView {
                objectName: "albumTracksList"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: backend.tracks
                delegate: CheckDelegate {
                    objectName: "albumTrackItem"
                    required property var modelData
                    width: ListView.view.width
                    text: modelData.display_title || modelData.title
                    checked: win.selectedTracks.indexOf(modelData.path) >= 0
                    onToggled: { let paths = win.selectedTracks.slice(); if (checked) paths.push(modelData.path); else paths = paths.filter(p => p !== modelData.path); win.selectedTracks = paths }
                }
                ScrollBar.vertical: ScrollBar {}
            }
            RowLayout {
                Action { text: "Send album"; enabled: !!backend.target; onClicked: { backend.send(win.selectedAlbum.id, []); albumDialog.close() } }
                Action { text: "Send selected (" + win.selectedTracks.length + ")"; enabled: !!backend.target && win.selectedTracks.length > 0; onClicked: { backend.send(win.selectedAlbum.id, win.selectedTracks); albumDialog.close() } }
                Item { Layout.fillWidth: true }
                Action { text: "Close"; onClicked: albumDialog.close() }
            }
        }
    }
    Sheet {
        id: settingsDialog; objectName: "settingsDialog"; anchors.centerIn: parent
        property string category: "Files"
        property string draftConflictPolicy: "ask_every_time"
        property string draftFormatPolicy: "ask_every_time"
        property string draftSameFormatPolicy: "ask_every_time"
        width: Math.min(win.width - 80, 900); height: Math.min(win.height - 80, 580); modal: true
        function resetDrafts() {
            draftConflictPolicy = backend.conflictPolicy
            draftFormatPolicy = backend.formatPolicy
            draftSameFormatPolicy = backend.sameFormatPolicy
            artEnabled.checked = backend.artwork.enabled
            artName.text = backend.artwork.filename
            artWidth.value = backend.artwork.width
            artHeight.value = backend.artwork.height
        }
        onOpened: resetDrafts()
        header: RowLayout {
            Label { Layout.fillWidth: true; leftPadding: 12; text: "Settings"; color: win.ink; font.pixelSize: 20; font.bold: true }
            ToolButton {
                objectName: "settingsDismiss"
                text: "×"; font.pixelSize: 24
                ToolTip.text: "Close settings"; ToolTip.visible: hovered
                onClicked: settingsDialog.close()
            }
        }
        ColumnLayout {
            anchors.fill: parent; spacing: 0
            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
                ColumnLayout {
                    objectName: "settingsCategories"
                    Layout.preferredWidth: 190; Layout.fillHeight: true
                    spacing: 8
                    Button {
                        objectName: "settingsFilesCategory"
                        Layout.fillWidth: true
                        text: "Files"
                        onClicked: settingsDialog.category = "Files"
                        background: Rectangle {
                            radius: 8
                            color: settingsDialog.category === "Files" ? (win.dark ? "#333b50" : "#e8e5f5") : "transparent"
                        }
                        contentItem: Text {
                            text: parent.text; color: win.ink; font.pixelSize: 14
                            horizontalAlignment: Text.AlignLeft; verticalAlignment: Text.AlignVCenter
                            leftPadding: 12
                        }
                    }
                    Button {
                        objectName: "settingsArtworkCategory"
                        Layout.fillWidth: true
                        text: "Artwork"
                        onClicked: settingsDialog.category = "Artwork"
                        background: Rectangle {
                            radius: 8
                            color: settingsDialog.category === "Artwork" ? (win.dark ? "#333b50" : "#e8e5f5") : "transparent"
                        }
                        contentItem: Text {
                            text: parent.text; color: win.ink; font.pixelSize: 14
                            horizontalAlignment: Text.AlignLeft; verticalAlignment: Text.AlignVCenter
                            leftPadding: 12
                        }
                    }
                    Item { Layout.fillHeight: true }
                }
                Rectangle {
                    Layout.fillHeight: true; width: 1
                    color: win.dark ? "#374057" : "#dce1ed"
                }
                StackLayout {
                    objectName: "settingsOptions"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    Layout.leftMargin: 20
                    currentIndex: settingsDialog.category === "Files" ? 0 : 1
                    ColumnLayout {
                        objectName: "settingsFilesPane"
                        spacing: 12
                        Label { text: "Files"; color: win.ink; font.pixelSize: 17; font.bold: true }
                        Label { text: "When a file already exists"; color: win.ink; wrapMode: Text.Wrap }
                        Picker {
                            id: conflictPolicyPicker; objectName: "conflictPolicyPicker"
                            Layout.fillWidth: true
                            model: ["Always Skip", "Always Overwrite", "Overwrite if size is different", "Ask if size is different", "Ask every time"]
                            currentIndex: ["always_skip", "always_overwrite", "overwrite_if_size_different", "ask_if_size_different", "ask_every_time"].indexOf(settingsDialog.draftConflictPolicy)
                            onActivated: settingsDialog.draftConflictPolicy = ["always_skip", "always_overwrite", "overwrite_if_size_different", "ask_if_size_different", "ask_every_time"][currentIndex]
                        }
                        Label { text: "When the same track exists in a different format"; color: win.ink; wrapMode: Text.Wrap }
                        Picker {
                            id: formatPolicyPicker; objectName: "formatPolicyPicker"
                            Layout.fillWidth: true
                            model: ["Always Copy", "Copy and delete existing", "Keep higher bitrate", "Keep lower bitrate", "Ask every time"]
                            currentIndex: ["always_copy", "copy_and_delete", "keep_higher_bitrate", "keep_lower_bitrate", "ask_every_time"].indexOf(settingsDialog.draftFormatPolicy)
                            onActivated: settingsDialog.draftFormatPolicy = ["always_copy", "copy_and_delete", "keep_higher_bitrate", "keep_lower_bitrate", "ask_every_time"][currentIndex]
                        }
                        Label { text: "When the same track exists with the same format but a different filename"; color: win.ink; wrapMode: Text.Wrap }
                        Picker {
                            id: sameFormatPolicyPicker; objectName: "sameFormatPolicyPicker"
                            Layout.fillWidth: true
                            model: ["Always Copy", "Keep Existing", "Replace Existing", "Ask Every Time"]
                            currentIndex: ["always_copy", "keep_existing", "replace_existing", "ask_every_time"].indexOf(settingsDialog.draftSameFormatPolicy)
                            onActivated: settingsDialog.draftSameFormatPolicy = ["always_copy", "keep_existing", "replace_existing", "ask_every_time"][currentIndex]
                        }
                        Item { Layout.fillHeight: true }
                        Action { text: "Clear thumbnail cache"; onClicked: backend.clearCache() }
                    }
                    ColumnLayout {
                        objectName: "settingsArtworkPane"
                        spacing: 12
                        Label { text: "Artwork"; color: win.ink; font.pixelSize: 17; font.bold: true }
                        CheckBox { id: artEnabled; text: "Generate artwork on destination"; checked: backend.artwork.enabled }
                        Label { text: "Filename"; color: win.ink }
                        Entry { id: artName; Layout.fillWidth: true; text: backend.artwork.filename }
                        Label { text: "Maximum dimensions (pixels)"; color: win.ink }
                        RowLayout {
                            Layout.fillWidth: true
                            SpinBox { id: artWidth; Layout.fillWidth: true; from: 1; to: 16384; value: backend.artwork.width; editable: true }
                            Label { text: "×"; color: win.ink }
                            SpinBox { id: artHeight; Layout.fillWidth: true; from: 1; to: 16384; value: backend.artwork.height; editable: true }
                        }
                        Caption { Layout.fillWidth: true; text: "Preserves proportions. Never enlarges smaller images."; wrapMode: Text.Wrap }
                        Item { Layout.fillHeight: true }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.topMargin: 12
                Item { Layout.fillWidth: true }
                Action {
                    objectName: "settingsCancel"
                    text: "Cancel"
                    onClicked: { settingsDialog.resetDrafts(); settingsDialog.close() }
                }
                Action {
                    objectName: "settingsSave"
                    text: "Save settings"
                    onClicked: {
                        if (backend.saveSettings(settingsDialog.draftConflictPolicy,
                                                 settingsDialog.draftFormatPolicy,
                                                 settingsDialog.draftSameFormatPolicy,
                                                 artEnabled.checked, artName.text,
                                                 artWidth.value, artHeight.value))
                            settingsDialog.close()
                    }
                }
            }
        }
    }
    Sheet {
        id: conflictDialog; objectName: "conflictDialog"; anchors.centerIn: parent; width: 660; height: 340; modal: false
        title: win.conflictKind === "same_path" ? "A file already exists"
            : win.conflictKind === "missing_number" ? "Confirm matching track"
            : win.conflictKind === "same_format" ? "Same track and format already exists"
            : "Same track in a different format"
        closePolicy: Popup.NoAutoClose
        ColumnLayout {
            anchors.fill: parent
            Label { id: conflictPath; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; color: win.ink }
            Label {
                Layout.fillWidth: true
                visible: win.conflictKind !== "same_path" && win.formatCandidatePaths.length > 0
                text: win.formatCandidatePaths.join("\n")
                wrapMode: Text.WrapAnywhere
                color: win.muted
            }
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: win.conflictKind === "same_path" ? "Other transfers continue while this file waits."
                    : win.conflictKind === "missing_number" ? "Album, artist, and title match, but a track number is missing. Is this the same track?"
                    : win.conflictKind === "same_format" ? "Album, artist, title, and track number match a file in the same format with a different filename."
                    : win.conflictKind === "bitrate" ? "A bitrate could not be determined. Choose how to handle the existing version."
                    : "Choose what to do with the matching destination version."
            }
            CheckBox { id: applyAll; visible: win.conflictKind === "same_path"; text: "Apply to all conflicts in this queue run" }
            RowLayout {
                Action { visible: win.conflictKind === "same_path"; text: "Skip"; onClicked: { conflictDialog.close(); backend.resolveConflict(conflictPath.text, false, applyAll.checked) } }
                Action { visible: win.conflictKind === "same_path"; text: "Overwrite"; onClicked: { conflictDialog.close(); backend.resolveConflict(conflictPath.text, true, applyAll.checked) } }
                Action { visible: win.conflictKind === "missing_number"; text: "Same track"; onClicked: { conflictDialog.close(); backend.resolveFormatConflict(conflictPath.text, "same", win.formatCandidatePath) } }
                Action { visible: win.conflictKind === "missing_number"; text: "Different track"; onClicked: { conflictDialog.close(); backend.resolveFormatConflict(conflictPath.text, "different", win.formatCandidatePath) } }
                Action { objectName: "formatKeepExisting"; visible: win.conflictKind === "format" || win.conflictKind === "same_format" || win.conflictKind === "bitrate"; text: "Keep existing"; onClicked: { conflictDialog.close(); backend.resolveFormatConflict(conflictPath.text, "keep_existing", "") } }
                Action { objectName: "formatCopyAlongside"; visible: win.conflictKind === "format" || win.conflictKind === "same_format" || win.conflictKind === "bitrate"; text: "Copy alongside"; onClicked: { conflictDialog.close(); backend.resolveFormatConflict(conflictPath.text, "copy", "") } }
                Action { objectName: "formatReplace"; visible: win.conflictKind === "format" || win.conflictKind === "same_format" || win.conflictKind === "bitrate"; text: "Replace old"; onClicked: { conflictDialog.close(); backend.resolveFormatConflict(conflictPath.text, "replace", "") } }
                Action { text: "Decide later"; onClicked: conflictDialog.close() }
                Action { text: "Cancel all"; onClicked: { backend.cancelAll(); conflictDialog.close() } }
            }
        }
    }
    Sheet {
        id: historyDialog; anchors.centerIn: parent; width: 650; height: 480; title: "Transfer history"; modal: true; standardButtons: Dialog.Close
        ListView { anchors.fill: parent; clip: true; model: backend.results; spacing: 12; ScrollBar.vertical: ScrollBar {}
            delegate: Column { required property var modelData; width: ListView.view.width
                Label { width: parent.width; text: modelData.title + " · " + modelData.album; color: win.ink; elide: Text.ElideRight }
                Caption { width: parent.width; text: modelData.message; wrapMode: Text.WrapAnywhere; elide: Text.ElideNone }
            }
        }
    }
    Rectangle { anchors.centerIn: parent; visible: win.shuttingDown; width: 360; height: 100; radius: 16; color: win.panel; z: 100; Label { anchors.centerIn: parent; text: "Finishing transfers and closing…"; color: win.ink } }
    Popup { id: toast; anchors.centerIn: parent; width: Math.min(win.width - 80, 580); height: Math.max(160, notice.implicitHeight + 110); modal: true; padding: 24; closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        ColumnLayout { anchors.fill: parent; Label { id: notice; Layout.fillWidth: true; wrapMode: Text.Wrap; color: win.ink } Action { text: "OK"; onClicked: toast.close() } }
    }
}
