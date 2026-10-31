# GrooveGrab

GrooveGrab is a Linux desktop music browser and transfer utility. Browse album covers or folders, copy original audio to a music player, and generate resized JPEG covers from embedded artwork. The source library is read-only.

## Install and run

Requires Python 3.11+ and a graphical Linux session. On Debian/Ubuntu, install `python3-venv` first. Qt also needs the usual desktop libraries (`libegl1`, `libopengl0`, `libxcb-cursor0` on Debian/Ubuntu).

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/groovegrab
```

For a permanent user installation, `pipx install .` exposes `groovegrab` on your PATH. Copy `packaging/groovegrab.desktop` into `~/.local/share/applications/` after installation to add a launcher. The desktop launcher requires `groovegrab` on the graphical session's PATH.

## Use

1. Choose a source library and a separate destination folder on an already-mounted drive. Saved albums appear first when reopening a known library, followed by an automatic background refresh by default. **Refresh library** manually checks for changes at any time and is disabled during an update. In Settings → Library, turn off **Refresh library on startup** to load only the saved index until you refresh manually. Loading or refreshing shows file counts and metadata progress; **Cancel refresh** keeps the saved index.
2. Browse album cards, search songs/artists/albums, or switch to the folder hierarchy. Use the clear control in the search field to reset the query.
3. Open an album to see track numbers. Album cards show singular or plural track counts correctly. Send all tracks or a selected subset. **View current selection** shows selected albums and tracks with their artwork, and lets you remove individual items or clear the selection before sending. Folder transfers include nested audio files only.
4. Transfers begin immediately. Expand queue albums to inspect tracks; remove pending tracks/albums, cancel the active track, or clear the queue. Completed files remain.
5. In Settings → Files, choose what to do when a destination filename already exists: Always Skip, Always Overwrite, Overwrite if size is different, Ask if size is different, or Ask every time. Size-based policies skip an existing file when its size matches; prompts allow other queued files to continue. Apply to all lasts until that queue run finishes and covers audio and artwork alike.
6. Also in Settings → Files, choose what to do when the destination album folder contains the same track in a different extension. The app matches tagged album, artist, and title without regard to case, and compares track numbers when both are available. If a number is missing, it asks whether the files are the same track. Choose Always Copy, Copy and delete existing, Keep higher bitrate, Keep lower bitrate, or Ask every time. Bitrate policies prompt if a bitrate is unavailable.
7. Settings → Files also controls same-format duplicates with a different filename. The default, Ask every time, matches only when tagged album, artist, title, and track number all match without regard to case. If any tag is missing, the files are not treated as duplicates. Choose whether to keep the existing file, copy both, or replace the old destination file. Replacements are copied successfully before older destination files are removed; source files are never deleted.
8. Settings has Save settings and Cancel actions available in every category. Changes are drafts until saved; Cancel discards them, and a successful save shows a brief toast. Settings → Appearance offers System, Light, and Dark themes. Settings → Library controls startup refresh and clears the thumbnail cache immediately. Settings → Artwork controls JPEG filename and maximum dimensions. Smaller covers are never enlarged. Artwork settings are captured when tracks are queued.

Artwork is generated after queued audio finishes. Missing artwork is reported in history. If a drive disappears or fills, restore/free the drive and choose Resume, or cancel. Do not unplug a drive during a transfer. This app does not mount, format, eject, or transcode music, and never modifies the source library.

Settings use Qt's user configuration location. For compatibility with existing installs, cover thumbnails remain under `$XDG_CACHE_HOME/music-transfer/covers` (default `~/.cache/music-transfer/covers`), and the SQLite library index remains at `$XDG_DATA_HOME/music-transfer/library.sqlite3` (default `~/.local/share/music-transfer/library.sqlite3`). Settings can clear thumbnails. The index stores metadata and artwork references for each library path. Unchanged tracks reuse their metadata, based on size and nanosecond modification time. Rescan checks for additions, edits, and deletions. Unavailable sources and unreadable directories retain their saved entries. Clearing thumbnails does not clear the index. An unusable database is preserved with an `.unusable-…` suffix before rebuilding. Nothing is cached or written in the source library. Audio without readable tags is still copied and browsable using folder/filename fallbacks. Raw AAC and WAV files often lack embedded artwork.

All library/database work, artwork processing, file copying, and queue preparation run in workers. The interface uses virtualized Qt item models with bounded updates. Closing cancels work asynchronously and waits for temporary-file cleanup without blocking the window.

## Development checks

Contributor setup, architecture, and development guidance are in the [developer documentation](docs/development.md).

```sh
.venv/bin/python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen .venv/bin/python -m music_transfer.app --smoke-test
```

The automated suite includes a 5,000-album/25,000-track model test, cached-open and incremental-refresh checks, database recovery, cancellation, and GUI heartbeat tests during slowed scanning/copying (maximum permitted gap: 250 ms in the controlled test environment). It also uses temporary source/target directories to verify path containment, source immutability, safe replacement/cancellation, queue operations, metadata fallbacks, and artwork sizing/caching. A physical removable drive should also be tested before relying on the app for a large transfer.
