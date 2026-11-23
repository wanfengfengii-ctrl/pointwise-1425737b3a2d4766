from collections.abc import Iterator
from contextlib import contextmanager
from sqlite3 import Connection


@contextmanager
def write_transaction(connection: Connection) -> Iterator[None]:
    """Own the SQLite write boundary from the first lookup to commit."""
    if connection.in_transaction:
        raise RuntimeError('catalog write requires a connection outside a transaction')
    connection.execute('PRAGMA foreign_keys = ON')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        yield
