import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from export.envelope import decode_envelope
from export.projection import export_column
from receive.catalog import DeliveryConflict
from receive.intake import receive_column
from receive.selection import available_columns, select_pair
from receive.validation import validate_column_envelope
from review.catalog import read_report
from review.publication import review_volume
from tests import issue_columns, load_sample, night_shift, source_day


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def make_columns(self, filename='ordinary.json'):
        with source_day(self.root / 'day') as day:
            return issue_columns(day, load_sample(filename))

    def test_arrival_orders_and_replay(self):
        number, reading, status = self.make_columns()
        selections = []
        for index, order in enumerate(((status, reading), (reading, status))):
            with self.subTest(order=index), night_shift(self.root / f'night-{index}.sqlite') as night:
                self.assertEqual(available_columns(night.intake, number), {})
                first = receive_column(night.intake, order[0])
                self.assertEqual(available_columns(night.intake, number), {first.column: first})
                second = receive_column(night.intake, order[1])
                self.assertEqual(receive_column(night.intake, order[0]), first)
                self.assertEqual(set(available_columns(night.intake, number)), {'reading', 'status'})
                self.assertEqual(len(night.intake.list_for_volume(number)), 2)
                selections.append(select_pair(night.intake, number, first.receipt_id, second.receipt_id))
        self.assertEqual(selections[0], selections[1])
        self.assertEqual(selections[0].reading.wire, reading)
        self.assertEqual(selections[0].status.wire, status)

    def test_reissue(self):
        number, reading, status = self.make_columns()
        with source_day(self.root / 'day') as day:
            for column, original in (('reading', reading), ('status', status)):
                with self.subTest(column=column):
                    saved = export_column(day.frozen, day.artifacts, number, column)
                    self.assertEqual(saved, original)
                    envelope = decode_envelope(saved)
                    self.assertEqual(envelope.artifact_id, f'{number}/{column}')
                    self.assertEqual(envelope.frozen_number, number)
                    self.assertEqual(envelope.column, column)

    def test_source_evidence(self):
        _, reading, status = self.make_columns('ordered.json')
        reading = validate_column_envelope(reading)
        status = validate_column_envelope(status)
        expected = ((True, ()), (True, ('A', 'A')), (False, ()))
        structures = [column.structure for column in (reading, status) if column.structure is not None]
        self.assertTrue(structures)
        for structure in structures:
            self.assertEqual(structure, expected)
        self.assertEqual(reading.values, ((2, 1, 17),))
        self.assertEqual(status.values, ((2, 2, '复检'),))

    def test_reopen_receipts(self):
        number, reading, status = self.make_columns()
        path = self.root / 'night.sqlite'
        with night_shift(path) as night:
            saved = receive_column(night.intake, status)
        with night_shift(path) as night:
            self.assertEqual(available_columns(night.intake, number), {'status': saved})
            self.assertEqual(night.intake.get(saved.receipt_id), saved)
            self.assertEqual(receive_column(night.intake, status), saved)
            receive_column(night.intake, reading)
            self.assertEqual(len(night.intake.list_for_volume(number)), 2)

    def test_conflicting_delivery(self):
        number, reading, status = self.make_columns()
        changed = json.loads(reading)
        changed['payload']['values'][0][2] = 99
        conflicting_wire = json.dumps(changed).encode('utf-8')
        validate_column_envelope(conflicting_wire)
        path = self.root / 'night.sqlite'
        with night_shift(path) as night:
            accepted = receive_column(night.intake, reading)
            different_spacing = json.dumps(json.loads(reading), indent=2).encode('utf-8')
            self.assertEqual(receive_column(night.intake, different_spacing), accepted)
            with self.assertRaises(DeliveryConflict):
                receive_column(night.intake, conflicting_wire)
        with night_shift(path) as night:
            self.assertEqual(night.intake.count_conflicts(accepted.artifact_id), 1)
            self.assertEqual(night.intake.get(accepted.receipt_id), accepted)
            self.assertEqual(available_columns(night.intake, number), {'reading': accepted})
            quality = receive_column(night.intake, status)
            result = review_volume(night.intake, night.reports, number, accepted.receipt_id, quality.receipt_id)
            saved = read_report(night.reports, result.report_id)
            self.assertEqual([probe.reading for probe in saved.snapshot.rounds[0].probes], [0, 21])
            self.assertEqual(saved.snapshot.totals.recheck_positions, ((1, 2),))

    def test_invalid_envelope_leaves_intake_empty(self):
        number, reading, _ = self.make_columns()
        data = json.loads(reading)
        variants = [reading[:-1], b'\xff', b'{"version": 1, "version": 1}',
                    json.dumps({**data, 'version': True}).encode(),
                    json.dumps({**data, 'artifact_id': 'foreign/reading'}).encode()]
        with night_shift(self.root / 'night.sqlite') as night:
            for wire in variants:
                with self.subTest(wire=wire), self.assertRaises(ValueError):
                    receive_column(night.intake, wire)
            self.assertEqual(available_columns(night.intake, number), {})
            self.assertEqual(night.reports.counts(), (0, 0))

    def test_issuance_rejects_unknown_volume_and_column(self):
        number, _, _ = self.make_columns()
        with source_day(self.root / 'day') as day:
            with self.assertRaises(ValueError):
                export_column(day.frozen, day.artifacts, number, 'voltage')
            with self.assertRaises(KeyError):
                export_column(day.frozen, day.artifacts, 'unknown', 'reading')
            self.assertIsNone(day.artifacts.get('unknown', 'reading'))
