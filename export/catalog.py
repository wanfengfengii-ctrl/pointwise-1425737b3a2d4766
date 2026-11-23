import sqlite3

from database import write_transaction
from export.envelope import decode_envelope, encode_envelope
from export.identity import validate_column
from source.rounds import require_label


class ArtifactCatalog:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        with write_transaction(self.connection):
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS issued_columns (
                    frozen_number TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    artifact_id TEXT NOT NULL UNIQUE,
                    wire BLOB NOT NULL,
                    PRIMARY KEY (frozen_number, column_name)
                )
            ''')

    def get(self, frozen_number: str, column: str) -> bytes | None:
        require_label(frozen_number, 'frozen_number')
        validate_column(column)
        row = self.connection.execute('''
            SELECT wire FROM issued_columns WHERE frozen_number = ? AND column_name = ?
        ''', (frozen_number, column)).fetchone()
        return None if row is None else row[0]

    def save_or_get(self, wire: bytes) -> bytes:
        envelope = decode_envelope(wire)
        canonical = encode_envelope(envelope)
        with write_transaction(self.connection):
            self.connection.execute('''
                INSERT INTO issued_columns VALUES (?, ?, ?, ?)
                ON CONFLICT(frozen_number, column_name) DO NOTHING
            ''', (envelope.frozen_number, envelope.column, envelope.artifact_id, canonical))
            saved = self.get(envelope.frozen_number, envelope.column)
            if saved is None:
                raise RuntimeError('issued column was not persisted')
        return saved
