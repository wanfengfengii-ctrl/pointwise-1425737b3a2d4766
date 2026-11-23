import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from receive.intake import receive_column
from receive.selection import available_columns
from review.catalog import ReportCommitError, read_report
from review.publication import review_volume
from review.totals import ReviewTotals
from source.rounds import ProbeOccurrence, Round
from tests import issue_columns, load_sample, night_shift, source_day, sparse_wire


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def issue(self, data):
        with source_day(self.root / 'day') as day:
            return issue_columns(day, data)

    def review(self, night, evidence, *, status_first=False, before_commit=None):
        number, reading, status = evidence
        order = (status, reading) if status_first else (reading, status)
        receipts = [receive_column(night.intake, wire) for wire in order]
        issued = review_volume(night.intake, night.reports, number,
                               receipts[0].receipt_id, receipts[1].receipt_id,
                               before_commit=before_commit)
        saved = read_report(night.reports, issued.report_id)
        self.assertEqual(saved.receipt_id, issued.receipt_id)
        self.assertEqual(saved.snapshot.frozen_number, number)
        sources = {receipt.column: receipt.receipt_id for receipt in receipts}
        self.assertEqual(saved.snapshot.reading_receipt_id, sources['reading'])
        self.assertEqual(saved.snapshot.status_receipt_id, sources['status'])
        return saved

    def assert_ordered_result(self, saved):
        expected = (
            (Round(True, ()), Round(True, (ProbeOccurrence('A', 17), ProbeOccurrence('A', None, '复检'))),
             Round(False, ())),
            ReviewTotals(1, 1, ((2, 2),)),
        )
        actual = (saved.snapshot.rounds, saved.snapshot.totals)
        self.assertEqual(actual, expected, f'INCIDENT_COLUMN_ALIGNMENT actual={actual!r} expected={expected!r}')

    def test_column_alignment(self):
        evidence = self.issue(load_sample('ordered.json'))
        results = []
        for status_first in (False, True):
            with self.subTest(status_first=status_first), night_shift(self.root / f'{status_first}.sqlite') as night:
                saved = self.review(night, evidence, status_first=status_first)
                self.assert_ordered_result(saved)
                self.assertEqual(night.reports.counts(), (1, 1))
                results.append(saved.snapshot)
        if len(results) == 2:
            self.assertEqual(results[0], results[1])

    def test_independent_columns(self):
        number = 'EXTERNAL-17'
        structure = [{'executed': True, 'probes': []},
                     {'executed': True, 'probes': ['A', 'A']},
                     {'executed': False, 'probes': []}]
        evidence = (number, sparse_wire(number, 'reading', structure, [[2, 1, 17]]),
                    sparse_wire(number, 'status', structure, [[2, 2, '复检']]))
        for status_first in (False, True):
            with self.subTest(status_first=status_first), night_shift(self.root / f'{status_first}.sqlite') as night:
                self.assert_ordered_result(self.review(night, evidence, status_first=status_first))

    def test_equal_and_all_missing(self):
        data = {'volume_id': 'equal', 'revision_id': 'R1', 'rounds': [
            {'executed': True, 'probes': [
                {'name': 'A', 'reading': 5, 'status': '复检'},
                {'name': 'A', 'reading': 5, 'status': '复检'},
                {'name': 'A'},
            ]},
            {'executed': True, 'probes': [{'name': 'A', 'reading': 0, 'status': '合格'}, {'name': 'A'}]},
        ]}
        evidence = self.issue(data)
        with night_shift(self.root / 'night.sqlite') as night:
            snapshot = self.review(night, evidence).snapshot
            self.assertEqual(snapshot.rounds, (
                Round(True, (ProbeOccurrence('A', 5, '复检'), ProbeOccurrence('A', 5, '复检'), ProbeOccurrence('A'))),
                Round(True, (ProbeOccurrence('A', 0, '合格'), ProbeOccurrence('A'))),
            ))
            self.assertEqual(snapshot.totals, ReviewTotals(3, 3, ((1, 1), (1, 2))))

    def test_only_empty_rounds(self):
        with source_day(self.root / 'day') as day:
            evidence = [issue_columns(day, {'volume_id': 'empty', 'revision_id': f'R{executed}',
                                            'rounds': [{'executed': executed, 'probes': []}]})
                        for executed in (True, False)]
        with night_shift(self.root / 'night.sqlite') as night:
            snapshots = [self.review(night, columns).snapshot for columns in evidence]
            self.assertEqual([snapshot.rounds for snapshot in snapshots], [(Round(True, ()),), (Round(False, ()),)])
            self.assertEqual([snapshot.totals for snapshot in snapshots], [ReviewTotals(0, 0, ())] * 2)
            self.assertEqual(night.reports.counts(), (2, 2))

    def zero_data(self):
        return {'volume_id': 'zero', 'revision_id': 'R1', 'rounds': [
            {'executed': True, 'probes': [{'name': 'Z', 'reading': 0}, {'name': 'Z', 'status': '复检'}]},
        ]}

    def test_zero_millivolts(self):
        evidence = self.issue(self.zero_data())
        with night_shift(self.root / 'night.sqlite') as night:
            snapshot = self.review(night, evidence).snapshot
            self.assertEqual(snapshot.rounds, (Round(True, (ProbeOccurrence('Z', 0), ProbeOccurrence('Z', None, '复检'))),))
            self.assertEqual(snapshot.totals, ReviewTotals(1, 1, ((1, 2),)))

    def test_rejects_missing_foreign_and_repeated_role(self):
        ordinary = load_sample('ordinary.json')
        with source_day(self.root / 'day') as day:
            number, reading, status = issue_columns(day, ordinary)
            _, _, other_status = issue_columns(day, {**ordinary, 'revision_id': 'R2'})
        with night_shift(self.root / 'night.sqlite') as night:
            left = receive_column(night.intake, reading)
            with self.assertRaisesRegex(ValueError, 'not been received'):
                review_volume(night.intake, night.reports, number, left.receipt_id, f'IN:{number}/status')
            self.assertEqual(night.reports.counts(), (0, 0))
            right = receive_column(night.intake, other_status)
            with self.assertRaisesRegex(ValueError, 'different frozen volume'):
                review_volume(night.intake, night.reports, number, left.receipt_id, right.receipt_id)
            self.assertEqual(available_columns(night.intake, number), {'reading': left})
            with self.assertRaisesRegex(ValueError, 'requires both'):
                review_volume(night.intake, night.reports, number, left.receipt_id, left.receipt_id)
            self.assertEqual(night.reports.counts(), (0, 0))
            accepted = receive_column(night.intake, status)
            self.assertEqual(set(available_columns(night.intake, number)), {'reading', 'status'})
            issued = review_volume(night.intake, night.reports, number, left.receipt_id, accepted.receipt_id)
            self.assertEqual(read_report(night.reports, issued.report_id).snapshot.totals, ReviewTotals(2, 2, ((1, 2),)))

    def test_invalid_origin(self):
        structure = [{'executed': True, 'probes': ['P']}, {'executed': False, 'probes': []}]
        variants = (([[1, 2, 17]], 'does not exist'), ([[2, 1, 17]], 'does not exist'),
                    ([[3, 1, 17]], 'does not exist'), ([[1, 1, 17], [1, 1, 18]], 'declared twice'))
        with night_shift(self.root / 'night.sqlite') as night:
            for index, (values, message) in enumerate(variants):
                with self.subTest(values=values):
                    number = f'bad-origin-{index}'
                    reading = receive_column(night.intake, sparse_wire(number, 'reading', structure, values))
                    status = receive_column(night.intake, sparse_wire(number, 'status', structure, [[1, 1, '复检']]))
                    with self.assertRaisesRegex(ValueError, message):
                        review_volume(night.intake, night.reports, number, reading.receipt_id, status.receipt_id)
                    self.assertEqual(night.reports.counts(), (0, 0))

    def test_structure_conflict(self):
        number = 'structure-conflict'
        probe_round = {'executed': True, 'probes': ['P']}
        left = [{'executed': True, 'probes': []}, probe_round]
        right = [{'executed': False, 'probes': []}, probe_round]
        with night_shift(self.root / 'night.sqlite') as night:
            reading = receive_column(night.intake, sparse_wire(number, 'reading', left, [[2, 1, 17]]))
            status = receive_column(night.intake, sparse_wire(number, 'status', right, [[2, 1, '复检']]))
            with self.assertRaisesRegex(ValueError, 'structure declarations disagree'):
                review_volume(night.intake, night.reports, number, reading.receipt_id, status.receipt_id)
            self.assertEqual(night.reports.counts(), (0, 0))

    def fail_after_writes(self, night, evidence, old):
        observed = []

        def refuse_commit():
            observed.append(night.reports.counts())
            raise RuntimeError('shift ledger rejected the commit')

        with self.assertRaises(ReportCommitError) as error:
            self.review(night, evidence, before_commit=refuse_commit)
        self.assertIsInstance(error.exception.__cause__, RuntimeError)
        self.assertEqual(observed, [(2, 2)])
        self.assertEqual(night.reports.counts(), (1, 1))
        self.assertEqual(read_report(night.reports, old.report_id), old)

    def test_submission_failure(self):
        old_evidence = self.issue(load_sample('ordinary.json'))
        new_evidence = self.issue(load_sample('ordered.json'))
        path = self.root / 'night.sqlite'
        with night_shift(path) as night:
            old = self.review(night, old_evidence)
            self.fail_after_writes(night, new_evidence, old)
        with night_shift(path) as night:
            self.assertEqual(night.reports.counts(), (1, 1))
            self.assertEqual(read_report(night.reports, old.report_id), old)

    def test_zero_retry(self):
        old_evidence = self.issue(load_sample('ordinary.json'))
        zero_evidence = self.issue(self.zero_data())
        path = self.root / 'night.sqlite'
        with night_shift(path) as night:
            old = self.review(night, old_evidence)
            self.fail_after_writes(night, zero_evidence, old)
        with night_shift(path) as night:
            retried = self.review(night, zero_evidence, status_first=True)
            self.assertEqual(retried.snapshot.totals, ReviewTotals(1, 1, ((1, 2),)))
            self.assertEqual(night.reports.counts(), (2, 2))
            self.assertNotEqual(retried.report_id, old.report_id)
            self.assertEqual(read_report(night.reports, old.report_id), old)
