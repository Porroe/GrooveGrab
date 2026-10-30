from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from PySide6.QtCore import QObject, Signal, Slot, Property, QUrl, QSettings, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from .core import (ArtworkSettings, ThumbnailCache, overlaps, destination,
                   copy_audio, atomic_write, cover_image, jpeg_bytes, read_track, EXTENSIONS, Cancelled)
from .database import LibraryIndex, database_path
from .models import RecordModel


class ConflictPending(Exception):
    pass


CONFLICT_POLICIES = (
    'always_skip',
    'always_overwrite',
    'overwrite_if_size_different',
    'ask_if_size_different',
    'ask_every_time',
)

FORMAT_POLICIES = (
    'always_copy',
    'copy_and_delete',
    'keep_higher_bitrate',
    'keep_lower_bitrate',
    'ask_every_time',
)

SAME_FORMAT_POLICIES = (
    'always_copy',
    'keep_existing',
    'replace_existing',
    'ask_every_time',
)


def album_id(track):
    return hashlib.sha256(repr((track['artist'], track['album'], track['folder'])).encode()).hexdigest()


def group_tracks(tracks):
    albums = {}
    for track in tracks:
        identifier = album_id(track)
        album = albums.setdefault(identifier, dict(id=identifier, title=track['album'], artist=track['artist'],
                                                   folder=track['folder'], tracks=[], cover=''))
        album['tracks'].append(track)
    for album in albums.values():
        album['tracks'].sort(key=lambda t: t['path'])
    return sorted(albums.values(), key=lambda a: (a['artist'].casefold(), a['title'].casefold(), a['folder']))


def fingerprint(album):
    return hashlib.sha256(json.dumps([(t['path'], t.get('size'), t.get('mtime_ns')) for t in album['tracks']]).encode()).hexdigest()


class Backend(QObject):
    changed = Signal()
    libraryChanged = Signal()
    selectedAlbumCoverChanged = Signal()
    libraryReset = Signal()
    libraryProgressChanged = Signal()
    conflict = Signal(str)
    formatConflict = Signal(str, str, object)
    notify = Signal(str)
    renderReady = Signal(object)
    folderChosen = Signal(object)
    coverReady = Signal(object)
    cacheCleared = Signal()
    shutdownFinished = Signal()

    def __init__(self, index_path=None):
        super().__init__()
        self.settings = QSettings('MusicTransfer', 'MusicTransfer')
        self._source = self.settings.value('source', '')
        self._target = self.settings.value('target', '')
        self._theme = self.settings.value('theme', 'system')
        conflict_policy = self.settings.value('conflict_policy', 'ask_every_time')
        self._conflict_policy = conflict_policy if conflict_policy in CONFLICT_POLICIES else 'ask_every_time'
        format_policy = self.settings.value('format_policy', 'ask_every_time')
        self._format_policy = format_policy if format_policy in FORMAT_POLICIES else 'ask_every_time'
        same_format_policy = self.settings.value('same_format_policy', 'ask_every_time')
        self._same_format_policy = same_format_policy if same_format_policy in SAME_FORMAT_POLICIES else 'ask_every_time'
        self._art = ArtworkSettings(**json.loads(self.settings.value('artwork', '{}')))
        self.index_path = Path(index_path or database_path())
        self._albums, self._queue, self._results = [], [], []
        self.catalog = {}
        self._covers = {}
        self._album_totals, self._album_done = {}, {}
        self._scanning = self._running = self._paused = False
        self._preparing = 0
        self._progress = 0.0
        self._status = 'Ready to transfer music'
        self._library_status = 'Choose your music library to get started'
        self._library_stage = ''
        self._library_done = self._library_total = 0
        self._library_progress = 0.0
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.cover_condition = threading.Condition()
        self.catalog_lock = threading.RLock()
        self.mailbox_lock = threading.Lock()
        self.queue_collapsed = set()
        self._pending_history = deque()
        self.apply = None
        self._conflicts, self._artwork_folders = [], {}
        self._active_job = None
        self.cache = ThumbnailCache()
        self.cache_lock = threading.Lock()
        self.cache_generation = 0
        self.stopping = False
        self.transfer_thread = None
        self.scan_thread = None
        self.scan_cancel = threading.Event()
        self.scan_generation = 0
        self.cover_pending = set()
        self.cover_jobs = deque()
        self.commands = ThreadPoolExecutor(max_workers=1, thread_name_prefix='queue-command')
        self.queries = ThreadPoolExecutor(max_workers=1, thread_name_prefix='library-query')
        self.query_generation = 0
        self.query_text, self.query_artist = '', 'All artists'
        self.collapsed_folders = set()
        self.transfer_dirty = threading.Event()
        self._progress_message = None
        self._new_results = deque()
        self._result_counter = 0
        self._queue_models = {}
        self._selected_album_id = ''
        self.album_model = RecordModel(self)
        self.track_model = RecordModel(self, key='path')
        self.folder_model = RecordModel(self, key='path')
        self.artist_model = RecordModel(self, key='name')
        self.queue_model = RecordModel(self)
        self.history_model = RecordModel(self)
        self.library_events = deque()
        self.renderReady.connect(self.accept_render)
        self.folderChosen.connect(self.accept_folder)
        self.coverReady.connect(self.cover_loaded)
        self.cacheCleared.connect(self.cache_cleared)
        self.cover_thread = threading.Thread(target=self.cover_worker, daemon=True)
        self.cover_thread.start()
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(100)
        self.ui_timer.timeout.connect(self.poll_workers)
        self.ui_timer.start()
        self.search_timer = QTimer(self)
        self.search_timer.setInterval(180)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.schedule_query)
        self._last_root = ''
        self._shutdown_started = False
        self._shutdown_complete = False

    shutdownComplete = Property(bool, lambda s: s._shutdown_complete, notify=shutdownFinished)
    source = Property(str, lambda s: s._source, notify=changed)
    target = Property(str, lambda s: s._target, notify=changed)
    theme = Property(str, lambda s: s._theme, notify=changed)
    conflictPolicy = Property(str, lambda s: s._conflict_policy, notify=changed)
    formatPolicy = Property(str, lambda s: s._format_policy, notify=changed)
    sameFormatPolicy = Property(str, lambda s: s._same_format_policy, notify=changed)
    albums = Property(QObject, lambda s: s.album_model, constant=True)
    tracks = Property(QObject, lambda s: s.track_model, constant=True)
    folders = Property(QObject, lambda s: s.folder_model, constant=True)
    artists = Property(QObject, lambda s: s.artist_model, constant=True)
    queue = Property(QObject, lambda s: s.queue_model, constant=True)
    results = Property(QObject, lambda s: s.history_model, constant=True)
    scanning = Property(bool, lambda s: s._scanning, notify=libraryProgressChanged)
    running = Property(bool, lambda s: s._running or s._preparing > 0, notify=changed)
    paused = Property(bool, lambda s: s._paused, notify=changed)
    conflictCount = Property(int, lambda s: len(s._conflicts), notify=changed)
    progress = Property(float, lambda s: s._progress, notify=changed)
    status = Property(str, lambda s: s._status, notify=changed)
    artwork = Property('QVariantMap', lambda s: asdict(s._art), notify=changed)
    selectedAlbumCover = Property(str, lambda s: s.selected_album_cover(), notify=selectedAlbumCoverChanged)
    libraryStatus = Property(str, lambda s: s._library_status, notify=libraryProgressChanged)
    libraryStage = Property(str, lambda s: s._library_stage, notify=libraryProgressChanged)
    libraryDone = Property(int, lambda s: s._library_done, notify=libraryProgressChanged)
    libraryTotal = Property(int, lambda s: s._library_total, notify=libraryProgressChanged)
    libraryProgress = Property(float, lambda s: s._library_progress, notify=libraryProgressChanged)

    @Slot(str)
    def setTheme(self, theme):
        self._theme = theme
        self.settings.setValue('theme', theme)
        self.changed.emit()

    @Slot(str)
    def setConflictPolicy(self, policy):
        if policy not in CONFLICT_POLICIES:
            self.notify.emit('Choose a valid existing-file setting.')
            return
        self._conflict_policy = policy
        self.settings.setValue('conflict_policy', policy)
        self.changed.emit()

    @Slot(str)
    def setFormatPolicy(self, policy):
        if policy not in FORMAT_POLICIES:
            self.notify.emit('Choose a valid different-format setting.')
            return
        self._format_policy = policy
        self.settings.setValue('format_policy', policy)
        self.changed.emit()

    @Slot(str)
    def setSameFormatPolicy(self, policy):
        if policy not in SAME_FORMAT_POLICIES:
            self.notify.emit('Choose a valid same-format setting.')
            return
        self._same_format_policy = policy
        self.settings.setValue('same_format_policy', policy)
        self.changed.emit()

    @Slot(str, str, str, bool, str, int, int, result=bool)
    def saveSettings(self, conflict_policy, format_policy, same_format_policy,
                     artwork_enabled, artwork_filename, artwork_width, artwork_height):
        if conflict_policy not in CONFLICT_POLICIES:
            self.notify.emit('Choose a valid existing-file setting.')
            return False
        if format_policy not in FORMAT_POLICIES:
            self.notify.emit('Choose a valid different-format setting.')
            return False
        if same_format_policy not in SAME_FORMAT_POLICIES:
            self.notify.emit('Choose a valid same-format setting.')
            return False
        try:
            artwork = ArtworkSettings(artwork_enabled, artwork_filename, artwork_width, artwork_height)
            artwork.validate()
        except ValueError as error:
            self.notify.emit(str(error))
            return False

        self._conflict_policy = conflict_policy
        self._format_policy = format_policy
        self._same_format_policy = same_format_policy
        self._art = artwork
        self.settings.setValue('conflict_policy', conflict_policy)
        self.settings.setValue('format_policy', format_policy)
        self.settings.setValue('same_format_policy', same_format_policy)
        self.settings.setValue('artwork', json.dumps(asdict(artwork)))
        self.changed.emit()
        self.notify.emit('Settings saved.')
        return True

    @Slot(str, bool)
    def chooseFolder(self, url, source):
        if self.running or (source and self._scanning) or self.stopping:
            return
        other = self._target if source else self._source
        def work():
            try:
                path = Path(QUrl(url).toLocalFile()).resolve()
                if not path.is_dir():
                    raise ValueError('Choose an existing folder.')
                if other and overlaps(path, Path(other)):
                    raise ValueError('Source and destination must be separate, non-overlapping folders.')
                if source:
                    protected = [self.cache.directory.resolve(), Path(self.settings.fileName()).resolve(), self.index_path.resolve()]
                    if any(p == path or path in p.parents for p in protected):
                        raise ValueError('Choose a music subfolder outside the app’s settings, database, and cache.')
                self.folderChosen.emit((str(path), source))
            except Exception as error:
                self.notify.emit(str(error))
        self.commands.submit(work)

    @Slot(object)
    def accept_folder(self, result):
        path, source = result
        if self.running or (source and self._scanning) or self.stopping:
            return
        if source:
            self._source = path
        else:
            self._target = path
        self.settings.setValue('source' if source else 'target', path)
        self.changed.emit()
        if source:
            self.rescan()

    def post_library(self, event):
        with self.mailbox_lock:
            self.library_events.append(event)

    @Slot()
    def rescan(self):
        if self._scanning or not self._source or self.stopping:
            return
        self.scan_generation += 1
        generation = self.scan_generation
        root = self._source
        if self._last_root != root:
            self.query_generation += 1
            self._selected_album_id = ''
            self.libraryReset.emit()
            with self.catalog_lock:
                self.catalog = {}
            self.album_model.submit([])
            self.folder_model.submit([])
            self.track_model.submit([])
            self.cover_pending.clear()
            self._last_root = root
        self._scanning = True
        self.scan_cancel = threading.Event()
        cancel = self.scan_cancel
        self._library_stage = 'Loading saved library'
        self._library_status = 'Loading saved library…'
        self._library_progress = 0
        self._library_done = self._library_total = 0
        self.libraryProgressChanged.emit()
        def work():
            index = None
            try:
                resolved = Path(root).resolve()
                protected = [self.index_path.resolve(), self.cache.directory.resolve(), Path(self.settings.fileName()).resolve()]
                if any(p == resolved or resolved in p.parents for p in protected):
                    raise ValueError('Music source must not contain app data, settings, or cache')
                index = LibraryIndex(self.index_path)
                if index.recovered:
                    self.notify.emit('Saved an unusable database as ' + str(index.recovered) + '; rebuilding the index.')
                cached = index.load(root)
                saved_covers = index.artwork(root)
                tracks = dict(cached)
                initial = group_tracks(tracks.values())
                grouped = {a['id']: {t['path']: t for t in a['tracks']} for a in initial}
                pending = set()
                last_publish = [time.monotonic()]
                self.publish_catalog(initial, root, generation, saved_covers)
                def progress(stage, done, total):
                    self.post_library((generation, 'progress', stage, done, total))
                def flush():
                    upserts, removed_albums = [], []
                    for identifier in pending:
                        members = grouped.get(identifier, {})
                        if not members:
                            removed_albums.append(identifier)
                            continue
                        ordered = sorted(members.values(), key=lambda t: t['path'])
                        first = ordered[0]
                        upserts.append(dict(id=identifier, title=first['album'], artist=first['artist'], folder=first['folder'], tracks=ordered, cover=''))
                    if upserts or removed_albums:
                        self.publish_delta(upserts, removed_albums, generation, saved_covers)
                    pending.clear()
                    last_publish[0] = time.monotonic()
                def batch(changes, removed):
                    for track in changes:
                        old = tracks.get(track['relative'])
                        if old:
                            old_id = album_id(old)
                            grouped[old_id].pop(old['path'], None)
                            pending.add(old_id)
                        identifier = album_id(track)
                        grouped.setdefault(identifier, {})[track['path']] = track
                        pending.add(identifier)
                        tracks[track['relative']] = track
                    for relative in removed:
                        old = tracks.pop(relative, None)
                        if old:
                            identifier = album_id(old)
                            grouped[identifier].pop(old['path'], None)
                            pending.add(identifier)
                    if time.monotonic() - last_publish[0] >= .1:
                        flush()
                refreshed, unreadable = index.refresh(root, cancelled=cancel.is_set, progress=progress, batch=batch)
                flush()
                note = f' · {len(unreadable)} unreadable folders retained' if unreadable else ''
                self.post_library((generation, 'finished', 'Library up to date' + note))
            except Cancelled:
                # Roll live partial results back to the last committed index.
                if index:
                    self.publish_catalog(group_tracks(index.load(root).values()), root, generation, index.artwork(root))
                self.post_library((generation, 'finished', 'Refresh canceled; saved library retained'))
            except Exception as error:
                if index:
                    self.publish_catalog(group_tracks(index.load(root).values()), root, generation, index.artwork(root))
                self.post_library((generation, 'finished', str(error) + ' · saved library retained'))
            finally:
                if index:
                    index.close()
        self.scan_thread = threading.Thread(target=work, name='library-scan', daemon=True)
        self.scan_thread.start()

    @Slot()
    def cancelScan(self):
        self.scan_cancel.set()

    def prepare_album(self, album, previous, saved_covers):
        stamp = fingerprint(album)
        old = previous.get(album['id'])
        saved = saved_covers.get(album['id'])
        cover = album.get('cover', '') or (old.get('cover', '') if old and old.get('fingerprint') == stamp else '')
        if not cover and saved and saved[0] == stamp:
            cover = saved[1] if saved[1] and Path(QUrl(saved[1]).toLocalFile()).is_file() else ''
        return dict(album, fingerprint=stamp, cover=cover,
            search=(album['title'] + ' ' + album['artist'] + ' ' + ' '.join(t['title'] for t in album['tracks'])).casefold())

    def publish_catalog(self, albums, root, generation, saved_covers=None):
        if generation != self.scan_generation or self.stopping:
            return
        with self.catalog_lock:
            previous = self.catalog
        catalog = {a['id']: self.prepare_album(a, previous, saved_covers or {}) for a in albums}
        with self.catalog_lock:
            if generation != self.scan_generation:
                return
            self.catalog = catalog
            self._albums = list(catalog.values())
        self.schedule_query()

    def publish_delta(self, albums, removed, generation, saved_covers):
        if generation != self.scan_generation or self.stopping:
            return
        with self.catalog_lock:
            previous = self.catalog
        changes = {a['id']: self.prepare_album(a, previous, saved_covers) for a in albums}
        catalog = dict(previous)
        catalog.update(changes)
        for identifier in removed:
            catalog.pop(identifier, None)
        with self.catalog_lock:
            if generation != self.scan_generation:
                return
            self.catalog = catalog
            self._albums = list(catalog.values())
        self.schedule_query()

    @Slot(object)
    def loaded(self, albums):
        # Also useful for fixture/demo libraries; production loads use the index worker.
        self.queries.submit(self.publish_catalog, albums, self._source, self.scan_generation)
        self._scanning = False
        self.libraryProgressChanged.emit()

    @Slot(str, str)
    def setFilter(self, text, artist):
        self.query_text, self.query_artist = text.casefold(), artist
        self.query_generation += 1  # Invalidate results immediately, before debounce.
        self.search_timer.start()

    @Slot(str)
    def toggleFolder(self, path):
        if path in self.collapsed_folders:
            self.collapsed_folders.remove(path)
        else:
            self.collapsed_folders.add(path)
        self.schedule_query()

    @Slot()
    def schedule_query(self):
        if self.stopping:
            return
        with self.catalog_lock:
            self.query_generation += 1
            generation = self.query_generation
        self.queries.submit(self.query_worker, generation)

    def query_worker(self, generation):
        if generation != self.query_generation or self.stopping:
            return
        with self.catalog_lock:
            albums = tuple(self.catalog.values())
        text, artist, root = self.query_text, self.query_artist, self._source
        collapsed = set(self.collapsed_folders)
        visible, folders, artists = [], {}, {'All artists'}
        for album in albums:
            if generation != self.query_generation or self.stopping:
                return
            artists.add(album['artist'])
            if (artist == 'All artists' or artist == album['artist']) and text in album['search']:
                visible.append({k: album[k] for k in ('id', 'title', 'artist', 'folder', 'cover', 'fingerprint')} |
                               {'track_count': len(album['tracks'])})
            path = Path(album['folder'])
            while str(path).startswith(root) and root:
                key = str(path)
                if (not text or text in key.casefold()) and not any(path != Path(p) and path.is_relative_to(p) for p in collapsed):
                    folders[key] = dict(path=key, name=path.name, depth=len(path.parts) - len(Path(root).parts), expanded=key not in collapsed)
                if key == root:
                    break
                path = path.parent
        visible.sort(key=lambda a: (a['artist'].casefold(), a['title'].casefold(), a['folder']))
        selected = next((a for a in albums if a['id'] == self._selected_album_id), None)
        tracks = []
        if selected:
            ordered_tracks = sorted(
                selected['tracks'],
                key=lambda track: (
                    track.get('disc_number') or 1,
                    track.get('track_number') or float('inf'),
                    track['path'],
                ),
            )
            for track in ordered_tracks:
                number = track.get('track_number') or track.get('number', '')
                if isinstance(number, str) and '/' in number:
                    number = number.split('/', 1)[0].strip()
                number = str(number).strip()
                display_title = f"{number} {track['title']}" if number else track['title']
                tracks.append(dict(track, display_title=display_title))
        self.renderReady.emit(dict(generation=generation, albums=visible, folders=[folders[k] for k in sorted(folders)],
            artists=[dict(name=name) for name in sorted(artists, key=lambda a: (a != 'All artists', a.casefold()))],
            tracks=tracks))

    @Slot(object)
    def accept_render(self, result):
        if result['generation'] != self.query_generation or self.stopping:
            return
        self.album_model.submit(result['albums'])
        self.folder_model.submit(result['folders'])
        self.artist_model.submit(result['artists'])
        self.track_model.submit(result['tracks'])
        self.libraryChanged.emit()

    @Slot(str)
    def openAlbum(self, identifier):
        self._selected_album_id = identifier
        self.selectedAlbumCoverChanged.emit()
        self.schedule_query()

    def selected_album_cover(self):
        with self.catalog_lock:
            album = self.catalog.get(self._selected_album_id)
            return album.get('cover', '') if album else ''

    @Slot(str)
    def requestCover(self, identifier):
        # No filesystem work or album scans on the GUI thread.
        with self.catalog_lock:
            album = self.catalog.get(identifier)
            if not album or album['cover']:
                return
            token = (self.scan_generation, identifier, album['fingerprint'], self.cache_generation)
            if token in self.cover_pending:
                return
            self.cover_pending.add(token)
        with self.cover_condition:
            self.cover_jobs.append((token, album, self._source))
            self.cover_condition.notify_all()

    def cover_worker(self):
        index = None
        while True:
            with self.cover_condition:
                self.cover_condition.wait_for(lambda: self.stopping or bool(self.cover_jobs))
                if self.stopping:
                    if index:
                        index.close()
                    return
                token, album, root = self.cover_jobs.popleft()
            try:
                with self.cache_lock:
                    if token[3] != self.cache_generation:
                        continue
                    url = self.cache.get([t['path'] for t in album['tracks']])
                if token[3] == self.cache_generation and not self.stopping:
                    if index is None:
                        index = LibraryIndex(self.index_path)
                    index.save_artwork(root, album['id'], album['fingerprint'], url)
                    self.coverReady.emit((token, url))
            except Exception:
                self.coverReady.emit((token, ''))

    @Slot(object)
    def cover_loaded(self, result):
        token, url = result
        generation, identifier, stamp, cache_generation = token
        if generation != self.scan_generation or cache_generation != self.cache_generation:
            return
        with self.catalog_lock:
            album = self.catalog.get(identifier)
            if not album or album['fingerprint'] != stamp:
                return
            self.catalog[identifier] = dict(album, cover=url)
        self.album_model.update(identifier, fingerprint=stamp, cover=url)
        if identifier == self._selected_album_id:
            self.selectedAlbumCoverChanged.emit()
        self.libraryChanged.emit()

    @Slot()
    def clearCache(self):
        def work():
            with self.cache_lock:
                self.cache_generation += 1
                self.cache.clear()
            with self.catalog_lock:
                self.catalog = {k: dict(a, cover='') for k, a in self.catalog.items()}
                self.cover_pending.clear()
            with self.cover_condition:
                self.cover_jobs.clear()
            self.cacheCleared.emit()
        self.commands.submit(work)

    @Slot()
    def cache_cleared(self):
        self.schedule_query()
        self.notify.emit('Thumbnail cache cleared; library index retained.')

    @Slot(bool, str, int, int)
    def saveArtwork(self, enabled, filename, width, height):
        try:
            value = ArtworkSettings(enabled, filename, width, height)
            value.validate()
            self._art = value
            self.settings.setValue('artwork', json.dumps(asdict(value)))
            self.changed.emit()
            self.notify.emit('Artwork settings saved.')
        except ValueError as error:
            self.notify.emit(str(error))

    def submit_command(self, function, *args):
        if not self.stopping:
            return self.commands.submit(function, *args)

    def prepare_selection(self, kind, identifiers, selected, art):
        try:
            with self.catalog_lock:
                albums = tuple(self.catalog.values())
            tracks = []
            selected = set(selected)
            identifiers = set(identifiers)
            for album in albums:
                if kind == 'albums' and album['id'] not in identifiers:
                    continue
                for track in album['tracks']:
                    if (not selected or track['path'] in selected) and (kind != 'folder' or Path(track['path']).is_relative_to(next(iter(identifiers)))):
                        tracks.append((album, track))
            self._enqueue(tracks, art)
        finally:
            self._preparing -= 1
            self.transfer_dirty.set()

    def select(self, kind, identifiers, selected=()):
        if self.stopping:
            return
        self._preparing += 1
        self.changed.emit()
        self.submit_command(self.prepare_selection, kind, identifiers, selected, self._art)

    @Slot(str, 'QVariantList')
    def send(self, identifier, selected):
        self.select('albums', [identifier], selected)

    @Slot('QVariantList')
    def sendAlbums(self, identifiers):
        self.select('albums', identifiers)

    @Slot(str)
    def sendFolder(self, folder):
        self.select('folder', [folder])

    @Slot(str, bool)
    def remove(self, identifier, album):
        self.submit_command(self._remove, identifier, album)

    @Slot()
    def cancelAll(self):
        self.submit_command(self._cancel_all)

    @Slot()
    def resume(self):
        self.submit_command(self._resume)

    @Slot(str, bool, bool)
    def resolveConflict(self, path, overwrite, apply_all):
        self.submit_command(self._resolve_conflict, path, overwrite, apply_all)

    @Slot(bool, bool)
    def answerConflict(self, overwrite, apply_all):
        self.submit_command(self._answer_conflict, overwrite, apply_all)

    @Slot(str, str, str)
    def resolveFormatConflict(self, path, decision, candidate):
        self.submit_command(self._resolve_format_conflict, path, decision, candidate)

    @Slot()
    def showConflicts(self):
        self.submit_command(self._show_conflicts)

    def queue_view(self):
        # Called in a worker only; snapshot cost never runs in a QML getter.
        with self.lock:
            groups = {}
            for job in self._queue:
                group = groups.setdefault(job['album_id'], dict(id=job['album_id'], title=job['album'], artist=job['artist'], tracks=[],
                    total=max(self._album_totals.get(job['album_id'], 1), 1), done=self._album_done.get(job['album_id'], 0), fraction=0.0))
                group['fraction'] += job['fraction']
                group['tracks'].append({k: job[k] for k in ('id', 'title', 'state', 'fraction')})
            for group in groups.values():
                group['fraction'] = (group['done'] + group['fraction']) / group['total']
            return list(groups.values())

    @Slot()
    def poll_workers(self):
        if self.stopping:
            return
        # GUI callback: take mailboxes only, then apply bounded model operations.
        with self.mailbox_lock:
            events = list(self.library_events)
            self.library_events.clear()
        latest = None
        for event in events:
            if event[0] == self.scan_generation:
                latest = event
        if latest:
            if latest[1] == 'progress':
                _, _, stage, done, total = latest
                self._library_stage, self._library_done, self._library_total = stage, done, total
                self._library_progress = done / max(total, 1) if total else 0
                self._library_status = f'{stage} · {done:,}' + (f' / {total:,} files ({self._library_progress:.0%})' if total else ' files found')
            else:
                self._scanning = False
                self._library_status = latest[2]
                self._library_stage = ''
            self.libraryProgressChanged.emit()
        if self.transfer_dirty.is_set() and not getattr(self, '_snapshot_pending', False) and getattr(self, '_transfer_payload', None) is None:
            self.transfer_dirty.clear()
            self._snapshot_pending = True
            self.commands.submit(self.make_queue_snapshot)
        payload = getattr(self, '_transfer_payload', None)
        if payload is not None:
            self._transfer_payload = None
            rows, results = payload
            self.queue_model.submit(rows)
            self._pending_history.extend(results)
            self.changed.emit()
        progress = self._progress_message
        self._progress_message = None
        if progress is not None:
            track_id, album_id, fraction, album_fraction = progress
            self.queue_model.update(track_id, fraction=fraction, state='Copying')
            self.queue_model.update('album:' + album_id, fraction=album_fraction)
            self.changed.emit()
        deadline = time.monotonic() + .003
        while self._pending_history and time.monotonic() < deadline:
            self.history_model.append(self._pending_history.popleft())

    @Slot(str)
    def toggleQueueAlbum(self, identifier):
        def toggle():
            if identifier in self.queue_collapsed:
                self.queue_collapsed.remove(identifier)
            else:
                self.queue_collapsed.add(identifier)
            self.transfer_dirty.set()
        self.submit_command(toggle)

    def make_queue_snapshot(self):
        groups = self.queue_view()
        rows = []
        for group in groups:
            expanded = group['id'] not in self.queue_collapsed
            rows.append(dict(id='album:' + group['id'], album_id=group['id'], kind='album', title=group['title'],
                subtitle=f"{group['artist']} · {group['done']} / {group['total']} tracks finished",
                fraction=group['fraction'], expanded=expanded, state=''))
            if expanded:
                rows.extend(dict(track, kind='track', album_id=group['id'], subtitle='', expanded=False) for track in group['tracks'])
        with self.lock:
            results = list(self._new_results)
            self._new_results.clear()
        self._transfer_payload = (rows, results)
        self._snapshot_pending = False

    @Slot()
    def shutdown(self):
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self.stopping = True
        self.scan_cancel.set()
        self.ui_timer.stop()
        self.search_timer.stop()
        def finish():
            self.commands.shutdown(wait=True)
            self._cancel_all()
            with self.condition:
                self.condition.notify_all()
            with self.cover_condition:
                self.cover_condition.notify_all()
            for worker in (self.scan_thread, self.transfer_thread, self.cover_thread):
                if worker:
                    worker.join()
            self.queries.shutdown(wait=True)
            self._shutdown_complete = True
            self.shutdownFinished.emit()
        threading.Thread(target=finish, name='shutdown', daemon=True).start()

    def _enqueue(self, tracks, artwork=None):
        if not self._source or not self._target:
            self.notify.emit('Choose both a source library and a target folder first.')
            return
        if overlaps(Path(self._source), Path(self._target)):
            self.notify.emit('Source and destination overlap.')
            return
        if not Path(self._target).is_dir():
            self.notify.emit('The destination is unavailable.')
            return
        with self.condition:
            if not self._running:
                self._album_totals.clear()
                self._album_done.clear()
            existing = {j['path'] for j in self._queue}
            for album, track in tracks:
                if track['path'] in existing:
                    continue
                existing.add(track['path'])
                self._album_totals[album['id']] = self._album_totals.get(album['id'], 0) + 1
                self._queue.append(dict(track, id=track['path'], album_id=album['id'], state='Pending', fraction=0.0,
                                        cancelled=False, art=artwork or self._art, root=self._source, target=self._target, device=Path(self._target).stat().st_dev))
            if self._queue and not self._running:
                self._running = True
                self.apply = None
                self.transfer_thread = threading.Thread(target=self.transfer_worker, daemon=True)
                self.transfer_thread.start()
            self.condition.notify_all()
        self.transfer_dirty.set()

    @Slot(str, bool)
    def _remove(self, identifier, album):
        with self.condition:
            for job in self._queue:
                if (job['album_id'] if album else job['id']) == identifier:
                    job['cancelled'] = True
            for job in self._queue:
                if job['cancelled'] and job['state'] != 'Copying':
                    self._album_totals[job['album_id']] -= 1
            self._queue = [j for j in self._queue if not j['cancelled'] or j['state'] == 'Copying']
            for c in self._conflicts:
                if (c['job']['album_id'] if album else c['job']['id']) == identifier:
                    c['job']['cancelled'] = True
            for context in self._artwork_folders.values():
                if (context['album_id'] if album else context['id']) == identifier:
                    context['cancelled'] = True
            if self._active_job and (self._active_job['album_id'] if album else self._active_job['id']) == identifier:
                self._active_job['cancelled'] = True
            self._conflicts = [c for c in self._conflicts if not c['job']['cancelled']]
            if self._conflicts:
                self._show_conflicts()
            self.condition.notify_all()
        self.transfer_dirty.set()

    @Slot()
    def _cancel_all(self):
        with self.condition:
            for job in self._queue:
                job['cancelled'] = True
            self._queue = [j for j in self._queue if j['state'] == 'Copying']
            for conflict in self._conflicts:
                conflict['job']['cancelled'] = True
            self._conflicts.clear()
            self._artwork_folders.clear()
            if self._active_job:
                self._active_job['cancelled'] = True
            self.condition.notify_all()
        self.transfer_dirty.set()

    @Slot()
    def _resume(self):
        with self.condition:
            self._paused = False
            self.condition.notify_all()
        self.transfer_dirty.set()

    def _show_conflicts(self):
        with self.lock:
            if self._conflicts:
                conflict = self._conflicts[0]
                if conflict.get('kind', 'same_path') == 'same_path':
                    self.conflict.emit(str(conflict['path']))
                else:
                    self.formatConflict.emit(conflict['kind'], str(conflict['path']), conflict.get('candidates', []))

    @Slot(str, bool, bool)
    def _resolve_conflict(self, path, overwrite, apply_all):
        with self.condition:
            if (not self._conflicts or self._conflicts[0].get('kind', 'same_path') != 'same_path'
                    or str(self._conflicts[0]['path']) != path):
                self._show_conflicts()
                return
            self._answer_conflict(overwrite, apply_all)

    def _resolve_format_conflict(self, path, decision, candidate):
        with self.condition:
            if (not self._conflicts or self._conflicts[0].get('kind') == 'same_path'
                    or str(self._conflicts[0]['path']) != path):
                self._show_conflicts()
                return
            conflict = self._conflicts[0]
            if conflict['kind'] == 'missing_number':
                valid = decision in ('same', 'different') and candidate in conflict['candidates']
            else:
                valid = decision in ('keep_existing', 'copy', 'replace')
            if not valid:
                self.notify.emit('Choose a valid action for this file conflict.')
                return
            self._conflicts.pop(0)
            job = conflict['job']
            if not job['cancelled']:
                if conflict['kind'] == 'missing_number':
                    job.setdefault('metadata_confirmations', {})[candidate] = decision == 'same'
                else:
                    job.setdefault('format_decisions', {})[path] = decision
                job['state'] = 'Pending'
            self.condition.notify_all()
            if self._conflicts:
                self._show_conflicts()
        self.transfer_dirty.set()

    @Slot(bool, bool)
    def _answer_conflict(self, overwrite, apply_all):
        with self.condition:
            if not self._conflicts or self._conflicts[0].get('kind', 'same_path') != 'same_path':
                self._show_conflicts()
                return
            if apply_all:
                self.apply = overwrite
            conflicts = ([c for c in self._conflicts if c.get('kind', 'same_path') == 'same_path']
                         if apply_all else self._conflicts[:1])
            for conflict in conflicts:
                self._conflicts.remove(conflict)
                job = conflict['job']
                if job['cancelled']:
                    continue
                job.setdefault('decisions', {})[str(conflict['path'])] = overwrite
                if conflict['art']:
                    self._artwork_folders[str(conflict['path'].parent)] = job
                else:
                    job['state'] = 'Pending'
            self.condition.notify_all()
            if self._conflicts:
                self._show_conflicts()
        self.transfer_dirty.set()

    def allowed(self, path, job, art=False, expected_size=None):
        if not path.exists():
            return True
        policy = self._conflict_policy
        if policy == 'always_skip':
            return False
        if policy == 'always_overwrite':
            return True
        if policy in ('overwrite_if_size_different', 'ask_if_size_different'):
            if expected_size is not None and path.stat().st_size == expected_size:
                return False
            if policy == 'overwrite_if_size_different':
                return True
        with self.condition:
            decision = job.get('decisions', {}).pop(str(path), None)
            if decision is not None:
                return decision
            if self.apply is not None:
                return self.apply
            if not art:
                job['state'] = 'Awaiting decision'
            existing = next((c for c in self._conflicts if c['art'] and art and c['path'] == path), None)
            if existing:
                existing['job'] = job
                raise ConflictPending()
            self._conflicts.append(dict(path=path, job=job, art=art, kind='same_path'))
            if len(self._conflicts) == 1:
                self.conflict.emit(str(path))
            self.transfer_dirty.set()
            raise ConflictPending()

    @staticmethod
    def normalized_metadata(track, key):
        return str(track.get(key, '')).strip().casefold()

    def matching_format_files(self, output, job):
        source = read_track(Path(job['path']), Path(job['root']))
        tagged = source.get('tagged', {})
        if not all(tagged.get(key) for key in ('album', 'artist', 'title')):
            return [], None
        matches, uncertain = [], None
        confirmations = job.get('metadata_confirmations', {})
        if not output.parent.is_dir():
            return matches, uncertain
        target_root = Path(job['target']).resolve()
        for path in output.parent.iterdir():
            if (not path.is_file() or path.is_symlink() or path == output
                    or path.suffix.lower() not in EXTENSIONS
                    or path.suffix.casefold() == output.suffix.casefold()):
                continue
            path.resolve().relative_to(target_root)
            existing = read_track(path, path.parent)
            existing_tagged = existing.get('tagged', {})
            if not all(existing_tagged.get(key) for key in ('album', 'artist', 'title')):
                continue
            if any(self.normalized_metadata(source, key) != self.normalized_metadata(existing, key)
                   for key in ('album', 'artist', 'title')):
                continue
            candidate = str(path)
            if tagged.get('number') and existing_tagged.get('number'):
                if self.normalized_metadata(source, 'number') == self.normalized_metadata(existing, 'number'):
                    matches.append(path)
            elif candidate in confirmations:
                if confirmations[candidate]:
                    matches.append(path)
            elif uncertain is None:
                uncertain = path
        return matches, uncertain

    def matching_same_format_files(self, output, job):
        source = read_track(Path(job['path']), Path(job['root']))
        tagged = source.get('tagged', {})
        keys = ('album', 'artist', 'title', 'number')
        if not all(tagged.get(key) for key in keys):
            return []
        if not output.parent.is_dir():
            return []
        target_root = Path(job['target']).resolve()
        matches = []
        for path in output.parent.iterdir():
            if (not path.is_file() or path.is_symlink()
                    or path.name.casefold() == output.name.casefold()
                    or path.suffix.casefold() != output.suffix.casefold()):
                continue
            path.resolve().relative_to(target_root)
            existing = read_track(path, path.parent)
            existing_tagged = existing.get('tagged', {})
            if not all(existing_tagged.get(key) for key in keys):
                continue
            if all(self.normalized_metadata(source, key) == self.normalized_metadata(existing, key)
                   for key in keys):
                matches.append(path)
        return matches

    def request_format_decision(self, kind, output, candidates, job):
        with self.condition:
            job['state'] = 'Awaiting decision'
            self._conflicts.append(dict(path=output, job=job, art=False, kind=kind,
                                        candidates=[str(path) for path in candidates]))
            if len(self._conflicts) == 1:
                self._show_conflicts()
            self.transfer_dirty.set()
        raise ConflictPending()

    def format_action(self, output, job):
        same_format_matches = self.matching_same_format_files(output, job)
        if same_format_matches:
            policy = self._same_format_policy
            if policy == 'always_copy':
                return 'copy', []
            if policy == 'keep_existing':
                return 'skip', []
            if policy == 'replace_existing':
                return 'copy', same_format_matches
            decision = job.get('format_decisions', {}).pop(str(output), None)
            if decision is None:
                self.request_format_decision('same_format', output, same_format_matches, job)
            if decision == 'keep_existing':
                return 'skip', []
            if decision == 'replace':
                return 'copy', same_format_matches
            return 'copy', []

        matches, uncertain = self.matching_format_files(output, job)
        if uncertain:
            self.request_format_decision('missing_number', output, [uncertain], job)
        if not matches:
            return 'copy', []
        policy = self._format_policy
        if policy == 'always_copy':
            return 'copy', []
        if policy == 'copy_and_delete':
            return 'copy', matches
        if policy == 'ask_every_time':
            decision = job.get('format_decisions', {}).pop(str(output), None)
            if decision is None:
                self.request_format_decision('format', output, matches, job)
            if decision == 'keep_existing':
                return 'skip', []
            if decision == 'replace':
                return 'copy', matches
            return 'copy', []

        bitrate_key = 'bitrate'
        source = read_track(Path(job['path']), Path(job['root']))
        existing = [(path, read_track(path, path.parent).get(bitrate_key)) for path in matches]
        if source.get(bitrate_key) is None or any(rate is None for _, rate in existing):
            decision = job.get('format_decisions', {}).pop(str(output), None)
            if decision is None:
                self.request_format_decision('bitrate', output, matches, job)
            if decision == 'keep_existing':
                return 'skip', []
            if decision == 'replace':
                return 'copy', matches
            return 'copy', []

        high = policy == 'keep_higher_bitrate'
        best_existing = (max if high else min)(rate for _, rate in existing)
        source_wins = source[bitrate_key] > best_existing if high else source[bitrate_key] < best_existing
        if source_wins:
            return 'copy', matches
        losers = [path for path, rate in existing if rate != best_existing]
        return 'skip', losers

    def remove_old_versions(self, paths, output, job):
        for path in paths:
            if path == output or not path.exists():
                continue
            self.validate_output(path, job)
            try:
                path.unlink()
            except OSError as error:
                return str(error)
        return ''

    def transfer_worker(self):
        artwork_folders = self._artwork_folders
        completed = 0
        while True:
            with self.condition:
                job = next((j for j in self._queue if j['state'] != 'Awaiting decision'), None)
                if job is None and not artwork_folders:
                    if self._conflicts:
                        self._status = 'Waiting for conflict decisions; other transfers are complete'
                        self.transfer_dirty.set()
                        self.condition.wait()
                        continue
                    self._running = False
                    self._paused = False
                    self._progress = 0
                    self._status = 'Transfers finished'
                    self.apply = None
                    self._active_job = None
                    self.transfer_dirty.set()
                    return
                if job:
                    job['state'] = 'Copying'
                    self._active_job = job
                    self.transfer_dirty.set()
            if job is None:
                # Queue remains active during artwork, so new selections join this run.
                with self.condition:
                    if not artwork_folders:
                        continue
                    folder, context = artwork_folders.popitem()
                    self._active_job = context
                try:
                    self.write_artwork(Path(folder), context)
                except (Cancelled, ConflictPending):
                    pass
                except OSError as error:
                    if error.errno in (errno.ENOSPC, errno.ENODEV, errno.EIO):
                        with self.condition:
                            self._paused = True
                            self._status = f'{error}. Restore the destination or free space, then Resume.'
                            self.transfer_dirty.set()
                            self.condition.wait_for(lambda: not self._paused or context['cancelled'])
                            if not context['cancelled']:
                                artwork_folders[folder] = context
                    else:
                        self.record(context, 'Artwork: ' + str(error))
                except Exception as error:
                    self.record(context, 'Artwork: ' + str(error))
                continue
            try:
                if job['cancelled']:
                    raise Cancelled()
                target = Path(job['target'])
                if not target.is_dir() or target.stat().st_dev != job['device']:
                    raise OSError(errno.ENODEV, 'Destination is unavailable')
                if overlaps(Path(job['root']), target):
                    raise ValueError('Source and target now overlap; transfer refused.')
                output = destination(Path(job['path']), Path(job['root']), target)
                if self.allowed(output, job, expected_size=Path(job['path']).stat().st_size):
                    action, old_versions = self.format_action(output, job)
                    if action == 'skip':
                        removal_error = self.remove_old_versions(old_versions, output, job)
                        message = 'Skipped'
                        if removal_error:
                            message += '; could not remove an older destination version: ' + removal_error
                        self.record(job, message)
                    else:
                        last_update = [0.0]
                        def progress(fraction):
                            job['fraction'] = fraction
                            self._progress = (completed + fraction) / max(completed + len(self._queue), 1)
                            self._status = 'Copying ' + job['title']
                            now = time.monotonic()
                            if now - last_update[0] >= .1 or fraction >= 1:
                                last_update[0] = now
                                total = max(self._album_totals.get(job['album_id'], 1), 1)
                                done = self._album_done.get(job['album_id'], 0)
                                self._progress_message = (job['id'], job['album_id'], fraction, (done + fraction) / total)
                        copy_audio(Path(job['path']), output, lambda: job['cancelled'], progress, lambda: self.validate_output(output, job))
                        removal_error = self.remove_old_versions(old_versions, output, job)
                        message = 'Copied'
                        if removal_error:
                            message += '; could not remove an older destination version: ' + removal_error
                        self.record(job, message)
                        artwork_folders[str(output.parent)] = job
                else:
                    self.record(job, 'Skipped')
            except ConflictPending:
                continue
            except Cancelled:
                self.record(job, 'Cancelled')
            except OSError as error:
                if (error.errno in (errno.ENOSPC, errno.ENODEV, errno.EIO) or (error.errno == errno.ENOENT and not Path(job['target']).is_dir())) and not job['cancelled']:
                    with self.condition:
                        self._paused = True
                        job['state'] = 'Paused'
                        self._status = f'{error}. Restore the destination or free space, then Resume.'
                        self.transfer_dirty.set()
                        self.condition.wait_for(lambda: not self._paused or job['cancelled'])
                    continue
                self.record(job, 'Failed: ' + str(error))
            except Exception as error:
                self.record(job, 'Failed: ' + str(error))
            with self.condition:
                if job in self._queue:
                    self._queue.remove(job)
                completed += 1
                self._album_done[job['album_id']] = self._album_done.get(job['album_id'], 0) + 1
            self.transfer_dirty.set()

    def write_artwork(self, folder, job):
        art = job['art']
        if not art.enabled:
            return
        if job['cancelled']:
            return
        target = Path(job['target'])
        if not target.is_dir() or target.stat().st_dev != job['device']:
            raise OSError(errno.ENODEV, 'Destination unavailable')
        if overlaps(Path(job['root']), target):
            raise ValueError('Source and target now overlap; artwork refused.')
        folder.resolve().relative_to(target.resolve())
        output = folder / art.filename
        output.resolve().relative_to(target.resolve())
        size_based = self._conflict_policy in ('overwrite_if_size_different', 'ask_if_size_different')
        if not size_based and not self.allowed(output, job, art=True):
            return
        paths = [p for p in Path(job['folder']).iterdir() if p.is_file() and not p.is_symlink()]
        image = cover_image(paths)
        if image is None:
            self.record(job, 'Artwork unavailable: ' + str(folder))
            return
        data = jpeg_bytes(image, art.width, art.height)
        if size_based and not self.allowed(output, job, art=True, expected_size=len(data)):
            return
        atomic_write(output, lambda stream: stream.write(data), lambda: job['cancelled'], lambda: self.validate_output(output, job))

    def validate_output(self, output, job):
        target = Path(job['target'])
        if not target.is_dir() or target.stat().st_dev != job['device']:
            raise OSError(errno.ENODEV, 'Destination unavailable or device changed')
        if overlaps(Path(job['root']), target):
            raise ValueError('Source and destination overlap; write refused.')
        output.resolve().relative_to(target.resolve())

    def record(self, job, message):
        with self.lock:
            result = dict(id=str(self._result_counter), title=job['title'], album=job['album'], message=message)
            self._result_counter += 1
            self._results.insert(0, result)
            self._new_results.append(result)
        self.transfer_dirty.set()



def main():
    os.environ.setdefault('QT_QUICK_CONTROLS_STYLE', 'Basic')
    app = QGuiApplication(sys.argv)
    app.setApplicationName('GrooveGrab')
    app.setQuitOnLastWindowClosed(False)
    backend = Backend()
    backend.shutdownFinished.connect(app.quit)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('backend', backend)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'qml' / 'Main.qml')))
    if not engine.rootObjects():
        backend.shutdown()
        return 1
    if '--smoke-test' in sys.argv:
        QTimer.singleShot(1000, backend.shutdown)
    result = app.exec()
    from shiboken6 import delete
    delete(engine)
    return result


if __name__ == '__main__':
    sys.exit(main())
