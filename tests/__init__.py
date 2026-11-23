import json
import sqlite3
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from export.catalog import ArtifactCatalog
from export.projection import export_column
from receive.catalog import IntakeCatalog
from release.catalog import FrozenCatalog
from release.freeze import freeze
from release.storage import FrozenStorage
from review.catalog import ReportCatalog
from source.intake import register
from source.revisions import RevisionCatalog


SAMPLES = Path(__file__).resolve().parents[1] / 'samples'


def load_sample(filename):
    return json.loads((SAMPLES / filename).read_text(encoding='utf-8'))


@dataclass(frozen=True)
class SourceDay:
    revisions: RevisionCatalog
    frozen: FrozenCatalog
    artifacts: ArtifactCatalog


@dataclass(frozen=True)
class NightShift:
    intake: IntakeCatalog
    reports: ReportCatalog


@contextmanager
def source_day(root):
    with ExitStack() as stack:
        root.mkdir(parents=True, exist_ok=True)
        revisions_db = stack.enter_context(closing(sqlite3.connect(root / 'source.sqlite')))
        frozen_db = stack.enter_context(closing(sqlite3.connect(root / 'frozen.sqlite')))
        artifacts_db = stack.enter_context(closing(sqlite3.connect(root / 'issued.sqlite')))
        day = SourceDay(RevisionCatalog(revisions_db), FrozenCatalog(FrozenStorage(frozen_db)),
                        ArtifactCatalog(artifacts_db))
        for catalog in (day.revisions, day.frozen, day.artifacts):
            catalog.initialize()
        yield day


@contextmanager
def night_shift(path):
    with closing(sqlite3.connect(path)) as connection:
        night = NightShift(IntakeCatalog(connection), ReportCatalog(connection))
        night.intake.initialize()
        night.reports.initialize()
        yield night


def issue_columns(day, data):
    register(day.revisions, data['volume_id'], data['revision_id'], data['rounds'])
    number = freeze(day.revisions, day.frozen, data['volume_id'], data['revision_id'])
    reading = export_column(day.frozen, day.artifacts, number, 'reading')
    status = export_column(day.frozen, day.artifacts, number, 'status')
    return number, reading, status


def sparse_wire(number, column, rounds, values):
    envelope = {
        'version': 1,
        'frozen_number': number,
        'column': column,
        'artifact_id': f'{number}/{column}',
        'payload': {'layout': 'sparse', 'rounds': rounds, 'values': values},
    }
    return json.dumps(envelope, ensure_ascii=False).encode('utf-8')
