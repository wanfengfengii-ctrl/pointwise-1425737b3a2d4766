from dataclasses import dataclass

from receive.selection import SelectedColumns
from receive.validation import Structure, ValidatedColumn
from source.rounds import ProbeOccurrence, Round


@dataclass(frozen=True)
class ReconstructedVolume:
    frozen_number: str
    rounds: tuple[Round, ...]


def _index_values(column: ValidatedColumn, structure: Structure) -> dict[tuple[int, int], int | str]:
    values = {}
    for round_number, probe_number, value in column.values:
        if round_number > len(structure) or probe_number > len(structure[round_number - 1][1]):
            raise ValueError(f'column origin ({round_number}, {probe_number}) does not exist')
        origin = (round_number, probe_number)
        if origin in values:
            raise ValueError(f'column origin {origin} was declared twice')
        values[origin] = value
    return values


def reconstruct_volume(selected: SelectedColumns) -> ReconstructedVolume:
    structure = selected.reading.structure
    if structure != selected.status.structure:
        raise ValueError('column structure declarations disagree')
    readings = _index_values(selected.reading, structure)
    statuses = _index_values(selected.status, structure)
    rounds = []
    for round_number, (executed, names) in enumerate(structure, start=1):
        probes = []
        for probe_number, name in enumerate(names, start=1):
            origin = (round_number, probe_number)
            probes.append(ProbeOccurrence(name, readings.get(origin), statuses.get(origin)))
        rounds.append(Round(executed, tuple(probes)))
    return ReconstructedVolume(selected.frozen_number, tuple(rounds))
