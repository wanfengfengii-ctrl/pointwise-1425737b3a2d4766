import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from export.bands import canonical_json, digest
from export.manifest import parse_manifest
from export.projection import export_bands
from receive.intake import receive_block, receive_column
from review.catalog import ReportCommitError, read_band_report
from review.publication import review_bands
from tests import load_sample, night_shift, source_day

ARRIVALS = ('P2', 'D', 'C', 'P1', 'B', 'A')


class BandTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.case = load_sample('bands.json')

    def issue(self, protected=None, cut=None, day_root='day'):
        case = self.case
        with source_day(self.root / day_root) as day:
            source = case['source']
            from release.freeze import freeze
            from source.intake import register
            register(day.revisions, **source)
            number = freeze(day.revisions, day.frozen, source['volume_id'], source['revision_id'])
            return export_bands(day.frozen, day.artifacts, number,
                                case['cut'] if cut is None else cut,
                                case['protected'] if protected is None else protected)

    def deliver(self, night, manifest, blocks, arrival):
        return {slot: receive_block(night.intake, manifest, blocks[slot]) for slot in arrival}

    def test_manifest_identity_and_cut(self):
        manifest, blocks = self.issue()
        parsed = parse_manifest(manifest)
        self.assertEqual(parsed.issue_id, digest(manifest))
        self.assertEqual((parsed.total_rounds, parsed.cut, parsed.protected), (4, 2, ('B', 'C')))
        self.assertEqual(set(blocks), {'A', 'B', 'C', 'D', 'P1', 'P2'})
        self.assertEqual({block['issue_id'] for block in blocks.values()}, {parsed.issue_id})
        self.assertTrue(all(block['slot'] == slot for slot, block in blocks.items()))
        frame_length = parsed.frame_length
        self.assertEqual(frame_length, max(length for length, _ in parsed.originals.values()))
        self.assertEqual({len(blocks[slot]['payload']) for slot in ('P1', 'P2')}, {frame_length})
        for slot in ('A', 'B', 'C', 'D'):
            self.assertEqual(digest(blocks[slot]['payload']), parsed.originals[slot][1])
        for slot in ('P1', 'P2'):
            labels = parsed.parities[slot][0]
            padded = bytearray(frame_length)
            for label in labels:
                payload = blocks[label]['payload'] + bytes(frame_length - len(blocks[label]['payload']))
                for index, byte in enumerate(payload):
                    padded[index] ^= byte
            self.assertEqual(bytes(padded), blocks[slot]['payload'])

    def test_reissue_same_cut_and_protected(self):
        first = self.issue()
        second = self.issue()
        self.assertEqual(first, second)
        # Same frozen volume, same cut, equivalent protection group order.
        third = self.issue(protected=['C', 'B'])
        self.assertEqual(first, third)
        fourth = self.issue(protected=['A', 'D'])
        self.assertNotEqual(first[0], fourth[0])
        self.assertNotEqual(first[1]['P1'], fourth[1]['P1'])

    def test_rejects_invalid_cut_and_protection(self):
        with source_day(self.root / 'day') as day:
            from release.freeze import freeze
            from source.intake import register
            source = self.case['source']
            register(day.revisions, **source)
            number = freeze(day.revisions, day.frozen, source['volume_id'], source['revision_id'])
            for cut in (0, 4, 5, -1, True, 2.0, '2'):
                with self.subTest(cut=cut), self.assertRaises(ValueError):
                    export_bands(day.frozen, day.artifacts, number, cut, ['B', 'C'])
            for protected in ([], ['A'], ['A', 'B'], ['C', 'C'], ['A', 'D', 'B'],
                              ('A', 'D'), ['a', 'd'], 'AD'):
                with self.subTest(protected=protected), self.assertRaises(ValueError):
                    export_bands(day.frozen, day.artifacts, number, 2, protected)
            with self.assertRaises(KeyError):
                export_bands(day.frozen, day.artifacts, 'unknown', 2, ['B', 'C'])

    def test_one_frame_leaves_both_bands_pending(self):
        manifest, blocks = self.issue()
        with night_shift(self.root / 'night.sqlite') as night:
            have = self.deliver(night, manifest, blocks, ['D', 'A'])
            result = review_bands(night.intake, night.reports, manifest, list(have.values()))
            self.assertIsNone(result['report_id'])
            self.assertIsNone(result['receipt_id'])
            self.assertEqual(result['snapshot']['bands'], [])
            self.assertEqual(result['snapshot']['pending'], [1, 2])
            self.assertEqual(night.reports.band_counts(), (0, 0))

    def test_one_frame_that_depends_on_the_missing_band_stays_pending(self):
        manifest, blocks = self.issue()
        with night_shift(self.root / 'night.sqlite') as night:
            have = self.deliver(night, manifest, blocks, ['P1', 'D', 'A'])
            result = review_bands(night.intake, night.reports, manifest, list(have.values()))
            self.assertEqual((result['report_id'], result['snapshot']['bands']), (None, []))
            self.assertEqual(result['snapshot']['pending'], [1, 2])

    def test_second_frame_completes_both_bands(self):
        manifest, blocks = self.issue()
        with night_shift(self.root / 'night.sqlite') as night:
            have = self.deliver(night, manifest, blocks, ['D', 'A'])
            pending = review_bands(night.intake, night.reports, manifest, list(have.values()))
            have.update(self.deliver(night, manifest, blocks, ['P2']))
            partial = review_bands(night.intake, night.reports, manifest, list(have.values()))
            self.assertEqual(partial['snapshot']['pending'], [2])
            self.assertEqual([band['band'] for band in partial['snapshot']['bands']], [1])
            self.assertEqual(partial['snapshot']['bands'][0], {
                **self.case['expected'][0],
                'sources': {
                    'A': [have['A']],
                    'B': sorted([have['A'], have['D'], have['P2']]),
                },
            })
            have.update(self.deliver(night, manifest, blocks, ['P1']))
            full = review_bands(night.intake, night.reports, manifest, list(have.values()))
            self.assertEqual([band['band'] for band in full['snapshot']['bands']], [1, 2])
            self.assertEqual(full['snapshot']['pending'], [])
            self.assertIsInstance(full['report_id'], int)
            self.assertTrue(full['receipt_id'])
            for band in full['snapshot']['bands']:
                for target, witness in band['sources'].items():
                    if target in have:
                        self.assertEqual(witness, [have[target]])
            self.assertEqual(night.reports.band_counts(), (2, 2))

    def test_loss_recovery_for_both_relations(self):
        first = self.issue()
        second = self.issue(protected=['A', 'D'], day_root='day-2')
        losses = self.case['losses'][:-1]
        for index, (manifest, blocks, lost_sets) in enumerate((
                (first[0], first[1], self.case['losses']),
                (second[0], second[1], losses + ['AD']))):
            for lost in lost_sets:
                with self.subTest(group=index, lost=lost):
                    with night_shift(self.root / f'loss-{index}-{lost}.sqlite') as night:
                        arrived = [slot for slot in ARRIVALS if slot not in set(lost)]
                        have = self.deliver(night, manifest, blocks, arrived)
                        result = review_bands(night.intake, night.reports, manifest, list(have.values()))
                        self.assertEqual([band['band'] for band in result['snapshot']['bands']], [1, 2])
                        self.assertEqual(result['snapshot']['pending'], [])
                        self.assertEqual(
                            [{key: value for key, value in band.items() if key != 'sources'}
                             for band in result['snapshot']['bands']],
                            self.case['expected'])

    def test_unselected_receipts_do_not_count(self):
        manifest, blocks = self.issue()
        with night_shift(self.root / 'night.sqlite') as night:
            have = self.deliver(night, manifest, blocks, ['A', 'B', 'C', 'D', 'P1', 'P2'])
            selected = [have['A'], have['D'], have['P2']]
            result = review_bands(night.intake, night.reports, manifest, selected)
            self.assertEqual([band['band'] for band in result['snapshot']['bands']], [1])
            self.assertEqual(result['snapshot']['pending'], [2])
            band = result['snapshot']['bands'][0]
            self.assertEqual(band['sources']['A'], [have['A']])
            self.assertEqual(set(band['sources']['B']), {have['A'], have['D'], have['P2']})
            repeated = review_bands(night.intake, night.reports, manifest,
                                    selected + selected[:1])
            self.assertEqual(repeated['snapshot'], result['snapshot'])
            empty = review_bands(night.intake, night.reports, manifest, [])
            self.assertEqual(empty['snapshot']['pending'], [1, 2])

    def test_rejects_foreign_unknown_and_conflicting_evidence(self):
        manifest, blocks = self.issue()
        other_manifest, other = self.issue(protected=['A', 'D'], day_root='day-2')
        with night_shift(self.root / 'night.sqlite') as night:
            have = self.deliver(night, manifest, blocks, ['A', 'D'])
            foreign = receive_block(night.intake, other_manifest, other['A'])
            with self.assertRaises(ValueError):
                review_bands(night.intake, night.reports, manifest, [have['A'], foreign])
            with self.assertRaises(ValueError):
                review_bands(night.intake, night.reports, manifest, [have['A'], 'IN:missing'])
            legacy = json.dumps({'version': 1, 'frozen_number': 'ARCHIVE-004', 'column': 'reading',
                                 'artifact_id': 'ARCHIVE-004/reading',
                                 'payload': {'layout': 'single-round', 'records': [['D', 8]]}}).encode()
            v1 = receive_column(night.intake, legacy)
            with self.assertRaises(ValueError):
                review_bands(night.intake, night.reports, manifest, [v1.receipt_id])
            with self.assertRaises(ValueError):
                review_bands(night.intake, night.reports, manifest, have['A'])
            self.assertEqual(night.reports.band_counts(), (0, 0))
            damaged = {**blocks['A'], 'payload': bytes([blocks['A']['payload'][0] ^ 1])
                       + blocks['A']['payload'][1:]}
            with self.assertRaises(ValueError):
                receive_block(night.intake, manifest, damaged)
            with self.assertRaises(ValueError):
                receive_block(night.intake, manifest, {**blocks['A'], 'issue_id': 'deadbeef'})
            self.assertEqual(receive_block(night.intake, manifest, blocks['A']), have['A'])
            mangled = dict(other['A'], slot='A')
            with self.assertRaises(ValueError):
                receive_block(night.intake, manifest, mangled)

    def test_conflicting_same_slot_delivery_is_rejected(self):
        manifest, blocks = self.issue()
        path = self.root / 'night.sqlite'
        with night_shift(path) as night:
            accepted = receive_block(night.intake, manifest, blocks['A'])
            self.assertEqual(receive_block(night.intake, manifest, blocks['A']), accepted)
            changed = dict(blocks['A'], payload=blocks['B']['payload'])
            with self.assertRaises(ValueError):
                receive_block(night.intake, manifest, changed)
            self.assertEqual(night.intake.get_band(accepted).slot, 'A')
            self.assertEqual(len(night.intake.list_bands_for_issue(parse_manifest(manifest).issue_id)), 1)
        with night_shift(path) as night:
            self.assertEqual(receive_block(night.intake, manifest, blocks['A']), accepted)

    def test_manifest_must_be_canonical(self):
        manifest, blocks = self.issue()
        data = json.loads(manifest)
        for variant in (json.dumps(data, indent=2).encode(), manifest[:-1], b'\xff',
                        b'{"version":2,"version":2}', canonical_json({**data, 'version': 1})):
            with self.subTest(variant=variant[:20]):
                with self.assertRaises(ValueError):
                    receive_block(self.fake_night(), variant, blocks['A'])
                with self.assertRaises(ValueError):
                    review_bands(self.fake_night(), None, variant, [])

    def fake_night(self):
        from receive.catalog import IntakeCatalog
        import sqlite3
        connection = sqlite3.connect(':memory:')
        catalog = IntakeCatalog(connection)
        catalog.initialize()
        return catalog

    def test_commit_failure_leaves_no_band_report(self):
        manifest, blocks = self.issue()
        with night_shift(self.root / 'night.sqlite') as night:
            have = self.deliver(night, manifest, blocks, ARRIVALS)

            def interrupt():
                raise RuntimeError('shift ledger rejected the commit')

            with self.assertRaises(ReportCommitError):
                review_bands(night.intake, night.reports, manifest, list(have.values()),
                             before_commit=interrupt)
            self.assertEqual(night.reports.band_counts(), (0, 0))
            issued = review_bands(night.intake, night.reports, manifest, list(have.values()))
            self.assertEqual(read_band_report(night.reports, issued['report_id']), issued)

    def test_reopen_keeps_prior_result_and_receipts(self):
        manifest, blocks = self.issue()
        path = self.root / 'night.sqlite'
        with source_day(self.root / 'day') as day:
            pass
        with night_shift(path) as night:
            have = self.deliver(night, manifest, blocks, ['A', 'D'])
            none = review_bands(night.intake, night.reports, manifest, list(have.values()))
            have.update(self.deliver(night, manifest, blocks, ['P2']))
            partial = review_bands(night.intake, night.reports, manifest, list(have.values()))
            self.assertIsNotNone(partial['report_id'])
        with night_shift(path) as night:
            self.assertEqual(receive_block(night.intake, manifest, blocks['A']), have['A'])
            self.assertEqual(read_band_report(night.reports, partial['report_id']), partial)
            with self.assertRaises(KeyError):
                read_band_report(night.reports, 999)
            self.assertEqual(none['snapshot']['pending'], [1, 2])
            frame = receive_block(night.intake, manifest, blocks['P1'])
            later = review_bands(night.intake, night.reports, manifest,
                                 [have['A'], have['D'], have['P2'], frame])
            self.assertEqual(read_band_report(night.reports, partial['report_id']), partial)
            self.assertEqual([band['band'] for band in later['snapshot']['bands']], [1, 2])
            self.assertEqual(night.reports.band_counts(), (2, 2))
    def test_export_reissues_after_reopen(self):
        manifest, blocks = self.issue()
        again_manifest, again_blocks = self.issue()
        self.assertEqual((again_manifest, again_blocks), (manifest, blocks))
        reopened_manifest, reopened_blocks = self.issue()
        self.assertEqual(reopened_blocks, blocks)
        self.assertEqual(parse_manifest(reopened_manifest).issue_id, parse_manifest(manifest).issue_id)

    def test_band_and_column_history_stay_separate(self):
        manifest, blocks = self.issue()
        from receive.intake import receive_column
        from review.catalog import read_report
        from review.publication import review_volume
        from tests import issue_columns
        with source_day(self.root / 'day') as day:
            number, reading, status = issue_columns(day, self.case['source'])
        with night_shift(self.root / 'night.sqlite') as night:
            left = receive_column(night.intake, reading)
            right = receive_column(night.intake, status)
            report = review_volume(night.intake, night.reports, number, left.receipt_id, right.receipt_id)
            have = self.deliver(night, manifest, blocks, ARRIVALS)
            result = review_bands(night.intake, night.reports, manifest, list(have.values()))
            self.assertEqual(night.reports.counts(), (1, 1))
            self.assertEqual(night.reports.band_counts(), (1, 1))
            with self.assertRaises(KeyError):
                read_band_report(night.reports, 999)
            self.assertEqual(read_report(night.reports, report.report_id).snapshot.frozen_number, number)
            with self.assertRaises(KeyError):
                read_report(night.reports, result['report_id'])

    def handcraft(self, manifest, originals, parities, frames):
        """Rebuild a manifest with arbitrary relations over the issued originals."""
        from export.bands import canonical_json
        data = json.loads(manifest)
        data['parities'] = {slot: [list(labels), digest(frames[slot])]
                            for slot, labels in parities.items()}
        body = canonical_json(data)
        issue_id = digest(body)
        pieces = {slot: {'issue_id': issue_id, 'slot': slot, 'payload': payload}
                  for slot, payload in originals.items()}
        pieces.update({slot: {'issue_id': issue_id, 'slot': slot, 'payload': frames[slot]}
                       for slot in parities})
        return body, pieces

    def frame(self, originals, labels):
        length = max(len(payload) for payload in originals.values())
        body = bytearray(length)
        for label in labels:
            padded = originals[label] + bytes(length - len(originals[label]))
            for index, byte in enumerate(padded):
                body[index] ^= byte
        return bytes(body)

    def test_foreign_relation_is_honoured(self):
        manifest, blocks = self.issue()
        originals = {slot: blocks[slot]['payload'] for slot in 'ABCD'}
        # Another relation the day shift may pick for the B/C protection group.
        frames = {'P1': self.frame(originals, ['A', 'B', 'D']),
                  'P2': self.frame(originals, ['A', 'C', 'D'])}
        body, pieces = self.handcraft(manifest, originals,
                                      {'P1': ['A', 'B', 'D'], 'P2': ['A', 'C', 'D']}, frames)
        with night_shift(self.root / 'night.sqlite') as night:
            have = {slot: receive_block(night.intake, body, pieces[slot])
                    for slot in ('A', 'B', 'C', 'P1', 'P2')}
            result = review_bands(night.intake, night.reports, body, list(have.values()))
            self.assertEqual([band['band'] for band in result['snapshot']['bands']], [1, 2])
            recovered = set(result['snapshot']['bands'][1]['sources']['D'])
            self.assertIn(recovered, ({have['A'], have['B'], have['P1']},
                                      {have['A'], have['C'], have['P2']}))

    def test_contradictory_relations_reject_the_run(self):
        manifest, blocks = self.issue()
        originals = {slot: blocks[slot]['payload'] for slot in 'ABCD'}
        honest = self.frame(originals, ['A', 'B'])
        frames = {'P1': honest, 'P2': bytes([honest[0] ^ 1]) + honest[1:]}
        body, pieces = self.handcraft(manifest, originals,
                                      {'P1': ['A', 'B'], 'P2': ['A', 'B']}, frames)
        with night_shift(self.root / 'night.sqlite') as night:
            have = {slot: receive_block(night.intake, body, pieces[slot])
                    for slot in ('A', 'B', 'C', 'D', 'P1', 'P2')}
            with self.assertRaises(ValueError):
                review_bands(night.intake, night.reports, body, list(have.values()))
            self.assertEqual(night.reports.band_counts(), (0, 0))


if __name__ == '__main__':
    unittest.main()
