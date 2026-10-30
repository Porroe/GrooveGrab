"""Render a disposable sample library for UI review; does not touch user settings."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtCore import QUrl, QTimer, QObject, QMetaObject
from PIL import Image, ImageDraw
from music_transfer.app import Backend

app = QGuiApplication([])
temp = tempfile.TemporaryDirectory()
backend = Backend(index_path=Path(temp.name) / 'library.sqlite3')
app.setQuitOnLastWindowClosed(False)
backend.shutdownFinished.connect(app.quit)
backend._source = '/Music/Library'
backend._target = '/media/MUSIC'
backend._theme = sys.argv[1] if len(sys.argv) > 1 else 'dark'
albums = []
for i, (name, artist, color) in enumerate([
    ('Midnight Signals', 'The Satellites', '#49418d'), ('Coastline', 'Mira Lane', '#a46f55'),
    ('A Quiet Place', 'North & West', '#497e78'), ('After Hours', 'The Satellites', '#2e4d79'),
    ('Slow Motion', 'Iris Cole', '#855569'), ('Open Skies', 'Mira Lane', '#7c8c62'),
    ('Paper Planes', 'North & West', '#947eaa'), ('In Between', 'Iris Cole', '#527c9b')]):
    image = Image.new('RGB', (400, 400), color)
    draw = ImageDraw.Draw(image)
    for r in range(170, 0, -22):
        draw.ellipse((200-r, 200-r, 200+r, 200+r), outline='#ddd4cf', width=3)
    draw.text((25, 25), name.upper(), fill='white', font_size=23)
    draw.text((25, 352), artist, fill='white', font_size=18)
    path = Path(temp.name) / f'{i}.jpg'; image.save(path)
    tracks = [dict(path=f'/Music/Library/{artist}/{name}/{n:02}.flac', title=f'Track {n}', album=name, artist=artist, folder=f'/Music/Library/{artist}/{name}') for n in range(1, 6)]
    albums.append(dict(id=str(i), title=name, artist=artist, folder=tracks[0]['folder'], tracks=tracks, cover=path.as_uri()))
backend.loaded(albums)
backend._library_status = '8 albums · saved library loaded'
backend._source = ''
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty('backend', backend)
engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'music_transfer/qml/Main.qml')))
backend._source = '/Music/Library'
backend.changed.emit()
backend.schedule_query()
mode = sys.argv[2] if len(sys.argv) > 2 else 'library'
if mode == 'queue':
    for album in albums[:2]:
        backend._album_totals[album['id']] = 5
        for n, track in enumerate(album['tracks']):
            backend._queue.append(dict(track, id=track['path'], album_id=album['id'], state='Copying' if n == 0 and album['id'] == '0' else 'Pending', fraction=.4 if n == 0 else 0))
    backend._running = True
    backend._progress = .04
    backend._status = 'Copying Track 1'
    backend.transfer_dirty.set()
elif mode in ('settings', 'album', 'conflict'):
    window = engine.rootObjects()[0]
    if mode == 'album':
        window.setProperty('selectedAlbum', albums[0])
        backend.openAlbum(albums[0]['id'])
    QMetaObject.invokeMethod(window.findChild(QObject, mode + 'Dialog'), 'open')
def capture():
    output = '/tmp/music-transfer-' + backend._theme + '-' + mode + '.png'
    engine.rootObjects()[0].grabWindow().save(output)
    print(output)
    backend.shutdown()
QTimer.singleShot(1200, capture)
result = app.exec()
from shiboken6 import delete
delete(engine)
sys.exit(result)
