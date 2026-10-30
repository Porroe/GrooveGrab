import threading
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl
from shiboken6 import delete

from music_transfer.database import LibraryIndex
from music_transfer.core import scan, copy_audio
from test_app import APP, AppFixture, wait_for, make_wave
from test_core import snapshot


class Heartbeat:
    def __init__(self):
        self.times = [time.monotonic()]
        self.timer = QTimer()
        self.timer.setInterval(10)
        self.timer.timeout.connect(lambda: self.times.append(time.monotonic()))

    def __enter__(self):
        self.timer.start()
        return self

    def __exit__(self, *args):
        self.timer.stop()

    @property
    def maximum_gap(self):
        return max((b - a for a, b in zip(self.times, self.times[1:])), default=0)


class ResponsivenessTests(AppFixture):
    def test_cached_rows_appear_before_refresh_and_unchanged_tags_are_not_read(self):
        index = LibraryIndex(self.backend.index_path)
        index.refresh(self.source); index.close()
        entered, release = threading.Event(), threading.Event()
        original = LibraryIndex.refresh
        def held(index, *args, **kwargs):
            entered.set(); release.wait(3)
            return original(index, *args, **kwargs)
        with patch.object(LibraryIndex, 'refresh', held), patch('music_transfer.database.read_track', side_effect=AssertionError('Tags reread')):
            self.backend.rescan()
            with Heartbeat() as heartbeat:
                wait_for(lambda: entered.is_set() and self.backend.album_model.count == 1)
                self.assertTrue(self.backend.scanning)
                release.set()
                wait_for(lambda: not self.backend.scanning)
            self.assertLess(heartbeat.maximum_gap, .250)
        self.assertIn('up to date', self.backend.libraryStatus)

    def test_progress_and_cancel_retain_saved_index(self):
        index = LibraryIndex(self.backend.index_path)
        index.refresh(self.source); index.close()
        for i in range(120):
            make_wave(self.source / 'New album' / f'{i:03}.wav', album='New album')
        self.before = snapshot(self.source)
        from music_transfer.database import read_track
        def slow(path, root):
            time.sleep(.005)
            return read_track(path, root)
        stages = []
        self.backend.libraryProgressChanged.connect(lambda: stages.append((self.backend.libraryStage, self.backend.libraryDone, self.backend.libraryTotal)))
        with patch('music_transfer.database.read_track', side_effect=slow):
            self.backend.rescan()
            with Heartbeat() as heartbeat:
                wait_for(lambda: self.backend.libraryTotal > 0 and self.backend.libraryDone > 0)
                self.backend.cancelScan()
                wait_for(lambda: not self.backend.scanning)
            self.assertLess(heartbeat.maximum_gap, .250)
        self.assertIn('canceled', self.backend.libraryStatus)
        index = LibraryIndex(self.backend.index_path)
        self.assertEqual(len(index.load(self.source)), 3)
        index.close()
        self.assertTrue(any(stage == 'Updating metadata' and total == 123 for stage, done, total in stages))

    def test_large_library_models_and_search_remain_responsive(self):
        albums = []
        for i in range(5000):
            folder = str(self.source / f'Artist/Album {i:05}')
            tracks = [dict(path=f'{folder}/{n}.flac', relative=f'Artist/Album {i:05}/{n}.flac', title=f'Song {n}',
                artist='Artist', album=f'Album {i:05}', folder=folder, size=100, mtime_ns=1) for n in range(5)]
            albums.append(dict(id=str(i), title=f'Album {i:05}', artist='Artist', folder=folder, tracks=tracks, cover=''))
        with Heartbeat() as heartbeat:
            self.backend.loaded(albums)
            wait_for(lambda: self.backend.album_model.count == 5000 and not self.backend.album_model.timer.isActive(), 15)
            self.backend.setFilter('Album 00012', 'All artists')
            self.backend.setFilter('Album 04999', 'All artists')
            wait_for(lambda: self.backend.album_model.count == 1 and self.backend.album_model.rows[0]['title'] == 'Album 04999', 15)
        self.assertGreater(len(heartbeat.times), 5)
        self.assertLess(heartbeat.maximum_gap, .250)

    def test_copy_progress_does_not_reset_queue_model_or_stall_gui(self):
        self.backend._source = ''
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty('backend', self.backend)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
        self.backend._source = str(self.source)
        resets, inserts = [], []
        self.backend.queue_model.modelReset.connect(lambda: resets.append(True))
        self.backend.queue_model.rowsInserted.connect(lambda *args: inserts.append(True))
        def slow_copy(source, output, cancelled, progress, validate):
            for i in range(35):
                if cancelled():
                    break
                time.sleep(.01)
                progress(i / 35)
            return copy_audio(source, output, cancelled, progress, validate)
        try:
            with patch('music_transfer.app.copy_audio', side_effect=slow_copy):
                self.backend.send(self.album['id'], [])
                with Heartbeat() as heartbeat:
                    wait_for(lambda: self.backend.queue_model.count == 4)
                    original = self.backend.queue_model.index(0)
                    wait_for(lambda: not self.backend.running, 8)
                self.assertLess(heartbeat.maximum_gap, .250)
            self.assertEqual(resets, [])
            # Only the album header and three tracks were inserted, once each.
            self.assertEqual(len(inserts), 4)
        finally:
            delete(engine)

    def test_shutdown_keeps_gui_alive_until_copy_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        def held(source, output, cancelled, progress, validate):
            entered.set(); release.wait(3)
            return copy_audio(source, output, cancelled, progress, validate)
        with patch('music_transfer.app.copy_audio', side_effect=held):
            self.backend.send(self.album['id'], [])
            wait_for(entered.is_set)
            started = time.monotonic()
            self.backend.shutdown()
            self.assertLess(time.monotonic() - started, .05)
            self.assertFalse(self.backend.shutdownComplete)
            with Heartbeat() as heartbeat:
                QTimer.singleShot(200, release.set)
                wait_for(lambda: self.backend.shutdownComplete)
            self.assertGreater(len(heartbeat.times), 5)
            self.assertLess(heartbeat.maximum_gap, .250)
        self.assertFalse(list(self.target.rglob('.music-transfer-*')))
