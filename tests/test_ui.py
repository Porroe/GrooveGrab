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
    def click_item(self, window, item):
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))

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
                    files_pane = window.findChild(QObject, 'settingsFilesPane')
                    artwork_pane = window.findChild(QObject, 'settingsArtworkPane')
                    self.assertTrue(files_pane.property('visible'))
                    self.assertFalse(artwork_pane.property('visible'))
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

                    QMetaObject.invokeMethod(dialog, 'open')
                    APP.processEvents()
                    dialog.setProperty('draftConflictPolicy', 'always_skip')
                    dialog.setProperty('draftFormatPolicy', 'copy_and_delete')
                    dialog.setProperty('draftSameFormatPolicy', 'replace_existing')
                    self.click_item(window, artwork_category)
                    APP.processEvents()
                    self.click_item(window, save)
                    APP.processEvents()
                    self.assertFalse(dialog.property('visible'))
                    self.assertEqual(self.backend.conflictPolicy, 'always_skip')
                    self.assertEqual(self.backend.formatPolicy, 'copy_and_delete')
                    self.assertEqual(self.backend.sameFormatPolicy, 'replace_existing')
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

            details = self.find_visual_item(grid.property('contentItem'), 'albumDetailsClickArea')
            self.click_item(window, details)
            APP.processEvents()
            dialog = window.findChild(QObject, 'albumDialog')
            self.assertTrue(dialog.property('visible'))
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
