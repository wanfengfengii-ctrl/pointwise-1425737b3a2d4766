import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from database import write_transaction
from review.report import ReportSnapshot, decode_report_snapshot, encode_report_snapshot


class ReportCommitError(RuntimeError):
    """The report and its success receipt could not be committed."""


@dataclass(frozen=True)
class PublishedReport:
    report_id: int
    receipt_id: str


@dataclass(frozen=True)
class StoredReport:
    report_id: int
    receipt_id: str
    snapshot: ReportSnapshot


@dataclass(frozen=True)
class StoredBandReport:
    report_id: int
    receipt_id: str
    snapshot: dict


class ReportCatalog:
    """One numbering space for V1 column reports and band reports."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        with write_transaction(self.connection):
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS reports (
                    report_id INTEGER PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'v1'
                )
            ''')
            columns = {row[1] for row in self.connection.execute('PRAGMA table_info(reports)')}
            if 'kind' not in columns:
                self.connection.execute("ALTER TABLE reports ADD COLUMN kind TEXT NOT NULL DEFAULT 'v1'")
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS report_receipts (
                    report_id INTEGER PRIMARY KEY REFERENCES reports(report_id),
                    receipt_id TEXT NOT NULL UNIQUE
                )
            ''')

    def _commit(self, kind: str, body: str,
                before_commit: Callable[[], None] | None, prefix: str) -> PublishedReport:
        try:
            with write_transaction(self.connection):
                cursor = self.connection.execute(
                    'INSERT INTO reports (kind, snapshot_json) VALUES (?, ?)', (kind, body))
                report_id = cursor.lastrowid
                receipt_id = f'{prefix}{report_id}'
                self.connection.execute(
                    'INSERT INTO report_receipts VALUES (?, ?)', (report_id, receipt_id))
                if before_commit is not None:
                    before_commit()
        except Exception as error:
            raise ReportCommitError('report and receipt were not published') from error
        return PublishedReport(report_id, receipt_id)

    def commit(self, snapshot: ReportSnapshot, *, before_commit: Callable[[], None] | None = None) -> PublishedReport:
        return self._commit('v1', encode_report_snapshot(snapshot), before_commit, 'OK-')

    def commit_band(self, snapshot: dict, *,
                    before_commit: Callable[[], None] | None = None) -> PublishedReport:
        body = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
        return self._commit('band', body, before_commit, 'BK-')

    def _fetch(self, report_id: int, kind: str):
        if type(report_id) is not int or not 0 < report_id <= 2**63 - 1:
            raise KeyError(report_id)
        row = self.connection.execute('''
            SELECT reports.snapshot_json, report_receipts.receipt_id
            FROM reports JOIN report_receipts USING (report_id)
            WHERE reports.report_id = ? AND reports.kind = ?
        ''', (report_id, kind)).fetchone()
        if row is None:
            raise KeyError(report_id)
        return row

    def get(self, report_id: int) -> StoredReport:
        row = self._fetch(report_id, 'v1')
        return StoredReport(report_id, row[1], decode_report_snapshot(row[0]))

    def get_band(self, report_id: int) -> StoredBandReport:
        row = self._fetch(report_id, 'band')
        return StoredBandReport(report_id, row[1], json.loads(row[0]))

    def _count(self, kind: str) -> tuple[int, int]:
        return tuple(self.connection.execute('''
            SELECT (SELECT COUNT(*) FROM reports WHERE kind = ?),
                   (SELECT COUNT(*) FROM report_receipts
                    JOIN reports USING (report_id) WHERE kind = ?)
        ''', (kind, kind)).fetchone())

    def counts(self) -> tuple[int, int]:
        return self._count('v1')

    def band_counts(self) -> tuple[int, int]:
        return self._count('band')


def read_report(reports: ReportCatalog, report_id: int) -> StoredReport:
    return reports.get(report_id)


def read_band_report(reports: ReportCatalog, report_id: int) -> dict:
    stored = reports.get_band(report_id)
    return {'report_id': stored.report_id, 'receipt_id': stored.receipt_id,
            'snapshot': stored.snapshot}
