import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
from types import SimpleNamespace
from PIL import Image
from mutagen.id3 import ID3, TIT2, TALB, TPE2, APIC, TPOS
from mutagen.wave import WAVE
from music_transfer.core import (scan, ArtworkSettings, ThumbnailCache, overlaps,
    destination, copy_audio, Cancelled, jpeg_bytes, cover_image, atomic_write, read_track)


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def fixture(path, color='red', size=(40, 20)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = io.BytesIO()
    Image.new('RGB', size, color).save(image, 'JPEG')
    tags = ID3()
    tags.add(TIT2(encoding=3, text=path.stem))
    tags.add(TALB(encoding=3, text='Album'))
    tags.add(TPE2(encoding=3, text='Artist'))
    tags.add(APIC(encoding=3, mime='image/jpeg', type=3, data=image.getvalue()))
    tags.save(path)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.source.mkdir(); self.target.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_nested_copy_and_source_unchanged(self):
        track = self.source / 'Artist/Album/song.flac'
        track.parent.mkdir(parents=True)
        track.write_bytes(b'audio' * 10000)
        before = snapshot(self.source)
        albums = scan(self.source)
        self.assertEqual(len(albums), 1)
        output = destination(track, self.source, self.target)
        copy_audio(track, output, lambda: False, lambda _: None)
        self.assertEqual(output.read_bytes(), track.read_bytes())
        self.assertEqual(snapshot(self.source), before)
        self.assertFalse(list(self.target.rglob('.music-transfer-*')))

    def test_overlap_and_escape(self):
        self.assertTrue(overlaps(self.source, self.source / 'nested'))
        self.assertTrue(overlaps(self.source, self.root))
        with self.assertRaises(ValueError):
            destination(self.root / 'outside.mp3', self.source, self.target)
        (self.target / 'escape').symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            destination(self.source / 'escape/song.mp3', self.source, self.target)

    def test_cancellation_preserves_existing(self):
        track = self.source / 'song.mp3'; track.write_bytes(b'x' * 3000000)
        output = self.target / 'song.mp3'; output.write_bytes(b'original')
        with self.assertRaises(Cancelled):
            copy_audio(track, output, lambda: True, lambda _: None)
        self.assertEqual(output.read_bytes(), b'original')
        self.assertEqual(list(self.target.iterdir()), [output])

    def test_write_failure_preserves_existing(self):
        output = self.target / 'song.mp3'; output.write_bytes(b'original')
        def fail(stream):
            stream.write(b'partial'); raise OSError('Disk full')
        with self.assertRaises(OSError):
            atomic_write(output, fail)
        self.assertEqual(output.read_bytes(), b'original')
        self.assertEqual(list(self.target.iterdir()), [output])

    def test_artwork_no_upscale_and_proportions(self):
        for size, maximum, expected in [((40, 20), (500, 500), (40, 20)), ((1000, 500), (300, 300), (300, 150))]:
            result = jpeg_bytes(Image.new('RGB', size), *maximum)
            self.assertEqual(Image.open(io.BytesIO(result)).size, expected)

    def test_settings_validation(self):
        for filename in ('../folder.jpg', 'a/b.jpg', 'a\\b.jpg', 'cover.png', ''):
            with self.assertRaises(ValueError):
                ArtworkSettings(filename=filename).validate()
        with self.assertRaises(ValueError):
            ArtworkSettings(width=0).validate()
        ArtworkSettings().validate()

    def test_cache_invalidation_and_clear_without_source_changes(self):
        path = self.source / 'song.mp3'; path.write_bytes(b'a')
        cache = ThumbnailCache(self.root / 'cache')
        before = snapshot(self.source)
        with patch('music_transfer.core.cover_image', return_value=Image.new('RGB', (40, 20))):
            first = cache.get([path]); second = cache.get([path])
            self.assertEqual(first, second)
            self.assertEqual(snapshot(self.source), before)
            path.write_bytes(b'changed')
            self.assertNotEqual(first, cache.get([path]))
        cache.clear()
        self.assertEqual(list(cache.directory.iterdir()), [])

    def test_unreadable_metadata_and_symlinks(self):
        (self.source / 'broken.mp3').write_bytes(b'invalid')
        (self.source / 'linked.mp3').symlink_to(self.source / 'broken.mp3')
        albums = scan(self.source)
        self.assertEqual(len(albums[0]['tracks']), 1)
        self.assertEqual(albums[0]['tracks'][0]['title'], 'broken')

    def test_track_metadata_marks_real_tags_and_missing_track_number(self):
        path = self.source / 'song.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0' * 800)
        audio = WAVE(path)
        audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text='Song'))
        audio.tags.add(TALB(encoding=3, text='Album'))
        audio.tags.add(TPE2(encoding=3, text='Artist'))
        audio.save()
        track = read_track(path, self.source)
        self.assertEqual(track['album'], 'Album')
        self.assertEqual(track['artist'], 'Artist')
        self.assertEqual(track['title'], 'Song')
        self.assertTrue(all(track['tagged'][key] for key in ('album', 'artist', 'title')))
        self.assertFalse(track['tagged']['number'])
        self.assertGreater(track['bitrate'], 0)

    def test_track_metadata_reads_disc_number_and_total(self):
        path = self.source / 'disc-track.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0' * 800)
        audio = WAVE(path)
        audio.add_tags()
        audio.tags.add(TPOS(encoding=3, text='2/3'))
        audio.save()

        track = read_track(path, self.source)
        self.assertEqual(track['disc_number'], 2)
        self.assertEqual(track['disc_total'], 3)
        self.assertEqual(track['disc_tag'], '2/3')
        self.assertTrue(track['tagged']['disc'])

    def test_track_metadata_reads_mp4_disc_pair(self):
        path = self.source / 'mp4-tagged.m4a'
        path.write_bytes(b'fake audio')
        with patch('music_transfer.core.mutagen.File', return_value=SimpleNamespace(
                tags={'disk': [(2, 4)]}, info=None)):
            track = read_track(path, self.source)
        self.assertEqual(track['disc_number'], 2)
        self.assertEqual(track['disc_total'], 4)
        self.assertEqual(track['disc_tag'], '2/4')
        self.assertTrue(track['tagged']['disc'])


if __name__ == '__main__':
    unittest.main()
