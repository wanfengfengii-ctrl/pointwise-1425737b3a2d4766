import hashlib
import json

from release.catalog import FrozenVolume

BAND_SLOTS = {1: ('A', 'B'), 2: ('C', 'D')}
SLOT_BAND = {slot: band for band, slots in BAND_SLOTS.items() for slot in slots}
SLOT_COLUMN = {'A': 'reading', 'B': 'status', 'C': 'reading', 'D': 'status'}
ORIGINAL_SLOTS = ('A', 'B', 'C', 'D')
PARITY_SLOTS = ('P1', 'P2')
LABELS = ORIGINAL_SLOTS + PARITY_SLOTS
PROTECTED_GROUPS = (('A', 'D'), ('B', 'C'))


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def normalize_cut(cut: object, total_rounds: int) -> int:
    if type(cut) is not int or not 1 <= cut < total_rounds:
        raise ValueError('cut must be an integer strictly inside the frozen rounds')
    return cut


def normalize_protected(protected: object) -> list[str]:
    if not isinstance(protected, list) or len(protected) != 2:
        raise ValueError('protected must be a two-element list')
    if not all(isinstance(label, str) for label in protected):
        raise ValueError('protected labels must be strings')
    ordered = sorted(protected)
    if tuple(ordered) not in PROTECTED_GROUPS or ordered[0] == ordered[1]:
        raise ValueError('protected must pair A with D or B with C')
    return ordered


def parity_relation(protected: list[str]) -> dict[str, list[str]]:
    """P1 carries the protected pair; P2 carries every other original block."""
    return {'P1': list(protected),
            'P2': [slot for slot in ORIGINAL_SLOTS if slot != protected[-1]]}


def band_round_span(band: int, cut: int, total_rounds: int) -> tuple[int, int]:
    return (1, cut) if band == 1 else (cut + 1, total_rounds)


def build_original(volume: FrozenVolume, cut: int, band: int, slot: str) -> bytes:
    first, last = band_round_span(band, cut, len(volume.content.rounds))
    declaration = []
    values = []
    for round_number in range(first, last + 1):
        round_data = volume.content.rounds[round_number - 1]
        declaration.append({'executed': round_data.executed,
                            'probes': [probe.name for probe in round_data.probes]})
        for probe_number, probe in enumerate(round_data.probes, start=1):
            value = probe.reading if slot in ('A', 'C') else probe.status
            if value is not None:
                values.append([round_number, probe_number, value])
    return canonical_json({
        'frozen_number': volume.frozen_number,
        'cut': cut,
        'slot': slot,
        'rounds': declaration,
        'values': values,
    })


def xor_payloads(payloads: dict[str, bytes], labels: list[str], length: int) -> bytes:
    body = bytearray(length)
    for label in labels:
        for index, byte in enumerate(payloads[label]):
            body[index] ^= byte
    return bytes(body)


def build_issuance(volume: FrozenVolume, cut: int,
                   protected: list[str]) -> tuple[bytes, dict[str, dict]]:
    payloads: dict[str, bytes] = {}
    originals: dict[str, list] = {}
    for band, slots in BAND_SLOTS.items():
        for slot in slots:
            payload = build_original(volume, cut, band, slot)
            payloads[slot] = payload
            originals[slot] = [len(payload), digest(payload)]
    frame_length = max(len(payload) for payload in payloads.values())
    parities: dict[str, list] = {}
    for slot, labels in parity_relation(protected).items():
        body = xor_payloads(payloads, labels, frame_length)
        payloads[slot] = body
        parities[slot] = [labels, digest(body)]
    manifest = canonical_json({
        'version': 2,
        'frozen_number': volume.frozen_number,
        'total_rounds': len(volume.content.rounds),
        'cut': cut,
        'protected': list(protected),
        'originals': originals,
        'parities': parities,
    })
    issue_id = digest(manifest)
    blocks = {slot: {'issue_id': issue_id, 'slot': slot, 'payload': payloads[slot]}
              for slot in LABELS}
    return manifest, blocks
