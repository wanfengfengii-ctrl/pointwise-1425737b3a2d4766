import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from receive.intake import receive_column
from review.catalog import read_report
from review.publication import review_volume
from review.totals import ReviewTotals
from source.rounds import ProbeOccurrence, Round
from tests import issue_columns, load_sample, night_shift, source_day, sparse_wire


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def receive_archive(self, night, *, status_wire=None):
        archived_reading, archived_status = load_sample('legacy-v1.json')
        reading = receive_column(night.intake, json.dumps(archived_reading).encode())
        status = receive_column(night.intake, status_wire if status_wire is not None else json.dumps(archived_status).encode())
        issued = review_volume(night.intake, night.reports, 'ARCHIVE-004', status.receipt_id, reading.receipt_id)
        saved = read_report(night.reports, issued.report_id)
        self.assertEqual((saved.report_id, saved.receipt_id), (issued.report_id, issued.receipt_id))
        self.assertEqual(saved.snapshot.frozen_number, 'ARCHIVE-004')
        self.assertEqual(saved.snapshot.reading_receipt_id, reading.receipt_id)
        self.assertEqual(saved.snapshot.status_receipt_id, status.receipt_id)
        return saved

    def assert_archive(self, saved):
        self.assertEqual(saved.snapshot.rounds, (
            Round(True, (ProbeOccurrence('D', 8, '复检'), ProbeOccurrence('D', None, '合格'))),
        ))
        self.assertEqual(saved.snapshot.totals, ReviewTotals(1, 2, ((1, 1),)))

    def test_legacy_layout(self):
        with night_shift(self.root / 'night.sqlite') as night:
            self.assert_archive(self.receive_archive(night))
            self.assertEqual(night.reports.counts(), (1, 1))

    def test_legacy_and_sparse_structure_can_agree(self):
        status = sparse_wire('ARCHIVE-004', 'status', [{'executed': True, 'probes': ['D', 'D']}],
                             [[1, 2, '合格'], [1, 1, '复检']])
        with night_shift(self.root / 'night.sqlite') as night:
            self.assert_archive(self.receive_archive(night, status_wire=status))

    def test_read_exact_after_reopen(self):
        path = self.root / 'night.sqlite'
        with night_shift(path) as night:
            old = self.receive_archive(night)
            self.assert_archive(old)
        with source_day(self.root / 'day') as day:
            number, reading, status = issue_columns(day, load_sample('ordered.json'))
        with night_shift(path) as night:
            left, right = receive_column(night.intake, reading), receive_column(night.intake, status)
            issued = review_volume(night.intake, night.reports, number, left.receipt_id, right.receipt_id)
            new = read_report(night.reports, issued.report_id)
            self.assertNotEqual(new.report_id, old.report_id)
            self.assertNotEqual(new.receipt_id, old.receipt_id)
            self.assertEqual(new.snapshot.totals, ReviewTotals(1, 1, ((2, 2),)))
        with night_shift(path) as night:
            old_reopened = read_report(night.reports, old.report_id)
            self.assert_archive(old_reopened)
            self.assertEqual(old_reopened, old)
            self.assertEqual(read_report(night.reports, new.report_id), new)
            self.assertEqual(night.reports.counts(), (2, 2))

    def test_unknown_report(self):
        with night_shift(self.root / 'night.sqlite') as night:
            self.receive_archive(night)
            with self.assertRaises(KeyError):
                read_report(night.reports, 999)
            self.assertEqual(night.reports.counts(), (1, 1))
