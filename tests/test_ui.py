import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import unittest
from PySide6.QtCore import QObject, QMetaObject, QPoint, QPointF, QUrl, Qt, qInstallMessageHandler
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from shiboken6 import delete
from music_transfer.core import scan
from test_app import APP, AppFixture, make_wave, snapshot, wait_for


class UITests(AppFixture):
    def click_item(self, window, item, button=Qt.LeftButton):
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(window, button, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))

    def find_visual_item(self, root, name):
        if root.objectName() == name:
            return root
        for child in root.childItems():
            found = self.find_visual_item(child, name)
            if found:
                return found
        return None

    def find_visual_items(self, root, name):
        found = [root] if root.objectName() == name else []
        for child in root.childItems():
            found.extend(self.find_visual_items(child, name))
        return found

    def all_visual_items(self, root):
        found = [root]
        for child in root.childItems():
            found.extend(self.all_visual_items(child))
        return found

    def test_themes_dialogs_and_keyboard(self):
        messages = []
        previous = qInstallMessageHandler(
            lambda kind, context, text: messages.append(text)
            if not text.startswith('Populating font family aliases took ')
            else None
        )
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            self.assertTrue(engine.rootObjects())
            window = engine.rootObjects()[0]
            wait_for(lambda: window.findChild(QObject, 'artistPicker').property('currentText') == 'All artists')
            self.backend._source = str(self.source)
            self.backend.changed.emit()
            for theme in ('dark', 'light', 'system'):
                self.backend.setTheme(theme)
                APP.processEvents()
                if theme != 'system':
                    self.assertEqual(window.property('dark'), theme == 'dark')
                self.assertFalse(window.grabWindow().isNull())
            QTest.keyClick(window, Qt.Key_Tab)
            self.assertIsNotNone(window.activeFocusItem())
            for name in ('settingsDialog', 'albumDialog'):
                window.setProperty('selectedAlbum', self.album)
                dialog = window.findChild(QObject, name)
                self.assertIsNotNone(dialog)
                QMetaObject.invokeMethod(dialog, 'open')
                APP.processEvents()
                self.assertTrue(dialog.property('visible'))
                if name == 'settingsDialog':
                    self.assertIsNotNone(window.findChild(QObject, 'settingsFilesPane'))
                    files_category = window.findChild(QObject, 'settingsFilesCategory')
                    artwork_category = window.findChild(QObject, 'settingsArtworkCategory')
                    library_category = window.findChild(QObject, 'settingsLibraryCategory')
                    appearance_category = window.findChild(QObject, 'settingsAppearanceCategory')
                    files_pane = window.findChild(QObject, 'settingsFilesPane')
                    artwork_pane = window.findChild(QObject, 'settingsArtworkPane')
                    library_pane = window.findChild(QObject, 'settingsLibraryPane')
                    appearance_pane = window.findChild(QObject, 'settingsAppearancePane')
                    self.assertTrue(files_pane.property('visible'))
                    self.assertFalse(artwork_pane.property('visible'))
                    self.assertFalse(library_pane.property('visible'))
                    self.assertFalse(appearance_pane.property('visible'))
                    self.assertIsNotNone(window.findChild(QObject, 'clearThumbnailCache'))
                    self.click_item(window, library_category)
                    APP.processEvents()
                    self.assertFalse(files_pane.property('visible'))
                    self.assertTrue(library_pane.property('visible'))
                    refresh_on_startup = window.findChild(QObject, 'refreshLibraryOnStartup')
                    self.assertTrue(refresh_on_startup.property('checked'))
                    self.click_item(window, files_category)
                    APP.processEvents()
                    policy_picker = window.findChild(QObject, 'conflictPolicyPicker')
                    self.assertEqual(policy_picker.property('currentIndex'), 4)
                    dialog.setProperty('draftConflictPolicy', 'always_skip')
                    APP.processEvents()
                    self.assertEqual(policy_picker.property('currentIndex'), 0)
                    format_picker = window.findChild(QObject, 'formatPolicyPicker')
                    self.assertEqual(format_picker.property('currentIndex'), 4)
                    dialog.setProperty('draftFormatPolicy', 'copy_and_delete')
                    APP.processEvents()
                    self.assertEqual(format_picker.property('currentIndex'), 1)
                    same_format_picker = window.findChild(QObject, 'sameFormatPolicyPicker')
                    self.assertEqual(same_format_picker.property('currentIndex'), 3)
                    dialog.setProperty('draftSameFormatPolicy', 'replace_existing')
                    APP.processEvents()
                    self.assertEqual(same_format_picker.property('currentIndex'), 2)
                    self.click_item(window, artwork_category)
                    APP.processEvents()
                    self.assertFalse(files_pane.property('visible'))
                    self.assertTrue(artwork_pane.property('visible'))
                    self.click_item(window, library_category)
                    APP.processEvents()
                    self.assertTrue(library_pane.property('visible'))
                    self.click_item(window, refresh_on_startup)
                    APP.processEvents()
                    self.assertFalse(refresh_on_startup.property('checked'))
                    self.click_item(window, appearance_category)
                    APP.processEvents()
                    self.assertTrue(appearance_pane.property('visible'))
                    theme_picker = window.findChild(QObject, 'themePicker')
                    self.assertIsNotNone(theme_picker)
                    cancel = window.findChild(QObject, 'settingsCancel')
                    save = window.findChild(QObject, 'settingsSave')
                    self.assertTrue(cancel.property('visible'))
                    self.assertTrue(save.property('visible'))
                    self.click_item(window, cancel)
                    APP.processEvents()
                    self.assertFalse(dialog.property('visible'))
                    self.assertEqual(self.backend.conflictPolicy, 'ask_every_time')
                    self.assertEqual(self.backend.formatPolicy, 'ask_every_time')
                    self.assertEqual(self.backend.sameFormatPolicy, 'ask_every_time')
                    self.assertTrue(self.backend.refreshOnStartup)
                    self.assertEqual(self.backend.theme, 'system')

                    QMetaObject.invokeMethod(dialog, 'open')
                    APP.processEvents()
                    dialog.setProperty('draftTheme', 'dark')
                    dialog.setProperty('draftConflictPolicy', 'always_skip')
                    dialog.setProperty('draftFormatPolicy', 'copy_and_delete')
                    dialog.setProperty('draftSameFormatPolicy', 'replace_existing')
                    self.click_item(window, library_category)
                    APP.processEvents()
                    self.click_item(window, refresh_on_startup)
                    APP.processEvents()
                    self.click_item(window, save)
                    APP.processEvents()
                    self.assertFalse(dialog.property('visible'))
                    self.assertEqual(self.backend.conflictPolicy, 'always_skip')
                    self.assertEqual(self.backend.formatPolicy, 'copy_and_delete')
                    self.assertEqual(self.backend.sameFormatPolicy, 'replace_existing')
                    self.assertFalse(self.backend.refreshOnStartup)
                    self.assertEqual(self.backend.theme, 'dark')
                    toast = window.findChild(QObject, 'settingsSavedToast')
                    self.assertIsNotNone(toast)
                    self.assertTrue(toast.property('visible'))
                    self.assertFalse(toast.property('modal'))
                    dismiss = window.findChild(QObject, 'settingsDismiss')
                    self.assertIsNotNone(dismiss)
                    QMetaObject.invokeMethod(dialog, 'open')
                    APP.processEvents()
                    self.click_item(window, dismiss)
                    APP.processEvents()
                    self.assertFalse(dialog.property('visible'))
                else:
                    QMetaObject.invokeMethod(dialog, 'close')
            window.setProperty('folderMode', True)
            APP.processEvents()
        finally:
            delete(engine)
            qInstallMessageHandler(previous)
        self.assertEqual(messages, [])

    def test_album_art_selects_and_album_text_opens_tracks(self):
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            wait_for(lambda: self.backend.album_model.count > 0)
            grid = window.findChild(QObject, 'albumGrid')
            wait_for(lambda: self.find_visual_item(grid.property('contentItem'), 'albumArtworkClickArea') is not None)
            count = self.find_visual_item(grid.property('contentItem'), 'albumTrackCount')
            self.assertEqual(count.property('text'), '3 tracks')

            artwork = self.find_visual_item(grid.property('contentItem'), 'albumArtworkClickArea')
            self.click_item(window, artwork)
            APP.processEvents()
            checkbox = self.find_visual_item(grid.property('contentItem'), 'albumSelectionCheckbox')
            self.assertTrue(checkbox.property('checked'))
            self.assertFalse(window.findChild(QObject, 'albumDialog').property('visible'))

            clear_selection = window.findChild(QObject, 'clearAlbumSelection')
            self.assertTrue(clear_selection.property('visible'))
            self.click_item(window, clear_selection)
            APP.processEvents()
            self.assertEqual(window.property('selectedAlbums').toVariant(), [])
            self.assertFalse(checkbox.property('checked'))
            self.assertFalse(clear_selection.property('visible'))

            details = self.find_visual_item(grid.property('contentItem'), 'albumDetailsClickArea')
            self.click_item(window, details)
            APP.processEvents()
            dialog = window.findChild(QObject, 'albumDialog')
            self.assertTrue(dialog.property('visible'))
            self.assertEqual(dialog.property('title'), '')
            window.setProperty('width', 1100)
            window.setProperty('height', 700)
            wait_for(lambda: dialog.property('width') == 680
                     and dialog.property('height') == 636)
            window.setProperty('width', 1500)
            window.setProperty('height', 1000)
            wait_for(lambda: dialog.property('width') == 680
                     and dialog.property('height') == 820)
            self.assertEqual(window.findChild(QObject, 'albumHeaderTitle').property('text'), 'Album')
            self.assertEqual(window.findChild(QObject, 'albumHeaderArtist').property('text'), 'Test artist')
            header_text = [
                item.property('text')
                for item in self.all_visual_items(window.findChild(QObject, 'albumHeader'))
                if item.property('text')
            ]
            self.assertNotIn('ALBUM', header_text)
            wait_for(lambda: bool(self.backend.selectedAlbumCover))
            artwork = window.findChild(QObject, 'albumHeaderArtwork')
            self.assertEqual(QUrl(artwork.property('source')), QUrl(self.backend.selectedAlbumCover))
        finally:
            delete(engine)

    def test_current_selection_lists_and_removes_albums_and_tracks(self):
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            grid = window.findChild(QObject, 'albumGrid')
            wait_for(lambda: self.find_visual_item(grid.property('contentItem'), 'albumArtworkClickArea') is not None)

            artwork = self.find_visual_item(grid.property('contentItem'), 'albumArtworkClickArea')
            self.click_item(window, artwork)
            view_selection = window.findChild(QObject, 'viewCurrentSelection')
            self.assertTrue(view_selection.property('visible'))
            self.assertTrue(QMetaObject.invokeMethod(view_selection, 'click'))
            selection_dialog = window.findChild(QObject, 'currentSelectionDialog')
            wait_for(lambda: selection_dialog.property('visible'))
            selection_list = window.findChild(QObject, 'currentSelectionList')
            wait_for(lambda: self.find_visual_item(selection_list.property('contentItem'), 'currentSelectionItem') is not None)
            content = selection_list.property('contentItem')
            wait_for(lambda: any(
                item.property('source').toString()
                for item in self.find_visual_items(content, 'selectionItemArtwork')
            ))
            title = self.find_visual_item(content, 'currentSelectionItem')
            self.assertIn('Album', [item.property('text') for item in self.all_visual_items(title)])
            remove = self.find_visual_item(title, 'removeSelectionItem')
            self.click_item(window, remove)
            APP.processEvents()
            self.assertEqual(window.property('selectedAlbums').toVariant(), [])
            selection_dialog.close()

            details = self.find_visual_item(grid.property('contentItem'), 'albumDetailsClickArea')
            self.click_item(window, details)
            dialog = window.findChild(QObject, 'albumDialog')
            track_list = window.findChild(QObject, 'albumTracksList')
            wait_for(lambda: self.find_visual_item(track_list.property('contentItem'), 'albumTrackItem') is not None)
            track_item = self.find_visual_item(track_list.property('contentItem'), 'albumTrackItem')
            self.click_item(window, track_item)
            self.assertEqual(len(window.property('selectedTracks').toVariant()), 1)

            view_tracks = window.findChild(QObject, 'viewCurrentSelectionFromTracks')
            self.assertTrue(view_tracks.property('visible'))
            self.click_item(window, view_tracks)
            wait_for(lambda: selection_dialog.property('visible'))
            wait_for(lambda: self.find_visual_item(selection_list.property('contentItem'), 'currentSelectionItem') is not None)
            row = self.find_visual_item(selection_list.property('contentItem'), 'currentSelectionItem')
            self.assertTrue(any(str(item.property('text')).startswith('Track ·')
                                for item in self.all_visual_items(row)))
            track_art = self.find_visual_item(row, 'selectionItemArtwork')
            wait_for(lambda: bool(track_art.property('source').toString()))
            self.click_item(window, self.find_visual_item(row, 'removeSelectionItem'))
            APP.processEvents()
            self.assertEqual(window.property('selectedTracks').toVariant(), [])
        finally:
            delete(engine)

    def test_search_clear_button_clears_query(self):
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            search = window.findChild(QObject, 'searchField')
            clear_button = window.findChild(QObject, 'clearSearch')
            self.assertIsNotNone(search)
            self.assertIsNotNone(clear_button)
            self.assertTrue(self.backend.clearButtonIcon.startswith('data:image/png;base64,'))
            self.backend.setTheme('dark')
            dark_icon_color = clear_button.property('themedClearColor')
            self.assertEqual(dark_icon_color, window.property('ink'))
            self.backend.setTheme('light')
            light_icon_color = clear_button.property('themedClearColor')
            self.assertEqual(light_icon_color, window.property('ink'))
            self.assertNotEqual(dark_icon_color, light_icon_color)
            self.assertEqual(clear_button.property('systemClearIcon').toString(), self.backend.clearButtonIcon)

            search.setProperty('text', 'no matching album')
            wait_for(lambda: clear_button.property('visible'))
            wait_for(lambda: self.backend.album_model.count == 0)
            self.assertTrue(QMetaObject.invokeMethod(clear_button, 'click'))
            wait_for(lambda: search.property('text') == ''
                     and self.backend.album_model.count > 0)
            self.assertFalse(clear_button.property('visible'))
        finally:
            delete(engine)

    def test_manual_refresh_button_is_always_visible_and_disabled_during_refresh(self):
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            self.backend._refresh_on_startup = False
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            refresh = window.findChild(QObject, 'manualRefreshLibrary')
            self.assertTrue(refresh.property('visible'))
            self.assertFalse(refresh.property('enabled'))

            self.backend._source = str(self.source)
            self.backend.changed.emit()
            self.assertTrue(refresh.property('enabled'))
            self.backend._scanning = True
            self.backend.libraryProgressChanged.emit()
            self.assertTrue(refresh.property('visible'))
            self.assertFalse(refresh.property('enabled'))
            self.backend._scanning = False
            self.backend.libraryProgressChanged.emit()
            self.assertTrue(refresh.property('enabled'))
            self.assertTrue(QMetaObject.invokeMethod(refresh, 'click'))
            wait_for(lambda: not self.backend.scanning
                     and self.backend.libraryStatus.startswith('Library up to date'))
        finally:
            delete(engine)

    def test_unavailable_library_warning_appears_in_source_box(self):
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            self.backend._source_available = False
            self.backend._library_status = (
                'Library is unavailable; saved index retained · saved library retained'
            )
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            self.backend._source = str(self.source)
            self.backend.changed.emit()
            self.backend.sourceAvailabilityChanged.emit()
            warning = window.findChild(QObject, 'sourceUnavailableMessage')
            self.assertTrue(warning.property('visible'))
            self.assertEqual(warning.property('text'), 'Library unavailable; saved library retained')
            self.assertNotEqual(warning.property('color'), window.property('ink'))
            self.assertEqual(window.findChild(QObject, 'libraryStatusMessage').property('text'),
                             'Saved library retained')

            self.backend._library_status = (
                'Source changed and no tracks were found; saved library retained'
            )
            self.backend.libraryProgressChanged.emit()
            self.assertTrue(warning.property('visible'))
            self.backend.confirmSourceAvailable()
            self.assertFalse(warning.property('visible'))
            self.assertEqual(window.findChild(QObject, 'libraryStatusMessage').property('text'),
                             'Source changed and no tracks were found; saved library retained')
        finally:
            delete(engine)

    def test_album_context_menu_copies_album_without_selection(self):
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            grid = window.findChild(QObject, 'albumGrid')
            wait_for(lambda: self.find_visual_item(grid.property('contentItem'), 'albumCard') is not None)
            card = self.find_visual_item(grid.property('contentItem'), 'albumCard')
            self.click_item(window, card, Qt.RightButton)

            menu = window.findChild(QObject, 'albumContextMenu')
            menu_item = window.findChild(QObject, 'copyAlbumMenuItem')
            wait_for(lambda: menu.property('visible'))
            self.assertEqual(menu_item.property('text'), 'Copy this album')
            self.assertTrue(menu_item.property('enabled'))
            self.assertEqual(window.property('contextAlbumId'), self.album['id'])
            self.assertEqual(window.property('selectedAlbums').toVariant(), [])
        finally:
            delete(engine)

    def test_album_track_numbers_and_singular_count(self):
        source_tracks = sorted(self.source.glob('Artist/Album/*.wav'))
        make_wave(source_tracks[0], number='01', title='First Track', disc='1/2')
        make_wave(source_tracks[1], number='02', title='Second Track', disc='2/2')
        make_wave(source_tracks[2], number='03', title='Third Track', disc='2/2')
        make_wave(
            self.source / 'Artist' / 'Solo' / 'single.wav',
            album='Solo Album',
            number='1',
            disc='1/2',
        )
        self.before = snapshot(self.source)
        self.backend.loaded(scan(self.source))

        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            wait_for(lambda: any(album_track.get('disc_total') == 2
                                 for album in self.backend.catalog.values()
                                 for album_track in album['tracks']))
            grid = window.findChild(QObject, 'albumGrid')
            content = grid.property('contentItem')
            wait_for(lambda: any(item.property('text') == '1 track'
                                 for item in self.find_visual_items(content, 'albumTrackCount')))
            counts = [item.property('text') for item in self.find_visual_items(content, 'albumTrackCount')]
            self.assertIn('3 tracks', counts)
            self.assertIn('1 track', counts)

            album = next(album for album in self.backend.album_model.rows if album['title'] == 'Album')
            window.setProperty('selectedAlbum', album)
            self.backend.openAlbum(album['id'])
            dialog = window.findChild(QObject, 'albumDialog')
            QMetaObject.invokeMethod(dialog, 'open')
            wait_for(lambda: len(self.backend.track_model.rows) == 3)
            labels = [track['display_title'] for track in self.backend.track_model.rows]
            self.assertIn('1 First Track', labels)
            self.assertIn('2 Second Track', labels)
            self.assertIn('3 Third Track', labels)
            track_list = dialog.findChild(QObject, 'albumTracksList')
            wait_for(lambda: self.find_visual_item(track_list.property('contentItem'), 'albumTrackItem') is not None)
            track_items = self.find_visual_items(track_list.property('contentItem'), 'albumTrackItem')
            displayed_labels = [
                item.property('text') for item in track_items
            ]
            self.assertIn('1 First Track', displayed_labels)
            self.assertIn('2 Second Track', displayed_labels)
            self.assertEqual(
                self.find_visual_items(track_list.property('contentItem'), 'albumDiscHeader'),
                [],
            )

            solo_album = next(
                album for album in self.backend.album_model.rows
                if album['title'] == 'Solo Album'
            )
            self.backend.openAlbum(solo_album['id'])
            wait_for(lambda: len(self.backend.track_model.rows) == 1
                     and self.backend.track_model.rows[0]['title'] == 'single'
                     and not self.backend.track_model.timer.isActive())
        finally:
            delete(engine)

    def test_dropdown_popup_colors_follow_each_theme(self):
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            dialog = window.findChild(QObject, 'settingsDialog')
            QMetaObject.invokeMethod(dialog, 'open')
            APP.processEvents()
            category = window.findChild(QObject, 'settingsAppearanceCategory')
            self.click_item(window, category)
            APP.processEvents()
            picker = window.findChild(QObject, 'themePicker')
            self.assertIsNotNone(picker)

            for theme in ('dark', 'light', 'system'):
                self.backend.setTheme(theme)
                APP.processEvents()
                self.click_item(window, picker)
                APP.processEvents()
                popup = window.findChild(QObject, 'pickerPopup')
                background = window.findChild(QObject, 'pickerPopupBackground')
                option_text = self.find_visual_item(popup.property('contentItem'), 'pickerOptionText') if popup else None
                self.assertTrue(popup.property('visible'))
                self.assertIsNotNone(background)
                self.assertIsNotNone(option_text)
                self.assertEqual(background.property('color'), window.property('panel'))
                self.assertEqual(option_text.property('color'), window.property('ink'))
                self.click_item(window, picker)
        finally:
            delete(engine)

    def test_same_format_duplicate_prompt_offers_all_actions(self):
        engine = QQmlApplicationEngine()
        try:
            track = self.album['tracks'][0]
            source = Path(track['path'])
            make_wave(source, number='01', title='Track One')
            previous = self.target / 'Artist' / 'Album' / 'renamed.wav'
            make_wave(previous, number='01', title='Track One')
            self.before = snapshot(self.source)
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            self.backend._source = str(self.source)
            self.backend.setSameFormatPolicy('ask_every_time')
            self.backend.send(self.album['id'], [track['path']])
            dialog = window.findChild(QObject, 'conflictDialog')
            wait_for(lambda: dialog.property('visible') and self.backend._conflicts
                     and self.backend._conflicts[0]['kind'] == 'same_format')
            self.assertEqual(dialog.property('title'), 'Same track and format already exists')
            for name in ('formatKeepExisting', 'formatCopyAlongside', 'formatReplace'):
                self.assertTrue(window.findChild(QObject, name).property('visible'))
            self.backend.resolveFormatConflict(str(self.target / 'Artist/Album/01.wav'), 'keep_existing', '')
            wait_for(lambda: not self.backend.running)
            self.assertTrue(previous.exists())
        finally:
            delete(engine)

    def test_conflict_window_is_nonblocking_and_reopenable(self):
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            wait_for(lambda: window.findChild(QObject, 'artistPicker').property('currentText') == 'All artists')
            self.backend._source = str(self.source)
            existing = self.target / 'Artist/Album/01.wav'
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b'existing')
            self.backend.send(self.album['id'], [])
            dialog = window.findChild(QObject, 'conflictDialog')
            wait_for(lambda: dialog.property('visible') and (self.target / 'Artist/Album/03.wav').exists())
            self.assertFalse(dialog.property('modal'))
            QMetaObject.invokeMethod(dialog, 'close')
            self.backend.showConflicts()
            wait_for(lambda: dialog.property('visible'))
            self.backend.resolveConflict(str(existing), False, True)
            wait_for(lambda: not self.backend.running)
            self.assertEqual(existing.read_bytes(), b'existing')
        finally:
            delete(engine)

    def test_format_conflict_dialog_offers_keep_copy_and_replace(self):
        engine = QQmlApplicationEngine()
        try:
            self.backend._source = ''
            engine.rootContext().setContextProperty('backend', self.backend)
            engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
            window = engine.rootObjects()[0]
            wait_for(lambda: window.findChild(QObject, 'artistPicker').property('currentText') == 'All artists')
            self.backend._source = str(self.source)
            previous = self.target / 'Artist' / 'Album' / '01.mp3'
            make_wave(previous, number='01')
            self.backend.setFormatPolicy('ask_every_time')
            track = next(track for track in self.album['tracks'] if Path(track['path']).name == '01.wav')
            self.backend.send(self.album['id'], [track['path']])
            dialog = window.findChild(QObject, 'conflictDialog')
            wait_for(lambda: dialog.property('visible') and self.backend._conflicts
                     and self.backend._conflicts[0]['kind'] == 'format')
            for name in ('formatKeepExisting', 'formatCopyAlongside', 'formatReplace'):
                self.assertTrue(window.findChild(QObject, name).property('visible'))
            self.backend.resolveFormatConflict(str(self.target / 'Artist/Album/01.wav'), 'keep_existing', '')
            wait_for(lambda: not self.backend.running)
            self.assertTrue(previous.exists())
        finally:
            delete(engine)


if __name__ == '__main__':
    unittest.main()
