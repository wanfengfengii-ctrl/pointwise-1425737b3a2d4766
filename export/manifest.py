import json
import re
from dataclasses import dataclass

from export.bands import (LABELS, ORIGINAL_SLOTS, PARITY_SLOTS, PROTECTED_GROUPS,
                          SLOT_BAND, SLOT_COLUMN, band_round_span, canonical_json, digest)
from source.observations import normalize_reading, normalize_status
from source.rounds import require_label

_DIGEST = re.compile(r'\A[0-9a-f]{64}\Z')


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f'duplicate JSON field: {name}')
        result[name] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f'unsupported JSON literal: {value}')


def strict_json(payload: object, what: str) -> object:
    if not isinstance(payload, bytes):
        raise ValueError(f'{what} must be bytes')
    try:
        text = payload.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError(f'{what} must be UTF-8 bytes') from error
    try:
        value = json.loads(text, object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise ValueError(f'invalid {what} JSON') from error
    if canonical_json(value) != payload:
        raise ValueError(f'{what} must use canonical JSON bytes')
    return value


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.match(value) is not None


def _require_length(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError('original length must be a positive integer')
    return value


@dataclass(frozen=True)
class BandManifest:
    issue_id: str
    frozen_number: str
    total_rounds: int
    cut: int
    protected: tuple[str, str]
    originals: dict[str, tuple[int, str]]
    parities: dict[str, tuple[tuple[str, ...], str]]
    body: bytes

    @property
    def frame_length(self) -> int:
        return max(length for length, _ in self.originals.values())


def parse_manifest(manifest: bytes) -> BandManifest:
    body = manifest
    data = strict_json(manifest, 'band manifest')
    if not isinstance(data, dict) or data.keys() != {
            'version', 'frozen_number', 'total_rounds', 'cut', 'protected', 'originals', 'parities'}:
        raise ValueError('invalid band manifest fields')
    if type(data['version']) is not int or data['version'] != 2:
        raise ValueError('unsupported band manifest version')
    frozen_number = require_label(data['frozen_number'], 'frozen_number')
    total_rounds = data['total_rounds']
    if type(total_rounds) is not int or total_rounds < 2:
        raise ValueError('total_rounds must be an integer of at least two')
    cut = data['cut']
    if type(cut) is not int or not 1 <= cut < total_rounds:
        raise ValueError('cut must be an integer strictly inside the frozen rounds')
    protected = data['protected']
    if (not isinstance(protected, list) or len(protected) != 2
            or not all(isinstance(label, str) for label in protected)
            or protected != sorted(protected) or protected[0] == protected[1]
            or tuple(protected) not in PROTECTED_GROUPS):
        raise ValueError('protected must name the A/D or B/C pair in ascending order')
    originals_raw = data['originals']
    if not isinstance(originals_raw, dict) or originals_raw.keys() != set(ORIGINAL_SLOTS):
        raise ValueError('manifest must describe exactly the four original blocks')
    originals = {}
    for slot in ORIGINAL_SLOTS:
        entry = originals_raw[slot]
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError(f'original {slot} requires a length and a digest')
        length, body_digest = _require_length(entry[0]), entry[1]
        if not _is_digest(body_digest):
            raise ValueError(f'original {slot} digest must be lower-case SHA-256')
        originals[slot] = (length, body_digest)
    parities_raw = data['parities']
    if not isinstance(parities_raw, dict) or parities_raw.keys() != set(PARITY_SLOTS):
        raise ValueError('manifest must describe exactly the two parity frames')
    parities = {}
    for slot in PARITY_SLOTS:
        entry = parities_raw[slot]
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError(f'parity {slot} requires a label list and a digest')
        labels, frame_digest = entry
        if (not isinstance(labels, list) or not labels
                or not all(isinstance(label, str) and label in originals for label in labels)
                or labels != sorted(set(labels))):
            raise ValueError(f'parity {slot} labels must be a non-empty ascending unique selection')
        if not _is_digest(frame_digest):
            raise ValueError(f'parity {slot} digest must be lower-case SHA-256')
        parities[slot] = (tuple(labels), frame_digest)
    return BandManifest(digest(body), frozen_number, total_rounds, cut,
                        tuple(protected), originals, parities, body)


@dataclass(frozen=True)
class ParsedOriginal:
    frozen_number: str
    cut: int
    slot: str
    structure: tuple[tuple[bool, tuple[str, ...]], ...]
    values: tuple[tuple[int, int, object], ...]


@dataclass(frozen=True)
class ValidatedBlock:
    issue_id: str
    slot: str
    payload: bytes
    original: ParsedOriginal | None


def parse_original(payload: bytes, manifest: BandManifest, slot: str) -> ParsedOriginal:
    data = strict_json(payload, f'original block {slot}')
    if not isinstance(data, dict) or data.keys() != {'frozen_number', 'cut', 'slot', 'rounds', 'values'}:
        raise ValueError(f'invalid original block {slot} fields')
    if data['frozen_number'] != manifest.frozen_number:
        raise ValueError(f'original block {slot} names a different frozen volume')
    if type(data['cut']) is not int or data['cut'] != manifest.cut:
        raise ValueError(f'original block {slot} names a different cut')
    if data['slot'] != slot:
        raise ValueError(f'original block {slot} declares another slot')
    rounds = data['rounds']
    if not isinstance(rounds, list):
        raise ValueError('original rounds must be an ordered list')
    first, last = band_round_span(SLOT_BAND[slot], manifest.cut, manifest.total_rounds)
    if len(rounds) != last - first + 1:
        raise ValueError(f'original block {slot} does not cover its band')
    structure = []
    for entry in rounds:
        if not isinstance(entry, dict) or entry.keys() != {'executed', 'probes'}:
            raise ValueError('original round requires executed and probes')
        executed, probes = entry['executed'], entry['probes']
        if type(executed) is not bool or not isinstance(probes, list):
            raise ValueError('original round requires a boolean executed flag and a probe list')
        if not executed and probes:
            raise ValueError('an unexecuted round cannot declare probes')
        structure.append((executed, tuple(require_label(name, 'probe name') for name in probes)))
    column = SLOT_COLUMN[slot]
    normalize = normalize_reading if column == 'reading' else normalize_status
    rows = data['values']
    if not isinstance(rows, list):
        raise ValueError('original values must be an ordered list')
    values = []
    previous = None
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError('original value requires a round, probe, and value')
        round_number, probe_number, value = row
        if type(round_number) is not int or not first <= round_number <= last:
            raise ValueError('original value round is outside its band')
        if type(probe_number) is not int or probe_number < 1:
            raise ValueError('original value probe is invalid')
        if probe_number > len(structure[round_number - first][1]):
            raise ValueError('original value probe does not exist in the declaration')
        value = normalize(value)
        if value is None:
            raise ValueError('original value rows must omit missing values')
        position = (round_number, probe_number)
        if previous is not None and position <= previous:
            raise ValueError('original values must be sorted and unique by position')
        previous = position
        values.append((round_number, probe_number, value))
    return ParsedOriginal(data['frozen_number'], data['cut'], slot, tuple(structure), tuple(values))


def validate_block(manifest: BandManifest, block: object) -> ValidatedBlock:
    if not isinstance(block, dict) or block.keys() != {'issue_id', 'slot', 'payload'}:
        raise ValueError('band block requires issue_id, slot, and payload')
    issue_id = require_label(block['issue_id'], 'issue_id')
    if issue_id != manifest.issue_id:
        raise ValueError('band block belongs to a different issuance')
    slot = block['slot']
    if not isinstance(slot, str) or slot not in LABELS:
        raise ValueError('band block slot must be one of A, B, C, D, P1, or P2')
    payload = block['payload']
    if not isinstance(payload, bytes):
        raise ValueError('band block payload must be bytes')
    if slot in manifest.originals:
        length, body_digest = manifest.originals[slot]
        if len(payload) != length:
            raise ValueError(f'original block {slot} length does not match the manifest')
        if digest(payload) != body_digest:
            raise ValueError(f'original block {slot} digest does not match the manifest')
        return ValidatedBlock(issue_id, slot, payload, parse_original(payload, manifest, slot))
    labels, frame_digest = manifest.parities[slot]
    if len(payload) != manifest.frame_length:
        raise ValueError(f'parity frame {slot} length does not match the manifest')
    if digest(payload) != frame_digest:
        raise ValueError(f'parity frame {slot} digest does not match the manifest')
    return ValidatedBlock(issue_id, slot, payload, None)
