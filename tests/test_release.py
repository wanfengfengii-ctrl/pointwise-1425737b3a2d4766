import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from release.freeze import freeze
from source.intake import register
from source.rounds import ProbeOccurrence, Round
from tests import load_sample, source_day


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.original = load_sample('ordinary.json')
        self.corrected = copy.deepcopy(self.original)
        self.corrected['revision_id'] = 'R2'
        self.corrected['rounds'][0]['probes'][1]['reading'] = 23

    def test_correction_keeps_original(self):
        with source_day(self.root) as day:
            self.assertEqual(register(day.revisions, **self.original), 'R1')
            old_number = freeze(day.revisions, day.frozen, 'day-41', 'R1')
            self.assertEqual(register(day.revisions, **self.corrected), 'R2')
            new_number = freeze(day.revisions, day.frozen, 'day-41', 'R2')
            self.assertNotEqual(old_number, new_number)
            self.assertEqual(day.frozen.get(old_number).content.rounds, (
                Round(True, (ProbeOccurrence('P1', 0, '合格'), ProbeOccurrence('P2', 21, '复检'))),
            ))
            self.assertEqual(day.frozen.get(new_number).content.rounds, (
                Round(True, (ProbeOccurrence('P1', 0, '合格'), ProbeOccurrence('P2', 23, '复检'))),
            ))

    def test_conflicting_revision(self):
        with source_day(self.root) as day:
            self.assertEqual(register(day.revisions, **self.original), 'R1')
            self.assertEqual(register(day.revisions, **self.original), 'R1')
            conflict = {**self.corrected, 'revision_id': 'R1'}
            with self.assertRaisesRegex(ValueError, 'different content'):
                register(day.revisions, **conflict)
            number = freeze(day.revisions, day.frozen, 'day-41', 'R1')
            volume = day.frozen.get(number)
            self.assertEqual((volume.volume_id, volume.revision_id), ('day-41', 'R1'))
            self.assertEqual(volume.content.rounds[0].probes[1].reading, 21)

    def test_exact_revision(self):
        with source_day(self.root) as day:
            register(day.revisions, **self.original)
            register(day.revisions, **self.corrected)
            new = freeze(day.revisions, day.frozen, 'day-41', 'R2')
            old = freeze(day.revisions, day.frozen, 'day-41', 'R1')
            self.assertNotEqual(old, new)
            self.assertEqual(freeze(day.revisions, day.frozen, 'day-41', 'R1'), old)
            for number, revision, reading in ((old, 'R1', 21), (new, 'R2', 23)):
                with self.subTest(revision=revision):
                    snapshot = day.frozen.get(number)
                    self.assertEqual((snapshot.frozen_number, snapshot.volume_id, snapshot.revision_id),
                                     (number, 'day-41', revision))
                    self.assertEqual(snapshot.content.rounds[0].probes[1].reading, reading)

    def test_reopen_revisions(self):
        with source_day(self.root) as day:
            register(day.revisions, **self.original)
            old = freeze(day.revisions, day.frozen, 'day-41', 'R1')
            register(day.revisions, **self.corrected)
            new = freeze(day.revisions, day.frozen, 'day-41', 'R2')
        with source_day(self.root) as day:
            for revision, number, reading in (('R1', old, 21), ('R2', new, 23)):
                with self.subTest(revision=revision):
                    data = day.revisions.get('day-41', revision)
                    self.assertEqual(data.rounds[0].probes[1].reading, reading)
                    self.assertEqual(freeze(day.revisions, day.frozen, 'day-41', revision), number)
                    frozen = day.frozen.get(number)
                    self.assertEqual(frozen.revision_id, revision)
                    self.assertEqual(frozen.content.rounds, data.rounds)

    def test_unexecuted_round_rejects_probes(self):
        with source_day(self.root) as day:
            invalid = [{'executed': False, 'probes': [{'name': 'P1', 'reading': 17}]}]
            with self.assertRaisesRegex(ValueError, 'unexecuted'):
                register(day.revisions, 'day-41', 'bad', invalid)
            with self.assertRaises(KeyError):
                freeze(day.revisions, day.frozen, 'day-41', 'bad')

    def test_invalid_observation(self):
        inputs = ({'reading': 9.5}, {'reading': True}, {'status': '未知'}, {'status': []})
        with source_day(self.root) as day:
            for index, fields in enumerate(inputs):
                with self.subTest(fields=fields):
                    revision = f'invalid-{index}'
                    rounds = [{'executed': True, 'probes': [{'name': 'P1', **fields}]}]
                    with self.assertRaises(ValueError):
                        register(day.revisions, 'day-41', revision, rounds)
                    with self.assertRaises(KeyError):
                        freeze(day.revisions, day.frozen, 'day-41', revision)
