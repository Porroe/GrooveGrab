import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_CONTROLS_STYLE', 'Basic')
import io
from pathlib import Path
import tempfile
import time
import unittest
import wave
from unittest.mock import patch

from PIL import Image
from mutagen.wave import WAVE
from mutagen.id3 import TIT2, TALB, TPE2, APIC, TRCK, TPOS
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QSettings

from music_transfer.app import Backend
from music_transfer.core import ArtworkSettings, ThumbnailCache, scan, cover_image
from test_core import snapshot

APP = QGuiApplication.instance() or QGuiApplication([])


def make_wave(path, album='Album', front=True, color='red', number='', artist='Test artist', title=None, disc=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\0\0' * 800)
    audio = WAVE(path); audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=title or path.stem))
    audio.tags.add(TALB(encoding=3, text=album))
    audio.tags.add(TPE2(encoding=3, text=artist))
    if number:
        audio.tags.add(TRCK(encoding=3, text=number))
    if disc:
        audio.tags.add(TPOS(encoding=3, text=disc))
    data = io.BytesIO(); Image.new('RGB', (40, 20), color).save(data, 'PNG')
    audio.tags.add(APIC(encoding=3, mime='image/png', type=3 if front else 4, data=data.getvalue()))
    audio.save()


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for worker')


class AppFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'; self.source.mkdir()
        self.target = self.root / 'target'; self.target.mkdir()
        self.backend = Backend(index_path=self.root / 'data/library.sqlite3')
        self.backend.settings = QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat)
        self.backend._refresh_on_startup = self.backend.settings.value('refresh_on_startup', True, type=bool)
        self.backend.setConflictPolicy('ask_every_time')
        self.backend.setFormatPolicy('ask_every_time')
        self.backend.setSameFormatPolicy('ask_every_time')
        self.backend.cache = ThumbnailCache(self.root / 'cache')
        self.backend._source = str(self.source); self.backend._target = str(self.target)
        self.backend._art = ArtworkSettings(enabled=False)
        for name in ('01.wav', '02.wav', '03.wav'):
            make_wave(self.source / 'Artist' / 'Album' / name, number=name[:2])
        self.backend.loaded(scan(self.source))
        wait_for(lambda: bool(self.backend._albums))
        self.album = self.backend._albums[0]
        self.before = snapshot(self.source)

    def tearDown(self):
        self.backend.cancelAll()
        wait_for(lambda: not self.backend.running)
        self.backend.shutdown()
        wait_for(lambda: self.backend.shutdownComplete)
        self.assertEqual(snapshot(self.source), self.before)
        self.temp.cleanup()

    def existing(self, name):
        path = self.target / 'Artist' / 'Album' / name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'existing')
        return path


class AppTests(AppFixture):
    def test_accepting_source_folder_saves_and_rescans(self):
        selected = self.root / 'selected-source'
        selected.mkdir()
        self.backend._refresh_on_startup = False
        with patch.object(self.backend, 'rescan') as rescan:
            self.backend.accept_folder((str(selected), True))
        self.assertEqual(self.backend.source, str(selected))
        self.assertEqual(self.backend.settings.value('source'), str(selected))
        rescan.assert_called_once_with()

    def test_conflict_defers_while_next_tracks_copy(self):
        conflict = self.existing('01.wav')
        self.backend.send(self.album['id'], [])
        wait_for(lambda: len(self.backend._conflicts) == 1 and (self.target / 'Artist/Album/03.wav').exists())
        self.assertEqual(conflict.read_bytes(), b'existing')
        self.assertEqual(self.backend.queue_view()[0]['tracks'][0]['state'], 'Awaiting decision')
        self.backend.answerConflict(True, False)
        wait_for(lambda: not self.backend.running)
        self.assertEqual(conflict.read_bytes(), (self.source / 'Artist/Album/01.wav').read_bytes())

    def test_skip_apply_all_and_reset_next_run(self):
        for name in ('01.wav', '02.wav', '03.wav'):
            self.existing(name)
        self.backend.send(self.album['id'], [])
        wait_for(lambda: len(self.backend._conflicts) == 3)
        self.backend.answerConflict(False, True)
        wait_for(lambda: not self.backend.running)
        self.assertEqual(sum(r['message'] == 'Skipped' for r in self.backend._results), 3)
        self.backend.send(self.album['id'], [self.album['tracks'][0]['path']])
        wait_for(lambda: self.backend.conflictCount == 1)
        self.backend.answerConflict(False, False)
        wait_for(lambda: not self.backend.running)

    def test_add_during_wait_and_remove_album(self):
        self.existing('01.wav')
        self.backend.send(self.album['id'], [self.album['tracks'][0]['path']])
        wait_for(lambda: self.backend.conflictCount == 1)
        self.backend.send(self.album['id'], [self.album['tracks'][1]['path']])
        wait_for(lambda: (self.target / 'Artist/Album/02.wav').exists())
        self.backend.remove(self.album['id'], True)
        wait_for(lambda: not self.backend.running)
        self.assertEqual(self.backend.conflictCount, 0)

    def test_artwork_and_metadata_from_real_wave(self):
        self.assertEqual(self.album['title'], 'Album')
        self.assertEqual(self.album['artist'], 'Test artist')
        self.backend._art = ArtworkSettings(filename='cover.jpeg', width=500, height=500)
        self.backend.send(self.album['id'], [])
        wait_for(lambda: not self.backend.running)
        with Image.open(self.target / 'Artist/Album/cover.jpeg') as image:
            self.assertEqual(image.size, (40, 20))
        self.backend.cache.get([t['path'] for t in self.album['tracks']])

    def test_artwork_conflict_does_not_block_new_audio(self):
        self.existing('folder.jpg')
        self.backend._art = ArtworkSettings()
        self.backend.send(self.album['id'], [self.album['tracks'][0]['path']])
        wait_for(lambda: self.backend.conflictCount == 1)
        self.backend.send(self.album['id'], [self.album['tracks'][1]['path']])
        wait_for(lambda: (self.target / 'Artist/Album/02.wav').exists())
        self.backend.answerConflict(False, True)
        wait_for(lambda: not self.backend.running)

    def test_existing_file_policy_overwrites_only_different_sizes(self):
        track = self.album['tracks'][0]
        source = Path(track['path'])
        target = self.existing(source.name)
        original = b'X' * source.stat().st_size
        target.write_bytes(original)
        self.backend.setConflictPolicy('overwrite_if_size_different')
        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: not self.backend.running)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.backend._results[0]['message'], 'Skipped')
        self.assertEqual(self.backend.settings.value('conflict_policy'), 'overwrite_if_size_different')

        second = self.album['tracks'][1]
        second_target = self.existing(Path(second['path']).name)
        second_target.write_bytes(b'different size')
        self.backend.send(self.album['id'], [second['path']])
        wait_for(lambda: not self.backend.running)
        self.assertEqual(second_target.read_bytes(), Path(second['path']).read_bytes())

    def test_existing_file_always_skip_and_always_overwrite(self):
        track = self.album['tracks'][0]
        source = Path(track['path'])
        target = self.existing(source.name)
        self.backend.setConflictPolicy('always_skip')
        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: not self.backend.running)
        self.assertEqual(target.read_bytes(), b'existing')
        self.assertEqual(self.backend._results[0]['message'], 'Skipped')

        self.backend.setConflictPolicy('always_overwrite')
        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: not self.backend.running)
        self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_ask_if_size_different_skips_matches_and_prompts_for_mismatch(self):
        self.backend.setConflictPolicy('ask_if_size_different')
        first = self.album['tracks'][0]
        same_size = self.existing(Path(first['path']).name)
        original = b'X' * Path(first['path']).stat().st_size
        same_size.write_bytes(original)
        self.backend.send(self.album['id'], [first['path']])
        wait_for(lambda: not self.backend.running)
        self.assertEqual(same_size.read_bytes(), original)
        self.assertEqual(self.backend._results[0]['message'], 'Skipped')

        second = self.album['tracks'][1]
        different_size = self.existing(Path(second['path']).name)
        self.backend.send(self.album['id'], [second['path']])
        wait_for(lambda: self.backend.conflictCount == 1)
        self.assertEqual(different_size.read_bytes(), b'existing')
        self.backend.answerConflict(False, False)
        wait_for(lambda: not self.backend.running)
        self.assertEqual(different_size.read_bytes(), b'existing')

    def test_format_policy_always_copy_and_copy_delete_existing(self):
        first = self.album['tracks'][0]
        source = Path(first['path'])
        candidate = self.target / 'Artist' / 'Album' / '01.mp3'
        make_wave(candidate, album='aLBum', artist='test ARTIST', number='01', title='01')
        original_candidate = candidate.read_bytes()
        self.backend.setFormatPolicy('always_copy')
        self.assertEqual(self.backend.settings.value('format_policy'), 'always_copy')
        self.backend.send(self.album['id'], [first['path']])
        wait_for(lambda: not self.backend.running)
        output = self.target / 'Artist' / 'Album' / '01.wav'
        self.assertEqual(output.read_bytes(), source.read_bytes())
        self.assertEqual(candidate.read_bytes(), original_candidate)

        second = self.album['tracks'][1]
        second_source = Path(second['path'])
        previous = self.target / 'Artist' / 'Album' / '02.mp3'
        make_wave(previous, number='02')
        self.backend.setFormatPolicy('copy_and_delete')
        self.backend.send(self.album['id'], [second['path']])
        wait_for(lambda: not self.backend.running)
        self.assertEqual((self.target / 'Artist' / 'Album' / '02.wav').read_bytes(), second_source.read_bytes())
        self.assertFalse(previous.exists())

    def test_format_match_compares_title_without_case_sensitivity(self):
        track = self.album['tracks'][0]
        source = Path(track['path'])
        make_wave(source, title='Track One', number='01')
        self.before = snapshot(self.source)
        previous = self.target / 'Artist' / 'Album' / '01.mp3'
        make_wave(previous, album='ALBUM', artist='test artist', number='01', title='track one')
        self.backend.setFormatPolicy('copy_and_delete')
        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: not self.backend.running)
        self.assertFalse(previous.exists())
        self.assertTrue((self.target / 'Artist' / 'Album' / '01.wav').exists())

    def test_same_format_duplicate_asks_and_matches_case_insensitively(self):
        track = self.album['tracks'][0]
        source = Path(track['path'])
        make_wave(source, album='Album', artist='Test artist', number='01', title='Track One')
        previous = self.target / 'Artist' / 'Album' / 'renamed.wav'
        make_wave(previous, album='ALBUM', artist='test ARTIST', number='01', title='track one')
        self.before = snapshot(self.source)

        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: self.backend._conflicts and self.backend._conflicts[0]['kind'] == 'same_format')
        self.assertEqual(self.backend.sameFormatPolicy, 'ask_every_time')
        self.assertEqual(self.backend._conflicts[0]['candidates'], [str(previous)])
        self.backend.resolveFormatConflict(str(self.target / 'Artist/Album/01.wav'), 'keep_existing', '')
        wait_for(lambda: not self.backend.running)
        self.assertTrue(previous.exists())
        self.assertFalse((self.target / 'Artist/Album/01.wav').exists())

    def test_same_format_duplicate_can_replace_after_successful_copy(self):
        track = self.album['tracks'][0]
        source = Path(track['path'])
        make_wave(source, number='01', title='Track One')
        previous = self.target / 'Artist' / 'Album' / 'renamed.wav'
        make_wave(previous, number='01', title='Track One')
        self.before = snapshot(self.source)
        self.backend.setSameFormatPolicy('replace_existing')

        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: not self.backend.running)
        self.assertFalse(previous.exists())
        self.assertEqual((self.target / 'Artist/Album/01.wav').read_bytes(), source.read_bytes())

    def test_same_format_duplicate_can_copy_alongside(self):
        track = self.album['tracks'][0]
        previous = self.target / 'Artist' / 'Album' / 'renamed.wav'
        make_wave(previous, number='01', title=Path(track['path']).stem)

        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: self.backend._conflicts and self.backend._conflicts[0]['kind'] == 'same_format')
        self.backend.resolveFormatConflict(str(self.target / 'Artist/Album/01.wav'), 'copy', '')
        wait_for(lambda: not self.backend.running)
        self.assertTrue(previous.exists())
        self.assertTrue((self.target / 'Artist/Album/01.wav').exists())

    def test_same_format_duplicate_requires_track_number_match(self):
        track = self.album['tracks'][0]
        previous = self.target / 'Artist' / 'Album' / 'renamed.wav'
        make_wave(previous, number='', title=Path(track['path']).stem)

        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: not self.backend.running)
        self.assertTrue(previous.exists())
        self.assertTrue((self.target / 'Artist/Album/01.wav').exists())

    def test_same_format_duplicate_ignores_different_track_numbers(self):
        track = self.album['tracks'][0]
        previous = self.target / 'Artist' / 'Album' / 'renamed.wav'
        make_wave(previous, number='03', title=Path(track['path']).stem)

        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: not self.backend.running)
        self.assertTrue(previous.exists())
        self.assertTrue((self.target / 'Artist/Album/01.wav').exists())

    def test_settings_save_commits_all_policies_and_artwork(self):
        self.assertTrue(self.backend.refreshOnStartup)
        self.assertTrue(self.backend.saveSettings(
            'dark', 'always_skip', 'copy_and_delete', 'replace_existing',
            False, False, 'cover.jpeg', 300, 240))
        self.assertEqual(self.backend.theme, 'dark')
        self.assertEqual(self.backend.settings.value('theme'), 'dark')
        self.assertEqual(self.backend.conflictPolicy, 'always_skip')
        self.assertEqual(self.backend.formatPolicy, 'copy_and_delete')
        self.assertEqual(self.backend.sameFormatPolicy, 'replace_existing')
        self.assertFalse(self.backend.refreshOnStartup)
        self.assertEqual(self.backend.artwork, {
            'enabled': False, 'filename': 'cover.jpeg', 'width': 300, 'height': 240,
        })
        self.assertEqual(self.backend.settings.value('same_format_policy'), 'replace_existing')
        self.assertFalse(self.backend.settings.value('refresh_on_startup', type=bool))

    def test_settings_save_rejects_invalid_values_without_partial_changes(self):
        before = (self.backend.theme, self.backend.conflictPolicy, self.backend.formatPolicy,
                  self.backend.sameFormatPolicy, self.backend.refreshOnStartup,
                  self.backend.artwork)
        self.assertFalse(self.backend.saveSettings(
            'invalid', 'always_skip', 'copy_and_delete', 'replace_existing',
            True, False, 'cover.jpeg', 300, 240))
        self.assertFalse(self.backend.saveSettings(
            'system', 'invalid', 'copy_and_delete', 'replace_existing',
            True, False, 'cover.jpeg', 300, 240))
        self.assertFalse(self.backend.saveSettings(
            'system', 'always_skip', 'copy_and_delete', 'replace_existing',
            True, False, '../cover.jpeg', 300, 240))
        self.assertEqual((self.backend.theme, self.backend.conflictPolicy, self.backend.formatPolicy,
                          self.backend.sameFormatPolicy, self.backend.refreshOnStartup,
                          self.backend.artwork), before)

    def test_format_policy_ask_every_time_can_replace_or_copy_alongside(self):
        first = self.album['tracks'][0]
        previous = self.target / 'Artist' / 'Album' / '01.mp3'
        make_wave(previous, number='01')
        self.backend.setFormatPolicy('ask_every_time')
        self.backend.send(self.album['id'], [first['path']])
        wait_for(lambda: self.backend._conflicts and self.backend._conflicts[0]['kind'] == 'format')
        self.backend.resolveFormatConflict(str(self.target / 'Artist/Album/01.wav'), 'replace', '')
        wait_for(lambda: not self.backend.running)
        self.assertFalse(previous.exists())
        self.assertTrue((self.target / 'Artist' / 'Album' / '01.wav').exists())

        second = self.album['tracks'][1]
        alongside = self.target / 'Artist' / 'Album' / '02.mp3'
        make_wave(alongside, number='02')
        self.backend.send(self.album['id'], [second['path']])
        wait_for(lambda: self.backend._conflicts and self.backend._conflicts[0]['kind'] == 'format')
        self.backend.resolveFormatConflict(str(self.target / 'Artist/Album/02.wav'), 'copy', '')
        wait_for(lambda: not self.backend.running)
        self.assertTrue(alongside.exists())
        self.assertTrue((self.target / 'Artist' / 'Album' / '02.wav').exists())

    def test_existing_and_format_conflicts_keep_their_own_prompts(self):
        first = self.existing('01.wav')
        format_match = self.target / 'Artist' / 'Album' / '02.mp3'
        make_wave(format_match, number='02')
        seen_format_conflicts = []
        self.backend.formatConflict.connect(lambda *args: seen_format_conflicts.append(args))
        self.backend.send(self.album['id'], [])
        wait_for(lambda: len(self.backend._conflicts) >= 2)
        self.assertEqual(self.backend._conflicts[0]['kind'], 'same_path')
        self.backend.answerConflict(False, False)
        wait_for(lambda: bool(seen_format_conflicts))
        self.assertEqual(seen_format_conflicts[-1][0], 'format')
        self.backend.resolveFormatConflict(str(self.target / 'Artist/Album/02.wav'), 'keep_existing', '')
        wait_for(lambda: not self.backend.running)
        self.assertEqual(first.read_bytes(), b'existing')
        self.assertTrue(format_match.exists())

    def test_missing_track_number_prompts_before_applying_format_policy(self):
        track = self.album['tracks'][0]
        previous = self.target / 'Artist' / 'Album' / '01.mp3'
        make_wave(previous, number='')
        self.backend.setFormatPolicy('copy_and_delete')
        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: self.backend._conflicts and self.backend._conflicts[0]['kind'] == 'missing_number')
        self.backend.resolveFormatConflict(str(self.target / 'Artist/Album/01.wav'), 'same', str(previous))
        wait_for(lambda: not self.backend.running)
        self.assertFalse(previous.exists())
        self.assertTrue((self.target / 'Artist' / 'Album' / '01.wav').exists())

    def test_different_track_number_does_not_count_as_format_match(self):
        track = self.album['tracks'][0]
        previous = self.target / 'Artist' / 'Album' / '01.mp3'
        make_wave(previous, number='03')
        self.backend.setFormatPolicy('copy_and_delete')
        self.backend.send(self.album['id'], [track['path']])
        wait_for(lambda: not self.backend.running)
        self.assertTrue(previous.exists())
        self.assertTrue((self.target / 'Artist' / 'Album' / '01.wav').exists())

    def test_failed_replacement_copy_keeps_previous_destination_file(self):
        import music_transfer.app as app_module

        track = self.album['tracks'][0]
        previous = self.target / 'Artist' / 'Album' / '01.mp3'
        make_wave(previous, number='01')
        self.backend.setFormatPolicy('copy_and_delete')
        with patch.object(app_module, 'copy_audio', side_effect=OSError('copy failed')):
            self.backend.send(self.album['id'], [track['path']])
            wait_for(lambda: not self.backend.running)
        self.assertTrue(previous.exists())
        self.assertFalse((self.target / 'Artist' / 'Album' / '01.wav').exists())
        self.assertIn('Failed:', self.backend._results[0]['message'])

    def test_bitrate_policies_select_source_or_destination_version(self):
        import music_transfer.app as app_module

        original_read_track = app_module.read_track
        cases = (
            ('keep_higher_bitrate', 256000, 128000, True),
            ('keep_higher_bitrate', 64000, 128000, False),
            ('keep_lower_bitrate', 64000, 128000, True),
            ('keep_lower_bitrate', 256000, 128000, False),
        )
        self.backend.setConflictPolicy('always_overwrite')
        for index, (policy, source_rate, target_rate, source_wins) in enumerate(cases):
            source_track = self.album['tracks'][index % len(self.album['tracks'])]
            source = Path(source_track['path'])
            suffix = source.stem + '.mp3'
            previous = self.target / 'Artist' / 'Album' / suffix
            make_wave(previous, number=source.stem)
            output = self.target / 'Artist' / 'Album' / source.name
            output.unlink(missing_ok=True)
            rates = {str(source): source_rate, str(previous): target_rate}

            def read_with_test_bitrate(path, root):
                result = original_read_track(path, root)
                result['bitrate'] = rates.get(str(path), result['bitrate'])
                return result

            self.backend.setFormatPolicy(policy)
            with patch('music_transfer.app.read_track', side_effect=read_with_test_bitrate):
                self.backend.send(self.album['id'], [source_track['path']])
                wait_for(lambda: not self.backend.running)
            self.assertEqual(output.exists(), source_wins)
            self.assertEqual(previous.exists(), not source_wins)

    def test_unknown_bitrate_prompts_and_keep_existing_preserves_destination(self):
        import music_transfer.app as app_module

        track = self.album['tracks'][0]
        previous = self.target / 'Artist' / 'Album' / '01.mp3'
        make_wave(previous, number='01')
        original_read_track = app_module.read_track

        def without_bitrate(path, root):
            result = original_read_track(path, root)
            result['bitrate'] = None
            return result

        self.backend.setFormatPolicy('keep_higher_bitrate')
        with patch('music_transfer.app.read_track', side_effect=without_bitrate):
            self.backend.send(self.album['id'], [track['path']])
            wait_for(lambda: self.backend._conflicts and self.backend._conflicts[0]['kind'] == 'bitrate')
            self.backend.resolveFormatConflict(str(self.target / 'Artist/Album/01.wav'), 'keep_existing', '')
            wait_for(lambda: not self.backend.running)
        self.assertTrue(previous.exists())
        self.assertFalse((self.target / 'Artist' / 'Album' / '01.wav').exists())

    def test_front_cover_preferred_across_tracks(self):
        first = self.source / 'Artist/Album/01.wav'
        second = self.source / 'Artist/Album/02.wav'
        make_wave(first, front=False, color='red')
        make_wave(second, front=True, color='blue')
        self.before = snapshot(self.source)
        image = cover_image([first, second])
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))

    def test_duplicate_and_pending_removal(self):
        # Hold the worker at its first copy while editing the rest of the queue.
        import threading
        entered, release = threading.Event(), threading.Event()
        from music_transfer.core import copy_audio
        def held(*args):
            entered.set(); release.wait(3); return copy_audio(*args)
        with patch('music_transfer.app.copy_audio', side_effect=held):
            self.backend.send(self.album['id'], [])
            self.assertTrue(entered.wait(2))
            self.backend.send(self.album['id'], [])
            self.assertEqual(len(self.backend._queue), 3)
            self.backend.remove(self.album['tracks'][2]['path'], False)
            wait_for(lambda: len(self.backend._queue) == 2)
            release.set()
            wait_for(lambda: not self.backend.running)
        self.assertFalse((self.target / 'Artist/Album/03.wav').exists())

class RecoveryTests(AppFixture):
    def test_disk_full_pauses_and_resumes(self):
        import errno
        from music_transfer.core import copy_audio
        calls = [0]
        def full_once(*args):
            calls[0] += 1
            if calls[0] == 1:
                raise OSError(errno.ENOSPC, 'Disk full')
            return copy_audio(*args)
        with patch('music_transfer.app.copy_audio', side_effect=full_once):
            self.backend.send(self.album['id'], [self.album['tracks'][0]['path']])
            wait_for(lambda: self.backend.paused)
            self.backend.resume()
            wait_for(lambda: not self.backend.running)
        self.assertTrue((self.target / 'Artist/Album/01.wav').exists())

    def test_active_album_cancel_keeps_completed_files(self):
        import threading
        from music_transfer.core import copy_audio
        entered, release = threading.Event(), threading.Event()
        calls = [0]
        def held(*args):
            calls[0] += 1
            if calls[0] == 2:
                entered.set(); release.wait(3)
            return copy_audio(*args)
        with patch('music_transfer.app.copy_audio', side_effect=held):
            self.backend.send(self.album['id'], [])
            self.assertTrue(entered.wait(2))
            self.backend.remove(self.album['id'], True)
            release.set()
            wait_for(lambda: not self.backend.running)
        self.assertTrue((self.target / 'Artist/Album/01.wav').exists())
        self.assertFalse((self.target / 'Artist/Album/02.wav').exists())
        self.assertFalse((self.target / 'Artist/Album/03.wav').exists())
        self.assertFalse(list(self.target.rglob('.music-transfer-*')))

    def test_disappeared_destination_resumes(self):
        self.target.rmdir()
        # Enqueue refuses missing targets before a run starts.
        self.backend.send(self.album['id'], [])
        wait_for(lambda: not self.backend.running)

    def test_stale_conflict_answer_does_not_apply_to_next_file(self):
        first = self.existing('01.wav'); second = self.existing('02.wav')
        self.backend.send(self.album['id'], [])
        wait_for(lambda: self.backend.conflictCount == 2)
        self.backend.remove(self.album['tracks'][0]['path'], False)
        wait_for(lambda: self.backend.conflictCount == 1)
        self.backend.resolveConflict(str(first), True, False)
        self.assertEqual(self.backend.conflictCount, 1)
        self.assertEqual(second.read_bytes(), b'existing')
        self.backend.resolveConflict(str(second), False, False)
        wait_for(lambda: not self.backend.running)


if __name__ == '__main__':
    unittest.main()
