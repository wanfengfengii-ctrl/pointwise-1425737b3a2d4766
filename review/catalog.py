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


class ReportCatalog:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        with write_transaction(self.connection):
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS reports (
                    report_id INTEGER PRIMARY KEY,
                    snapshot_json TEXT NOT NULL
                )
            ''')
            self.connection.execute('''
                CREATE TABLE IF NOT EXISTS report_receipts (
                    report_id INTEGER PRIMARY KEY REFERENCES reports(report_id),
                    receipt_id TEXT NOT NULL UNIQUE
                )
            ''')

    def commit(self, snapshot: ReportSnapshot, *, before_commit: Callable[[], None] | None = None) -> PublishedReport:
        body = encode_report_snapshot(snapshot)
        try:
            with write_transaction(self.connection):
                cursor = self.connection.execute('INSERT INTO reports (snapshot_json) VALUES (?)', (body,))
                report_id = cursor.lastrowid
                receipt_id = f'OK-{report_id}'
                self.connection.execute('INSERT INTO report_receipts VALUES (?, ?)', (report_id, receipt_id))
                if before_commit is not None:
                    before_commit()
        except Exception as error:
            raise ReportCommitError('report and receipt were not published') from error
        return PublishedReport(report_id, receipt_id)

    def get(self, report_id: int) -> StoredReport:
        if type(report_id) is not int or not 0 < report_id <= 2**63 - 1:
            raise KeyError(report_id)
        row = self.connection.execute('''
            SELECT reports.snapshot_json, report_receipts.receipt_id
            FROM reports JOIN report_receipts USING (report_id)
            WHERE reports.report_id = ?
        ''', (report_id,)).fetchone()
        if row is None:
            raise KeyError(report_id)
        return StoredReport(report_id, row[1], decode_report_snapshot(row[0]))

    def counts(self) -> tuple[int, int]:
        return tuple(self.connection.execute('''
            SELECT (SELECT COUNT(*) FROM reports), (SELECT COUNT(*) FROM report_receipts)
        ''').fetchone())


def read_report(reports: ReportCatalog, report_id: int) -> StoredReport:
    return reports.get(report_id)
