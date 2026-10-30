import unittest
from PySide6.QtCore import QPersistentModelIndex
from music_transfer.models import RecordModel
from test_app import APP, wait_for


class ModelTests(unittest.TestCase):
    def settled(self, model):
        wait_for(lambda: not model.timer.isActive() and model.pending is None)

    def test_reorder_and_insert_preserve_existing_rows(self):
        model = RecordModel()
        model.submit([dict(id='a'), dict(id='b'), dict(id='c')]); self.settled(model)
        index = QPersistentModelIndex(model.index(1))
        model.submit([dict(id='a'), dict(id='new'), dict(id='c'), dict(id='b')]); self.settled(model)
        self.assertTrue(index.isValid())
        self.assertEqual(index.row(), 3)
        self.assertEqual([r['id'] for r in model.rows], ['a', 'new', 'c', 'b'])
        self.assertEqual(model.positions, {'a': 0, 'new': 1, 'c': 2, 'b': 3})

    def test_latest_snapshot_wins_and_unchanged_rows_do_not_emit_changes(self):
        model = RecordModel()
        rows = [dict(id=str(i), value=i) for i in range(1000)]
        model.submit(rows)
        model.submit([dict(id='last', value=1)])
        self.settled(model)
        self.assertEqual(model.rows, [dict(id='last', value=1)])
        changes = []
        model.dataChanged.connect(lambda *args: changes.append(True))
        model.submit([dict(id='last', value=1)]); self.settled(model)
        self.assertEqual(changes, [])

    def test_delayed_thumbnail_is_not_overwritten_by_older_snapshot(self):
        model = RecordModel()
        model.submit([dict(id='album', fingerprint='old', cover='')]); self.settled(model)
        model.update('album', fingerprint='old', cover='file:///cached.jpg')
        model.submit([dict(id='album', fingerprint='old', cover='')]); self.settled(model)
        self.assertEqual(model.rows[0]['cover'], 'file:///cached.jpg')
        model.submit([dict(id='album', fingerprint='new', cover='')]); self.settled(model)
        self.assertEqual(model.rows[0]['cover'], '')
