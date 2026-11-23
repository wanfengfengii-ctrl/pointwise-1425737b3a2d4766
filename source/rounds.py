from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeOccurrence:
    name: str
    reading: int | None = None
    status: str | None = None


@dataclass(frozen=True)
class Round:
    executed: bool
    probes: tuple[ProbeOccurrence, ...]


def require_label(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonblank string')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(f'{field} must be valid UTF-8 text') from error
    return value


def iter_occurrences(rounds: tuple[Round, ...]) -> Iterator[tuple[int, int, ProbeOccurrence]]:
    for round_number, round_data in enumerate(rounds, start=1):
        for probe_number, occurrence in enumerate(round_data.probes, start=1):
            yield round_number, probe_number, occurrence


def rounds_as_records(rounds: tuple[Round, ...]) -> list[dict]:
    return [
        {
            'executed': round_data.executed,
            'probes': [
                {'name': probe.name, 'reading': probe.reading, 'status': probe.status}
                for probe in round_data.probes
            ],
        }
        for round_data in rounds
    ]
