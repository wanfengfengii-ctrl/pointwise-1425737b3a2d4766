import sqlite3

from database import write_transaction


class FrozenStorage:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        with write_transaction(self.connection):
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS frozen_volumes (
                    frozen_number TEXT PRIMARY KEY NOT NULL,
                    volume_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    UNIQUE (volume_id, revision_id)
                )
            ''')

    def insert_or_get(self, number: str, volume_id: str, revision_id: str, content: str) -> str:
        with write_transaction(self.connection):
            self.connection.execute('''
                INSERT INTO frozen_volumes VALUES (?, ?, ?, ?)
                ON CONFLICT(volume_id, revision_id) DO NOTHING
            ''', (number, volume_id, revision_id, content))
            saved_number, saved_content = self.connection.execute('''
                SELECT frozen_number, content_json FROM frozen_volumes
                WHERE volume_id = ? AND revision_id = ?
            ''', (volume_id, revision_id)).fetchone()
            if saved_content != content:
                raise ValueError('frozen source already bound to different content')
        return saved_number

    def fetch_by_number(self, number: str) -> tuple[str, str, str] | None:
        return self.connection.execute('''
            SELECT volume_id, revision_id, content_json FROM frozen_volumes
            WHERE frozen_number = ?
        ''', (number,)).fetchone()
