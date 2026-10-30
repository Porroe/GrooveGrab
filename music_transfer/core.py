from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from dataclasses import dataclass, asdict

import mutagen
from PIL import Image, ImageOps

EXTENSIONS = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.aif', '.aiff'}


def overlaps(source: Path, target: Path) -> bool:
    source, target = source.resolve(), target.resolve()
    return source == target or source in target.parents or target in source.parents


def destination(source: Path, root: Path, target: Path) -> Path:
    relative = source.resolve().relative_to(root.resolve())
    result = target / relative
    result.resolve().relative_to(target.resolve())
    return result


@dataclass(frozen=True)
class ArtworkSettings:
    enabled: bool = True
    filename: str = 'folder.jpg'
    width: int = 500
    height: int = 500

    def validate(self):
        if (not self.filename or Path(self.filename).name != self.filename
                or '/' in self.filename or '\\' in self.filename
                or Path(self.filename).suffix.lower() not in {'.jpg', '.jpeg'}):
            raise ValueError('Artwork name must be a plain .jpg or .jpeg filename.')
        if not 1 <= self.width <= 16384 or not 1 <= self.height <= 16384:
            raise ValueError('Artwork dimensions must be between 1 and 16384 pixels.')


def tag_value(tags, keys, fallback):
    for key in keys:
        value = tags.get(key) if tags else None
        if value:
            if hasattr(value, 'text'):
                value = value.text
            if isinstance(value, (list, tuple)):
                value = value[0]
            if isinstance(value, (list, tuple)):
                value = value[0]
            return str(value)
    return fallback


def tag_number_value(tags, keys):
    for key in keys:
        value = tags.get(key) if tags else None
        if not value:
            continue
        if hasattr(value, 'text'):
            value = value.text
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            value = value[0]
            if isinstance(value, (list, tuple)):
                current = str(value[0]) if value else ''
                total = str(value[1]) if len(value) > 1 else ''
                return current + ('/' + total if total else '')
        return str(value)
    return ''


def read_track(path: Path, root: Path):
    try:
        audio = mutagen.File(path)
        tags = audio.tags if audio else None
        info = audio.info if audio else None
    except Exception:
        tags = None
        info = None
    artist_keys = ('albumartist', 'TPE2', 'aART', 'artist', 'TPE1', '\xa9ART')
    album_keys = ('album', 'TALB', '\xa9alb')
    title_keys = ('title', 'TIT2', '\xa9nam')
    number_keys = ('tracknumber', 'TRCK', 'trkn')
    disc_keys = ('discnumber', 'TPOS', 'disk', '\xa9dis', 'WM/PartOfSet', 'DISKNUMBER')
    artist = tag_value(tags, artist_keys, 'Unknown artist')
    album = tag_value(tags, album_keys, path.parent.name)
    title = tag_value(tags, title_keys, path.stem)
    number = tag_value(tags, number_keys, '')
    disc = tag_number_value(tags, disc_keys)

    def number_parts(value):
        parts = value.split('/', 1)
        try:
            current = max(int(parts[0].strip()), 0)
        except ValueError:
            current = 0
        try:
            total = max(int(parts[1].strip()), 0) if len(parts) == 2 else 0
        except ValueError:
            total = 0
        return current, total

    track_number, _ = number_parts(number)
    disc_number, disc_total = number_parts(disc)
    try:
        bitrate = int(info.bitrate) if info and info.bitrate else None
        if bitrate is not None and bitrate <= 0:
            bitrate = None
    except (AttributeError, TypeError, ValueError, OverflowError):
        bitrate = None
    return dict(path=str(path), relative=str(path.relative_to(root)), title=title,
                artist=artist, album=album, folder=str(path.parent), number=number,
                track_number=track_number, disc_number=disc_number, disc_total=disc_total,
                disc_tag=disc,
                bitrate=bitrate,
                tagged=dict(artist=bool(tag_value(tags, artist_keys, '').strip()),
                            album=bool(tag_value(tags, album_keys, '').strip()),
                            title=bool(tag_value(tags, title_keys, '').strip()),
                            number=bool(number.strip()), disc=bool(disc.strip())))


def scan(root: Path):
    albums = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            path = Path(parent) / name
            if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                continue
            track = read_track(path, root)
            key = (track['artist'], track['album'], track['folder'])
            if key not in albums:
                albums[key] = dict(id=hashlib.sha256(repr(key).encode()).hexdigest(), title=track['album'], artist=track['artist'], folder=track['folder'], tracks=[], cover='')
            albums[key]['tracks'].append(track)
    return sorted(albums.values(), key=lambda a: (a['artist'].casefold(), a['title'].casefold(), a['folder']))


def embedded_images(path: Path):
    try:
        audio = mutagen.File(path)
        if audio is None:
            return []
        pictures = list(getattr(audio, 'pictures', []))
        tags = audio.tags
        if tags:
            if hasattr(tags, 'getall'):
                pictures.extend(tags.getall('APIC'))
            for raw in tags.get('metadata_block_picture', []):
                import base64
                from mutagen.flac import Picture
                try:
                    pictures.append(Picture(base64.b64decode(raw)))
                except Exception:
                    pass
            covers = tags.get('covr', [])
        else:
            covers = []
        pictures.sort(key=lambda p: 0 if getattr(p, 'type', None) == 3 else 1)
        return [(getattr(p, 'type', None) == 3, p.data) for p in pictures] + [(True, bytes(c)) for c in covers]
    except Exception:
        return []


def cover_image(paths):
    fallback = None
    for path in sorted(map(Path, paths)):
        for front, data in embedded_images(path):
            if not front and fallback is not None:
                continue
            try:
                with Image.open(io.BytesIO(data)) as image:
                    decoded = ImageOps.exif_transpose(image).convert('RGB')
                if front:
                    return decoded
                fallback = decoded
            except Exception:
                continue
    return fallback


def jpeg_bytes(image, width, height):
    image = image.copy()
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=90)
    return buffer.getvalue()


class ThumbnailCache:
    def __init__(self, directory=None):
        self.directory = Path(directory or Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'music-transfer' / 'covers')
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, paths):
        fingerprint = []
        for path in sorted(map(Path, paths)):
            try:
                stat = path.stat()
                fingerprint.append((str(path), stat.st_size, stat.st_mtime_ns))
            except OSError:
                pass
        key = hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()
        output = self.directory / (key + '.jpg')
        if output.exists():
            return output.as_uri()
        image = cover_image(paths)
        if image is None:
            return ''
        output.write_bytes(jpeg_bytes(image, 320, 320))
        return output.as_uri()

    def clear(self):
        for path in self.directory.glob('*.jpg'):
            path.unlink(missing_ok=True)


class Cancelled(Exception):
    pass


def atomic_write(output: Path, writer, cancelled=lambda: False, validate=lambda: None):
    validate()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.music-transfer-', dir=output.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            raise Cancelled()
        validate()
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def copy_audio(source, output, cancelled, progress, validate=lambda: None):
    size = source.stat().st_size
    def write(stream):
        copied = 0
        with source.open('rb') as incoming:
            while chunk := incoming.read(1024 * 1024):
                if cancelled():
                    raise Cancelled()
                stream.write(chunk)
                copied += len(chunk)
                progress(copied / max(size, 1))
    atomic_write(output, write, cancelled, validate)
