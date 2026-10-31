# Development

This document is for contributors working on GrooveGrab's implementation. The [README](../README.md) is the user guide.

## Set up a development environment

GrooveGrab requires Python 3.11 or newer, PySide6, Mutagen, Pillow, and a graphical Linux session. On Debian or Ubuntu, install `python3-venv` and the Qt runtime libraries listed in the README before creating the environment.

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m music_transfer.app
```

The editable install exposes the `groovegrab` command as well. Run the GUI from a graphical session; use the offscreen smoke test for startup checks in headless environments.

## Code map

| Path | Responsibility |
| --- | --- |
| `music_transfer/app.py` | Qt application entry point and `Backend`. Connects QML to library, settings, selection, queue, and transfer operations. |
| `music_transfer/qml/Main.qml` | Main window, dialogs, settings categories, theme-aware controls, and user interactions. |
| `music_transfer/core.py` | Music metadata parsing, supported audio extensions, path validation, thumbnail/artwork processing, and safe audio writes. |
| `music_transfer/database.py` | SQLite index lifecycle, cached metadata loading, incremental scans, and persisted artwork references. |
| `music_transfer/models.py` | Qt list models. Applies bounded row updates so large libraries do not require a single full model reset. |
| `tests/` | Core, database, app/backend, responsiveness, and QML UI regression tests. |

`music_transfer/__main__.py` delegates module execution to `music_transfer.app:main`. The installed `groovegrab` entry point uses the same function.

## Runtime and data flow

1. The QML window binds to a `Backend` instance. Settings are persisted using Qt `QSettings`.
2. The selected source is loaded from the SQLite index first. When startup refresh is enabled (the default), a background scan then discovers additions, edits, and deletions. When disabled, the saved index is loaded without walking the source; an explicit refresh or choosing a source starts a scan.
3. `LibraryIndex.refresh()` reuses metadata for files whose size and nanosecond modification time are unchanged. Metadata reads and database access happen off the UI thread. Worker results are sent back to the Qt thread for publication.
4. Albums and tracks are published through `RecordModel`. Queries and large model updates are processed incrementally to keep the interface responsive.
5. Queue preparation and file copying run in workers. QML receives progress and conflict requests through Qt signals. Audio is written safely to the destination; the source remains read-only.

The SQLite database defaults to `$XDG_DATA_HOME/music-transfer/library.sqlite3` (or `~/.local/share/music-transfer/library.sqlite3`). Thumbnail data defaults to `$XDG_CACHE_HOME/music-transfer/covers` (or `~/.cache/music-transfer/covers`). These locations are outside the source library.

## Invariants when changing behavior

- Never write, rename, delete, or cache files inside the source library.
- Keep saved index entries when a source is unavailable, unreadable, or a refresh is canceled. Only commit removals after a successful scan.
- Loading the saved index must remain distinct from refreshing the filesystem. Do not trigger a scan when startup refresh is disabled unless the user chooses or refreshes a source.
- Keep filesystem, database, artwork, and transfer work off the Qt GUI thread. Preserve responsiveness for large libraries and during shutdown.
- Validate destination paths and revalidate them before writes. Do not allow source/destination overlap or traversal outside the selected destination.
- Keep the Linux-compatible path and Qt behavior; avoid platform-specific assumptions in shared code.
- Treat settings edits as drafts in the UI when appropriate. Keep save/cancel and notification behavior consistent across settings categories.

## Tests and checks

Run the complete test suite:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Run the application startup smoke test without opening a visible window:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m music_transfer.app --smoke-test
```

The tests use temporary source and destination trees. Prefer focused tests while iterating, then run the full suite for changes that touch shared behavior. UI tests need Qt's offscreen platform; the app and UI must still be exercised in a graphical Linux session for platform-specific integration checks.

When adding UI behavior, cover both the exposed backend behavior and the QML interaction where practical. For library changes, include regression coverage for cached startup, refresh cancellation, and retention of saved entries on failure. For transfer changes, verify source immutability, destination containment, cancellation, and cleanup.

## Packaging

Project metadata and dependencies are in `pyproject.toml`. QML files are included as package data, and the desktop launcher is installed from `packaging/groovegrab.desktop`. Keep runtime dependencies there and verify that an editable install and the `groovegrab` entry point still work after packaging changes.
