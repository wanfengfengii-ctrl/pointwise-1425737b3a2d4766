import sqlite3
from dataclasses import dataclass

from database import write_transaction
from receive.validation import ValidatedColumn
from source.rounds import require_label


class DeliveryConflict(ValueError):
    """A valid delivery tried to replace the evidence for an accepted artifact."""


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    artifact_id: str
    frozen_number: str
    column: str
    wire: bytes


class IntakeCatalog:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        with write_transaction(self.connection):
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS intake_columns (
                    receipt_id TEXT PRIMARY KEY NOT NULL,
                    artifact_id TEXT NOT NULL UNIQUE,
                    frozen_number TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    wire BLOB NOT NULL,
                    UNIQUE (frozen_number, column_name)
                )
            ''')
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS delivery_conflicts (
                    attempt_id INTEGER PRIMARY KEY,
                    artifact_id TEXT NOT NULL REFERENCES intake_columns(artifact_id),
                    attempted_wire BLOB NOT NULL
                )
            ''')

    def accept(self, column: ValidatedColumn) -> Receipt:
        envelope = column.envelope
        candidate = Receipt('IN:' + envelope.artifact_id, envelope.artifact_id,
                            envelope.frozen_number, envelope.column, column.wire)
        with write_transaction(self.connection):
            row = self.connection.execute('''
                SELECT receipt_id, artifact_id, frozen_number, column_name, wire
                FROM intake_columns WHERE artifact_id = ?
            ''', (candidate.artifact_id,)).fetchone()
            if row is None:
                self.connection.execute('''
                    INSERT INTO intake_columns VALUES (?, ?, ?, ?, ?)
                ''', (candidate.receipt_id, candidate.artifact_id, candidate.frozen_number,
                      candidate.column, candidate.wire))
                return candidate
            accepted = Receipt(*row)
            if accepted.wire == candidate.wire:
                return accepted
            self.connection.execute('''
                INSERT INTO delivery_conflicts (artifact_id, attempted_wire) VALUES (?, ?)
            ''', (candidate.artifact_id, candidate.wire))
        raise DeliveryConflict(f'conflicting delivery for {candidate.artifact_id}')

    def get(self, receipt_id: str) -> Receipt:
        require_label(receipt_id, 'receipt_id')
        row = self.connection.execute('''
            SELECT receipt_id, artifact_id, frozen_number, column_name, wire
            FROM intake_columns WHERE receipt_id = ?
        ''', (receipt_id,)).fetchone()
        if row is None:
            raise KeyError(receipt_id)
        return Receipt(*row)

    def list_for_volume(self, frozen_number: str) -> tuple[Receipt, ...]:
        require_label(frozen_number, 'frozen_number')
        rows = self.connection.execute('''
            SELECT receipt_id, artifact_id, frozen_number, column_name, wire
            FROM intake_columns WHERE frozen_number = ? ORDER BY column_name
        ''', (frozen_number,))
        return tuple(Receipt(*row) for row in rows)

    def count_conflicts(self, artifact_id: str) -> int:
        return self.connection.execute('''
            SELECT COUNT(*) FROM delivery_conflicts WHERE artifact_id = ?
        ''', (artifact_id,)).fetchone()[0]
