from dataclasses import dataclass

from export.envelope import V1Envelope, decode_envelope, encode_envelope
from export.identity import READING_COLUMN
from receive.legacy import is_legacy_payload, read_legacy_payload
from source.observations import normalize_reading, normalize_status
from source.rounds import require_label


Structure = tuple[tuple[bool, tuple[str, ...]], ...]
ValueRow = tuple[int, int, int | str]


@dataclass(frozen=True)
class ValidatedColumn:
    envelope: V1Envelope
    wire: bytes
    structure: Structure
    values: tuple[ValueRow, ...]


def _read_structure(raw: object) -> Structure:
    if not isinstance(raw, list):
        raise ValueError('round evidence must be an ordered list')
    structure = []
    for entry in raw:
        if not isinstance(entry, dict) or entry.keys() != {'executed', 'probes'}:
            raise ValueError('invalid round evidence')
        executed, probes = entry['executed'], entry['probes']
        if type(executed) is not bool or not isinstance(probes, list):
            raise ValueError('invalid execution flag or probe list')
        if not executed and probes:
            raise ValueError('unexecuted round evidence has probes')
        structure.append((executed, tuple(require_label(name, 'probe name') for name in probes)))
    return tuple(structure)


def _read_values(raw: object, column: str) -> tuple[ValueRow, ...]:
    if not isinstance(raw, list):
        raise ValueError('sparse values must be a list')
    normalize = normalize_reading if column == READING_COLUMN else normalize_status
    values = []
    for entry in raw:
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError('sparse value requires a round, probe, and value')
        round_number, probe_number, value = entry
        if type(round_number) is not int or round_number < 1:
            raise ValueError('invalid round position')
        if type(probe_number) is not int or probe_number < 1:
            raise ValueError('invalid probe position')
        value = normalize(value)
        if value is None:
            raise ValueError('sparse value rows must omit missing values')
        values.append((round_number, probe_number, value))
    return tuple(values)


def validate_column_envelope(wire: bytes) -> ValidatedColumn:
    envelope = decode_envelope(wire)
    payload = envelope.payload
    if is_legacy_payload(payload):
        rounds, values = read_legacy_payload(payload)
    else:
        if payload.get('layout') != 'sparse' or payload.keys() != {'layout', 'rounds', 'values'}:
            raise ValueError('invalid sparse V1 payload')
        rounds, values = payload['rounds'], payload['values']
    return ValidatedColumn(
        envelope,
        encode_envelope(envelope),
        _read_structure(rounds),
        _read_values(values, envelope.column),
    )
