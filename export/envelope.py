import json
from dataclasses import dataclass

from export.identity import make_artifact_id


@dataclass(frozen=True)
class V1Envelope:
    frozen_number: str
    column: str
    artifact_id: str
    payload: dict
    version: int = 1


def _validate_header(envelope: V1Envelope) -> None:
    if type(envelope.version) is not int or envelope.version != 1:
        raise ValueError('unsupported envelope version')
    if envelope.artifact_id != make_artifact_id(envelope.frozen_number, envelope.column):
        raise ValueError('artifact identity does not match frozen number and column')
    if not isinstance(envelope.payload, dict):
        raise ValueError('envelope payload must be an object')


def encode_envelope(envelope: V1Envelope) -> bytes:
    _validate_header(envelope)
    data = {
        'version': envelope.version,
        'frozen_number': envelope.frozen_number,
        'column': envelope.column,
        'artifact_id': envelope.artifact_id,
        'payload': envelope.payload,
    }
    return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f'duplicate JSON field: {name}')
        result[name] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f'unsupported JSON literal: {value}')


def decode_envelope(wire: bytes) -> V1Envelope:
    if not isinstance(wire, bytes):
        raise ValueError('V1 column must be UTF-8 bytes')
    try:
        data = json.loads(wire.decode('utf-8'), object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid V1 JSON') from error
    if not isinstance(data, dict) or data.keys() != {'version', 'frozen_number', 'column', 'artifact_id', 'payload'}:
        raise ValueError('invalid V1 envelope fields')
    envelope = V1Envelope(**data)
    _validate_header(envelope)
    return envelope
