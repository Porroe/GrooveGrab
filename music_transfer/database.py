"""Worker-owned SQLite index. No files are written inside a music library."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time

from .core import EXTENSIONS, Cancelled, read_track

SCHEMA_VERSION = 1


class InvalidIndex(sqlite3.DatabaseError):
    pass


def database_path():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'music-transfer/library.sqlite3'


class LibraryIndex:
    def __init__(self, path=None):
        self.path = Path(path or database_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.recovered = None
        try:
            self.connection = self._open()
        except sqlite3.DatabaseError as error:
            if not isinstance(error, InvalidIndex) and getattr(error, 'sqlite_errorcode', None) not in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                self.connection.close()
                raise
            if hasattr(self, 'connection'):
                self.connection.close()
            stamp = str(time.time_ns())
            self.recovered = self.path.with_name(self.path.name + '.unusable-' + stamp)
            self.path.rename(self.recovered)
            for suffix in ('-wal', '-shm'):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.exists():
                    sidecar.rename(Path(str(self.recovered) + suffix))
            self.connection = self._open()

    def _open(self):
        db = sqlite3.connect(self.path, timeout=10)
        self.connection = db
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            raise InvalidIndex('Unsupported index version')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise InvalidIndex('Invalid index')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS libraries (root TEXT PRIMARY KEY, device INTEGER, scanned_at REAL);
            CREATE TABLE IF NOT EXISTS tracks (
                root TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL, metadata TEXT NOT NULL,
                PRIMARY KEY(root, path));
            CREATE TABLE IF NOT EXISTS artwork (
                root TEXT NOT NULL, album_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                url TEXT NOT NULL, PRIMARY KEY(root, album_id));
        ''')
        required = {'libraries': {'root', 'device', 'scanned_at'}, 'tracks': {'root', 'path', 'size', 'mtime_ns', 'metadata'}, 'artwork': {'root', 'album_id', 'fingerprint', 'url'}}
        for table, columns in required.items():
            actual = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
            if not columns <= actual:
                raise InvalidIndex('Incompatible index schema')
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        db.commit()
        return db

    def close(self):
        self.connection.close()

    def load(self, root):
        return {row[0]: dict(json.loads(row[3]), size=row[1], mtime_ns=row[2]) for row in
                self.connection.execute('SELECT path,size,mtime_ns,metadata FROM tracks WHERE root=?', (str(root),))}

    def artwork(self, root):
        return {album_id: (fingerprint, url) for album_id, fingerprint, url in self.connection.execute(
            'SELECT album_id,fingerprint,url FROM artwork WHERE root=?', (str(root),))}

    def save_artwork(self, root, album_id, fingerprint, url):
        with self.connection:
            self.connection.execute('INSERT OR REPLACE INTO artwork VALUES (?,?,?,?)',
                                    (str(root), album_id, fingerprint, url))

    def refresh(self, root, *, cancelled=lambda: False, progress=lambda *args: None,
                batch=lambda tracks, removed: None):
        root = Path(root).resolve()
        cached = self.load(root)
        def check():
            if cancelled():
                raise Cancelled()
        check()
        # An unavailable mount must never turn a saved library into an empty one.
        device = root.stat().st_dev
        saved = self.connection.execute('SELECT device FROM libraries WHERE root=?', (str(root),)).fetchone()
        if saved and saved[0] != device:
            raise OSError('Library device changed or is unavailable; saved index retained')
        with os.scandir(root):
            pass
        discovered, unreadable = [], []
        last = [0.0]
        def report(stage, done, total=0, force=False):
            now = time.monotonic()
            if force or now - last[0] >= .1:
                last[0] = now
                progress(stage, done, total)
        def failed(error):
            unreadable.append(Path(error.filename or root))
        report('Discovering files', 0, force=True)
        for parent, dirs, files in os.walk(root, followlinks=False, onerror=failed):
            check()
            dirs.sort()
            for filename in sorted(files):
                check()
                path = Path(parent) / filename
                if path.suffix.lower() in EXTENSIONS and not path.is_symlink():
                    discovered.append(path)
                    report('Discovering files', len(discovered))
        updated, changes, persisted = {}, [], []
        report('Updating metadata', 0, len(discovered), True)
        for count, path in enumerate(discovered, 1):
            check()
            relative = str(path.relative_to(root))
            try:
                stat = path.stat()
                old = cached.get(relative)
                if (old and old['size'] == stat.st_size and old['mtime_ns'] == stat.st_mtime_ns
                        and 'disc_number' in old and 'disc_total' in old and 'disc_tag' in old):
                    track = old
                else:
                    track = dict(read_track(path, root), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
                    changes.append(track)
                    persisted.append((relative, track))
                updated[relative] = track
            except OSError:
                # Do not lose records for files which temporarily cannot be read.
                if relative in cached:
                    updated[relative] = cached[relative]
            if len(changes) >= 100:
                batch(changes, [])
                changes = []
            report('Updating metadata', count, len(discovered))
        for relative, track in cached.items():
            if any((root / relative).is_relative_to(folder) for folder in unreadable):
                updated.setdefault(relative, track)
        check()
        # Recheck device after walking, before committing any deletions.
        if root.stat().st_dev != device:
            raise OSError('Library disconnected during refresh; saved index retained')
        removed = [relative for relative in cached if relative not in updated]
        with self.connection:
            self.connection.execute('INSERT OR REPLACE INTO libraries VALUES (?,?,?)', (str(root), device, time.time()))
            self.connection.executemany('INSERT OR REPLACE INTO tracks VALUES (?,?,?,?,?)',
                ((str(root), relative, track['size'], track['mtime_ns'], json.dumps(track))
                 for relative, track in persisted))
            self.connection.executemany('DELETE FROM tracks WHERE root=? AND path=?', ((str(root), p) for p in removed))
            check()
        batch(changes, removed)
        report('Updating metadata', len(discovered), len(discovered), True)
        return updated, unreadable
