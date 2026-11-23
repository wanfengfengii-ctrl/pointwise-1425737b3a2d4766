from dataclasses import dataclass

from review.reconstruct import ReconstructedVolume
from source.observations import RECHECK_STATUS
from source.rounds import iter_occurrences


@dataclass(frozen=True)
class ReviewTotals:
    reading_count: int
    status_count: int
    recheck_positions: tuple[tuple[int, int], ...]


def calculate_totals(volume: ReconstructedVolume) -> ReviewTotals:
    reading_count = 0
    status_count = 0
    recheck_positions = []
    for round_number, probe_number, occurrence in iter_occurrences(volume.rounds):
        reading_count += occurrence.reading is not None
        status_count += occurrence.status is not None
        if occurrence.status == RECHECK_STATUS:
            recheck_positions.append((round_number, probe_number))
    return ReviewTotals(reading_count, status_count, tuple(recheck_positions))
