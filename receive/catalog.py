import sqlite3
from dataclasses import dataclass

from database import write_transaction
from export.manifest import ValidatedBlock
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


@dataclass(frozen=True)
class BandReceipt:
    receipt_id: str
    issue_id: str
    slot: str
    payload: bytes


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
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS intake_bands (
                    receipt_id TEXT PRIMARY KEY NOT NULL,
                    artifact_id TEXT NOT NULL UNIQUE,
                    issue_id TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    UNIQUE (issue_id, slot)
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

    def accept_band(self, block: ValidatedBlock) -> BandReceipt:
        artifact_id = f'{block.issue_id}/{block.slot}'
        candidate = BandReceipt('IN:' + artifact_id, block.issue_id, block.slot, block.payload)
        with write_transaction(self.connection):
            row = self.connection.execute('''
                SELECT receipt_id, issue_id, slot, payload
                FROM intake_bands WHERE artifact_id = ?
            ''', (artifact_id,)).fetchone()
            if row is None:
                self.connection.execute('''
                    INSERT INTO intake_bands VALUES (?, ?, ?, ?, ?)
                ''', (candidate.receipt_id, artifact_id, candidate.issue_id, candidate.slot,
                      candidate.payload))
                return candidate
            accepted = BandReceipt(*row)
            if accepted.payload == candidate.payload:
                return accepted
            raise DeliveryConflict(f'conflicting delivery for band block {artifact_id}')

    def get(self, receipt_id: str) -> Receipt:
        require_label(receipt_id, 'receipt_id')
        row = self.connection.execute('''
            SELECT receipt_id, artifact_id, frozen_number, column_name, wire
            FROM intake_columns WHERE receipt_id = ?
        ''', (receipt_id,)).fetchone()
        if row is None:
            raise KeyError(receipt_id)
        return Receipt(*row)

    def get_band(self, receipt_id: str) -> BandReceipt:
        require_label(receipt_id, 'receipt_id')
        row = self.connection.execute('''
            SELECT receipt_id, issue_id, slot, payload
            FROM intake_bands WHERE receipt_id = ?
        ''', (receipt_id,)).fetchone()
        if row is None:
            raise KeyError(receipt_id)
        return BandReceipt(*row)

    def list_bands_for_issue(self, issue_id: str) -> tuple[BandReceipt, ...]:
        require_label(issue_id, 'issue_id')
        rows = self.connection.execute('''
            SELECT receipt_id, issue_id, slot, payload
            FROM intake_bands WHERE issue_id = ? ORDER BY slot
        ''', (issue_id,))
        return tuple(BandReceipt(*row) for row in rows)

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
