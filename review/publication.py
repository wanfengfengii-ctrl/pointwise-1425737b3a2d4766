from collections.abc import Callable

from receive.catalog import IntakeCatalog
from receive.selection import select_pair
from review.catalog import PublishedReport, ReportCatalog
from review.reconstruct import reconstruct_volume
from review.report import build_report_snapshot
from review.totals import calculate_totals


def review_volume(intake: IntakeCatalog, reports: ReportCatalog, frozen_number: str,
                  first_receipt_id: str, second_receipt_id: str, *,
                  before_commit: Callable[[], None] | None = None) -> PublishedReport:
    selected = select_pair(intake, frozen_number, first_receipt_id, second_receipt_id)
    volume = reconstruct_volume(selected)
    totals = calculate_totals(volume)
    return reports.commit(build_report_snapshot(selected, volume, totals), before_commit=before_commit)
