import json
from dataclasses import dataclass

from receive.selection import SelectedColumns
from review.reconstruct import ReconstructedVolume
from review.totals import ReviewTotals
from source.rounds import Round, rounds_as_records
from source.validation import validate_rounds


@dataclass(frozen=True)
class ReportSnapshot:
    frozen_number: str
    reading_receipt_id: str
    status_receipt_id: str
    rounds: tuple[Round, ...]
    totals: ReviewTotals


def build_report_snapshot(selected: SelectedColumns, volume: ReconstructedVolume,
                          totals: ReviewTotals) -> ReportSnapshot:
    return ReportSnapshot(volume.frozen_number, selected.reading_receipt_id,
                          selected.status_receipt_id, volume.rounds, totals)


def encode_report_snapshot(snapshot: ReportSnapshot) -> str:
    return json.dumps({
        'frozen_number': snapshot.frozen_number,
        'reading_receipt_id': snapshot.reading_receipt_id,
        'status_receipt_id': snapshot.status_receipt_id,
        'rounds': rounds_as_records(snapshot.rounds),
        'reading_count': snapshot.totals.reading_count,
        'status_count': snapshot.totals.status_count,
        'recheck_positions': snapshot.totals.recheck_positions,
    }, separators=(',', ':'))


def decode_report_snapshot(body: str) -> ReportSnapshot:
    record = json.loads(body)
    totals = ReviewTotals(record['reading_count'], record['status_count'],
                         tuple(tuple(position) for position in record['recheck_positions']))
    return ReportSnapshot(record['frozen_number'], record['reading_receipt_id'],
                          record['status_receipt_id'], validate_rounds(record['rounds']), totals)
