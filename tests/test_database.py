import os
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import music_transfer.database as database
from music_transfer.database import LibraryIndex
from music_transfer.core import Cancelled
from test_app import make_wave
from test_core import snapshot


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'; self.source.mkdir()
        self.path = self.root / 'data/library.sqlite3'
        self.track = self.source / 'Artist/Album/01.wav'
        make_wave(self.track)
        self.index = LibraryIndex(self.path)

    def tearDown(self):
        self.index.close()
        self.temp.cleanup()

    def test_reopen_reuses_metadata_and_preserves_source(self):
        before = snapshot(self.source)
        self.index.refresh(self.source)
        self.index.close(); self.index = LibraryIndex(self.path)
        with patch('music_transfer.database.read_track', side_effect=AssertionError('Unchanged tags reread')):
            tracks, errors = self.index.refresh(self.source)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(errors, [])
        self.assertEqual(snapshot(self.source), before)

    def test_refresh_adds_disc_metadata_to_legacy_cached_tracks(self):
        self.index.refresh(self.source)
        row = self.index.connection.execute(
            'SELECT metadata FROM tracks WHERE root=?', (str(self.source.resolve()),)
        ).fetchone()
        metadata = json.loads(row[0])
        metadata.pop('disc_number')
        metadata.pop('disc_total')
        metadata.pop('disc_tag')
        with self.index.connection:
            self.index.connection.execute(
                'UPDATE tracks SET metadata=? WHERE root=?',
                (json.dumps(metadata), str(self.source.resolve())),
            )

        with patch('music_transfer.database.read_track', wraps=database.read_track) as read:
            refreshed, errors = self.index.refresh(self.source)

        self.assertEqual(read.call_count, 1)
        self.assertEqual(errors, [])
        self.assertIn('disc_number', next(iter(refreshed.values())))
        self.assertIn('disc_total', next(iter(refreshed.values())))
        self.assertIn('disc_tag', next(iter(refreshed.values())))

    def test_add_modify_remove_and_multiple_libraries(self):
        self.index.refresh(self.source)
        second = self.source / 'Artist/Album/02.wav'; make_wave(second)
        from music_transfer.database import read_track
        with patch('music_transfer.database.read_track', wraps=read_track) as read:
            self.index.refresh(self.source)
            self.assertEqual(read.call_count, 1)
        make_wave(second, album='Renamed')
        self.track.unlink()
        refreshed, _ = self.index.refresh(self.source)
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(next(iter(refreshed.values()))['album'], 'Renamed')
        other = self.root / 'other'; other.mkdir(); make_wave(other / 'different.wav')
        self.index.refresh(other)
        self.assertEqual(len(self.index.load(other)), 1)
        self.assertEqual(len(self.index.load(self.source)), 1)

    def test_cancel_keeps_committed_index(self):
        self.index.refresh(self.source)
        make_wave(self.source / 'new.wav')
        count = [0]
        def cancel():
            count[0] += 1
            return count[0] > 5
        with self.assertRaises(Cancelled):
            self.index.refresh(self.source, cancelled=cancel)
        self.assertEqual(len(self.index.load(self.source)), 1)

    def test_missing_source_preserves_index(self):
        self.index.refresh(self.source)
        renamed = self.root / 'moved'; self.source.rename(renamed)
        with self.assertRaises(OSError):
            self.index.refresh(self.source)
        self.assertEqual(len(self.index.load(self.source)), 1)

    def test_changed_device_reindexes_readable_source(self):
        self.index.refresh(self.source)
        old_track = self.source / 'Artist/Album/01.wav'
        old_track.unlink()
        new_track = self.source / 'Artist/New Album/01.wav'
        make_wave(new_track, album='New Album')
        with self.index.connection:
            self.index.connection.execute(
                'UPDATE libraries SET device=? WHERE root=?',
                (self.source.stat().st_dev + 1, str(self.source.resolve())),
            )

        self.assertFalse(self.index.device_matches(self.source))
        refreshed, errors = self.index.refresh(self.source)

        self.assertEqual(errors, [])
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(next(iter(refreshed.values()))['album'], 'New Album')
        self.assertEqual(set(self.index.load(self.source)), {'Artist/New Album/01.wav'})
        self.assertEqual(self.index.device_matches(self.source), True)

    def test_changed_device_reuses_unchanged_metadata_and_artwork(self):
        self.index.refresh(self.source)
        self.index.save_artwork(self.source, 'album', 'fingerprint', 'file:///cover.jpg')
        with self.index.connection:
            self.index.connection.execute(
                'UPDATE libraries SET device=? WHERE root=?',
                (self.source.stat().st_dev + 1, str(self.source.resolve())),
            )

        with patch('music_transfer.database.read_track', side_effect=AssertionError('Unchanged tags reread')):
            refreshed, errors = self.index.refresh(self.source)

        self.assertEqual(len(refreshed), 1)
        self.assertEqual(errors, [])
        self.assertEqual(self.index.artwork(self.source)['album'], ('fingerprint', 'file:///cover.jpg'))
        self.assertEqual(self.index.device_matches(self.source), True)

    def test_changed_device_with_empty_source_retains_saved_index(self):
        self.index.refresh(self.source)
        self.track.unlink()
        with self.index.connection:
            self.index.connection.execute(
                'UPDATE libraries SET device=? WHERE root=?',
                (self.source.stat().st_dev + 1, str(self.source.resolve())),
            )

        with self.assertRaisesRegex(OSError, 'no tracks were found'):
            self.index.refresh(self.source)

        self.assertEqual(len(self.index.load(self.source)), 1)

    def test_unreadable_folder_is_not_deleted(self):
        self.index.refresh(self.source)
        real_walk = os.walk
        def inaccessible(root, **kwargs):
            kwargs['onerror'](PermissionError(13, 'Permission denied', str(self.track.parent)))
            yield str(self.source), [], []
        with patch('music_transfer.database.os.walk', side_effect=inaccessible):
            tracks, errors = self.index.refresh(self.source)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(errors), 1)

    def test_corrupt_database_is_preserved_then_rebuilt(self):
        self.index.close()
        self.path.write_bytes(b'not a sqlite database')
        self.index = LibraryIndex(self.path)
        self.assertEqual(self.index.recovered.read_bytes(), b'not a sqlite database')
        self.index.refresh(self.source)
        self.assertEqual(len(self.index.load(self.source)), 1)

    def test_incompatible_schema_is_preserved(self):
        self.index.close()
        connection = sqlite3.connect(self.path)
        connection.execute('PRAGMA user_version=99'); connection.close()
        self.index = LibraryIndex(self.path)
        self.assertTrue(self.index.recovered.is_file())

    def test_artwork_reference_is_separate_from_metadata(self):
        self.index.refresh(self.source)
        self.index.save_artwork(self.source, 'album', 'fingerprint', 'file:///cover.jpg')
        self.assertEqual(self.index.artwork(self.source)['album'], ('fingerprint', 'file:///cover.jpg'))
        self.assertEqual(len(self.index.load(self.source)), 1)


if __name__ == '__main__':
    unittest.main()
