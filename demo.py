import copy
import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory

from export.catalog import ArtifactCatalog
from export.projection import export_bands, export_column
from receive.catalog import IntakeCatalog
from receive.intake import receive_block, receive_column
from release.catalog import FrozenCatalog
from release.freeze import freeze
from release.storage import FrozenStorage
from review.catalog import ReportCatalog, read_band_report, read_report
from review.publication import review_bands, review_volume
from source.intake import register
from source.revisions import RevisionCatalog
from source.rounds import rounds_as_records


def issue_approved_columns(root, original):
    with ExitStack() as stack:
        source = stack.enter_context(closing(sqlite3.connect(root / 'source.sqlite')))
        release = stack.enter_context(closing(sqlite3.connect(root / 'frozen.sqlite')))
        delivery = stack.enter_context(closing(sqlite3.connect(root / 'issued.sqlite')))
        revisions = RevisionCatalog(source)
        frozen = FrozenCatalog(FrozenStorage(release))
        artifacts = ArtifactCatalog(delivery)
        for catalog in (revisions, frozen, artifacts):
            catalog.initialize()
        volume = original['volume_id']
        register(revisions, **original)
        corrected = copy.deepcopy(original)
        corrected['revision_id'] = 'R2'
        corrected['rounds'][1]['probes'][0]['reading'] = 19
        register(revisions, **corrected)
        corrected_number = freeze(revisions, frozen, volume, 'R2')
        approved_number = freeze(revisions, frozen, volume, original['revision_id'])
        if corrected_number == approved_number:
            raise AssertionError('original and corrected revisions share a frozen number')
        return (approved_number, export_column(frozen, artifacts, approved_number, 'reading'),
                export_column(frozen, artifacts, approved_number, 'status'))


def sign_offline(path, number, reading, status):
    with closing(sqlite3.connect(path)) as connection:
        intake = IntakeCatalog(connection)
        reports = ReportCatalog(connection)
        intake.initialize()
        reports.initialize()
        quality = receive_column(intake, status)
        millivolts = receive_column(intake, reading)
        return review_volume(intake, reports, number, quality.receipt_id, millivolts.receipt_id)


def issue_bands(root, case):
    root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        source = stack.enter_context(closing(sqlite3.connect(root / 'band-source.sqlite')))
        release = stack.enter_context(closing(sqlite3.connect(root / 'band-frozen.sqlite')))
        delivery = stack.enter_context(closing(sqlite3.connect(root / 'band-issued.sqlite')))
        revisions = RevisionCatalog(source)
        frozen = FrozenCatalog(FrozenStorage(release))
        artifacts = ArtifactCatalog(delivery)
        for catalog in (revisions, frozen, artifacts):
            catalog.initialize()
        register(revisions, **case['source'])
        number = freeze(revisions, frozen, case['source']['volume_id'], case['source']['revision_id'])
        return export_bands(frozen, artifacts, number, case['cut'], case['protected'])


def sign_bands(night_path, manifest, blocks):
    """A night shift with no source library: originals first, then the two frames."""
    with closing(sqlite3.connect(night_path)) as connection:
        intake = IntakeCatalog(connection)
        reports = ReportCatalog(connection)
        intake.initialize()
        reports.initialize()
        originals = [receive_block(intake, manifest, blocks[slot]) for slot in ('D', 'A')]
        both_missing = review_bands(intake, reports, manifest, originals)
        frame_one = receive_block(intake, manifest, blocks['P2'])
        one_frame = review_bands(intake, reports, manifest, originals + [frame_one])
        frame_two = receive_block(intake, manifest, blocks['P1'])
        two_frames = review_bands(intake, reports, manifest, originals + [frame_one, frame_two])
    return both_missing, one_frame, two_frames


def band_demo(temp):
    from tests import load_sample
    case = load_sample('bands.json')
    root = Path(temp) / 'bands'
    manifest, blocks = issue_bands(root, case)
    night_path = root / 'night.sqlite'
    both_missing, one_frame, two_frames = sign_bands(night_path, manifest, blocks)
    if both_missing['report_id'] is not None or both_missing['snapshot']['pending'] != [1, 2]:
        raise AssertionError(f'originals alone did not leave both bands pending: {both_missing["snapshot"]!r}')
    if one_frame['report_id'] is None or one_frame['snapshot']['pending'] != [2]:
        raise AssertionError(f'one frame did not complete exactly the front band: {one_frame["snapshot"]!r}')
    if [band['band'] for band in two_frames['snapshot']['bands']] != [1, 2]:
        raise AssertionError(f'two frames did not complete both bands: {two_frames["snapshot"]!r}')
    expected = [{key: value for key, value in band.items() if key != 'sources'} for band in case['expected']]
    actual = [{key: value for key, value in band.items() if key != 'sources'}
              for band in two_frames['snapshot']['bands']]
    if actual != expected:
        raise AssertionError(f'BAND_RESULT actual={actual!r} expected={expected!r}')
    with closing(sqlite3.connect(night_path)) as connection:
        reports = ReportCatalog(connection)
        reports.initialize()
        reopened = read_band_report(reports, two_frames['report_id'])
        one_frame_reopened = read_band_report(reports, one_frame['report_id'])
    if reopened != two_frames or one_frame_reopened != one_frame:
        raise AssertionError('reopened band report did not preserve the issued snapshot and receipt')
    print(f'双带卷 {case["source"]["volume_id"]}，一帧两带待齐；两帧报告 {two_frames["report_id"]}，'
          f'回执 {two_frames["receipt_id"]}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'bands', 'cut': case['cut'], 'protected': case['protected'],
        'both_missing': {'report_id': both_missing['report_id'],
                         'pending': both_missing['snapshot']['pending']},
        'one_frame': {'report_id': one_frame['report_id'], 'receipt_id': one_frame['receipt_id'],
                      'pending': one_frame['snapshot']['pending'],
                      'bands': [{key: value for key, value in band.items() if key != 'sources'}
                                for band in one_frame['snapshot']['bands']]},
        'two_frames': {'report_id': two_frames['report_id'], 'receipt_id': two_frames['receipt_id'],
                       'bands': actual},
        'reopened': {'report_id': reopened['report_id'], 'receipt_id': reopened['receipt_id']},
        'code_paths': ['export/bands.py', 'export/manifest.py', 'receive/catalog.py',
                       'review/bands.py', 'review/catalog.py'],
        'oracle': '第1轮空执行、第2轮两个同名探头；前带 17 毫伏在 (2,1)、复检在 (2,2)；'
                  '后带第3轮未执行、第4轮 0 毫伏且合格。',
    }, ensure_ascii=False, separators=(',', ':')))


def main():
    sample = Path(__file__).with_name('samples') / 'ordered.json'
    original = json.loads(sample.read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='binchuan-') as temp:
        root = Path(temp)
        number, reading, status = issue_approved_columns(root, original)
        night_path = root / 'night.sqlite'
        issued = sign_offline(night_path, number, reading, status)
        with closing(sqlite3.connect(night_path)) as connection:
            reports = ReportCatalog(connection)
            reports.initialize()
            stored = read_report(reports, issued.report_id)
            totals = stored.snapshot.totals
            actual = rounds_as_records(stored.snapshot.rounds)
            downstream = {'reading_count': totals.reading_count, 'status_count': totals.status_count,
                          'recheck_positions': [list(position) for position in totals.recheck_positions]}
            expected = [
                {'executed': True, 'probes': []},
                {'executed': True, 'probes': [
                    {'name': 'A', 'reading': 17, 'status': None},
                    {'name': 'A', 'reading': None, 'status': '复检'},
                ]},
                {'executed': False, 'probes': []},
            ]
            expected_downstream = {'reading_count': 1, 'status_count': 1, 'recheck_positions': [[2, 2]]}
            if (actual, downstream) != (expected, expected_downstream):
                raise AssertionError(f'INCIDENT_COLUMN_ALIGNMENT actual={(actual, downstream)!r} '
                                     f'expected={(expected, expected_downstream)!r}')
            if stored.receipt_id != issued.receipt_id or stored.snapshot.frozen_number != number:
                raise AssertionError('reopened report did not preserve the issued receipt and frozen number')
            print(f'冻结卷 {number}，报告 {stored.report_id}，回执 {stored.receipt_id}')
            evidence = {
                'case_id': 'v5', 'input': {'source': original, 'arrival_order': ['status', 'reading']},
                'expected': expected, 'actual': actual,
                'downstream_expected': expected_downstream, 'downstream_actual': downstream,
                'code_paths': ['export/projection.py', 'receive/selection.py', 'review/reconstruct.py',
                               'review/totals.py', 'review/catalog.py'],
                'oracle': '逐轮手点轮次执行状态及探头次序；第二轮首探头仅有 17 毫伏，次探头仅有复检。',
            }
            print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, separators=(',', ':')))
        band_demo(temp)


if __name__ == '__main__':
    main()
