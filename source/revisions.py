import json
import sqlite3
from dataclasses import dataclass

from database import write_transaction
from source.rounds import Round, require_label, rounds_as_records
from source.validation import validate_rounds


@dataclass(frozen=True)
class SourceRevision:
    volume_id: str
    revision_id: str
    rounds: tuple[Round, ...]


class RevisionCatalog:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        with write_transaction(self.connection):
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS source_revisions (
                    volume_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    rounds_json TEXT NOT NULL,
                    PRIMARY KEY (volume_id, revision_id)
                )
            ''')

    def save(self, revision: SourceRevision) -> str:
        key = (revision.volume_id, revision.revision_id)
        body = json.dumps(rounds_as_records(revision.rounds), separators=(',', ':'))
        with write_transaction(self.connection):
            self.connection.execute('''
                INSERT INTO source_revisions VALUES (?, ?, ?)
                ON CONFLICT(volume_id, revision_id) DO NOTHING
            ''', (*key, body))
            saved = self._fetch(*key)
            if saved[0] != body:
                raise ValueError(f'revision already exists with different content: {key!r}')
        return revision.revision_id

    def _fetch(self, volume_id: str, revision_id: str) -> tuple | None:
        return self.connection.execute('''
            SELECT rounds_json FROM source_revisions
            WHERE volume_id = ? AND revision_id = ?
        ''', (volume_id, revision_id)).fetchone()

    def get(self, volume_id: str, revision_id: str) -> SourceRevision:
        require_label(volume_id, 'volume_id')
        require_label(revision_id, 'revision_id')
        row = self._fetch(volume_id, revision_id)
        if row is None:
            raise KeyError((volume_id, revision_id))
        return SourceRevision(volume_id, revision_id, validate_rounds(json.loads(row[0])))
