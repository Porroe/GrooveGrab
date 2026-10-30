"""Stable Qt models: bounded row edits instead of whole-list QML snapshots."""
from collections import deque
import time
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal, Property, QTimer, Slot


class RecordModel(QAbstractListModel):
    countChanged = Signal()
    settled = Signal()
    ROLE = int(Qt.UserRole) + 1

    def __init__(self, parent=None, key='id'):
        super().__init__(parent)
        self.key = key
        self.rows = []
        self.positions = {}
        self.operations = deque()
        self.pending = None
        self.overrides = {}
        self.dirty_positions = False
        self.timer = QTimer(self)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self.drain)

    count = Property(int, lambda s: len(s.rows), notify=countChanged)

    def roleNames(self):
        return {
            self.ROLE: b'modelData',
            int(Qt.DisplayRole): b'display',
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and 0 <= index.row() < len(self.rows):
            if role == self.ROLE:
                return self.rows[index.row()]
            if role == Qt.DisplayRole:
                return self.rows[index.row()].get('name', self.rows[index.row()].get('title', ''))
        return None

    def update(self, identifier, **values):
        if 'fingerprint' in values:
            self.overrides[identifier] = (values['fingerprint'], values)
        if self.dirty_positions:
            self.reindex()
        position = self.positions.get(identifier)
        if position is None:
            return
        row = self.rows[position]
        if all(row.get(key) == value for key, value in values.items()):
            return
        self.rows[position] = dict(row, **values)
        index = self.index(position)
        self.dataChanged.emit(index, index, [self.ROLE, int(Qt.DisplayRole)])

    def submit(self, rows):
        if self.operations:
            self.pending = rows
            return
        self._desired = []
        self._seen = set()
        self.operations.append(('index', iter(rows)))
        self.timer.start()

    @Slot()
    def drain(self):
        deadline = time.monotonic() + .003
        steps = 0
        while self.operations and steps < 100 and time.monotonic() < deadline:
            steps += 1
            kind, payload = self.operations[0]
            if kind == 'index':
                try:
                    row = next(payload)
                except StopIteration:
                    self.operations.popleft()
                    self.operations.append(('remove', len(self.rows) - 1))
                    continue
                self._desired.append(row)
                self._seen.add(row[self.key])
            elif kind == 'remove':
                self.operations.popleft()
                position = payload
                if position < 0:
                    self.reindex()
                    self.operations.append(('upsert', 0))
                    continue
                if self.rows[position][self.key] not in self._seen:
                    self.beginRemoveRows(QModelIndex(), position, position)
                    removed = self.rows.pop(position)
                    self.positions.pop(removed[self.key], None)
                    self.overrides.pop(removed[self.key], None)
                    self.endRemoveRows()
                    self.dirty_positions = True
                    self.countChanged.emit()
                self.operations.appendleft(('remove', position - 1))
            else:
                self.operations.popleft()
                target = payload
                if target >= len(self._desired):
                    continue
                row = self._desired[target]
                identifier = row[self.key]
                override = self.overrides.get(identifier)
                if override and override[0] == row.get('fingerprint'):
                    row = dict(row, **override[1])
                elif override:
                    self.overrides.pop(identifier, None)
                if self.dirty_positions:
                    self.reindex()
                position = self.positions.get(identifier)
                if position is None:
                    self.beginInsertRows(QModelIndex(), target, target)
                    self.rows.insert(target, row)
                    self.endInsertRows()
                    self.reindex_range(target, len(self.rows))
                    self.countChanged.emit()
                else:
                    if position != target:
                        self.beginMoveRows(QModelIndex(), position, position, QModelIndex(), target if target < position else target + 1)
                        self.rows.insert(target, self.rows.pop(position))
                        self.endMoveRows()
                        self.reindex_range(min(target, position), max(target, position) + 1)
                    if self.rows[target] != row:
                        self.rows[target] = row
                        index = self.index(target)
                        self.dataChanged.emit(index, index, [self.ROLE, int(Qt.DisplayRole)])
                self.operations.appendleft(('upsert', target + 1))
        if not self.operations:
            self.timer.stop()
            self.settled.emit()
            if self.pending is not None:
                pending, self.pending = self.pending, None
                self.submit(pending)

    def reindex_range(self, start, end):
        for position in range(start, end):
            self.positions[self.rows[position][self.key]] = position

    def reindex(self):
        self.positions = {row[self.key]: i for i, row in enumerate(self.rows)}
        self.dirty_positions = False

    def append(self, row):
        if self.dirty_positions:
            self.reindex()
        identifier = row[self.key]
        if identifier in self.positions:
            self.update(identifier, **row)
            return
        position = len(self.rows)
        self.beginInsertRows(QModelIndex(), position, position)
        self.rows.append(row)
        self.positions[identifier] = position
        self.endInsertRows()
        self.countChanged.emit()
