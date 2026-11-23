import copy
import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory

from export.catalog import ArtifactCatalog
from export.projection import export_column
from receive.catalog import IntakeCatalog
from receive.intake import receive_column
from release.catalog import FrozenCatalog
from release.freeze import freeze
from release.storage import FrozenStorage
from review.catalog import ReportCatalog, read_report
from review.publication import review_volume
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


if __name__ == '__main__':
    main()
