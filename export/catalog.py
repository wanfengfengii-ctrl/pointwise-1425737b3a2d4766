import hashlib
import json
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
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS band_issuances (
                    frozen_number TEXT NOT NULL,
                    cut INTEGER NOT NULL,
                    protected TEXT NOT NULL,
                    manifest BLOB NOT NULL,
                    blocks_json TEXT NOT NULL,
                    PRIMARY KEY (frozen_number, cut, protected)
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

    def get_band_issuance(self, frozen_number: str, cut: int,
                          protected: list[str]) -> tuple[bytes, dict[str, dict]] | None:
        require_label(frozen_number, 'frozen_number')
        row = self.connection.execute('''
            SELECT manifest, blocks_json FROM band_issuances
            WHERE frozen_number = ? AND cut = ? AND protected = ?
        ''', (frozen_number, cut, ''.join(protected))).fetchone()
        if row is None:
            return None
        return row[0], _decode_blocks(row[0], json.loads(row[1]))

    def save_band_issuance(self, frozen_number: str, cut: int, protected: list[str],
                           manifest: bytes, blocks: dict[str, dict]) -> tuple[bytes, dict[str, dict]]:
        require_label(frozen_number, 'frozen_number')
        body = json.dumps({slot: blocks[slot]['payload'].hex() for slot in sorted(blocks)},
                          sort_keys=True, separators=(',', ':'))
        with write_transaction(self.connection):
            self.connection.execute('''
                INSERT INTO band_issuances VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(frozen_number, cut, protected) DO NOTHING
            ''', (frozen_number, cut, ''.join(protected), manifest, body))
            saved = self.get_band_issuance(frozen_number, cut, protected)
        if saved is None:
            raise RuntimeError('band issuance was not persisted')
        if saved[0] != manifest:
            raise ValueError('band issuance already exists with different content')
        return saved


def _decode_blocks(manifest: bytes, payloads: dict[str, str]) -> dict[str, dict]:
    issue_id = hashlib.sha256(manifest).hexdigest()
    return {slot: {'issue_id': issue_id, 'slot': slot, 'payload': bytes.fromhex(body)}
            for slot, body in payloads.items()}
