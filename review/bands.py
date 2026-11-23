import itertools
from dataclasses import dataclass

from export.bands import BAND_SLOTS, ORIGINAL_SLOTS, PARITY_SLOTS, band_round_span, digest
from export.manifest import BandManifest, parse_manifest, parse_original, validate_block
from source.observations import RECHECK_STATUS


class RecoveryError(ValueError):
    """Selected band evidence cannot determine the requested originals coherently."""


@dataclass(frozen=True)
class RecoveredOriginal:
    payload: bytes
    witnesses: frozenset[str]


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _pad(payload: bytes, length: int) -> bytes:
    return payload + bytes(length - len(payload))


def _select_blocks(intake, manifest: BandManifest, receipt_ids: object):
    if not isinstance(receipt_ids, list):
        raise ValueError('receipt_ids must be a list of receipt numbers')
    selected = {}
    for receipt_id in receipt_ids:
        if not isinstance(receipt_id, str):
            raise ValueError('receipt_ids must hold receipt number strings')
        try:
            receipt = intake.get_band(receipt_id)
        except KeyError as error:
            raise ValueError(f'band receipt is unknown: {receipt_id}') from error
        if receipt.issue_id != manifest.issue_id:
            raise ValueError(f'band receipt belongs to another issuance: {receipt_id}')
        validated = validate_block(manifest, {'issue_id': receipt.issue_id, 'slot': receipt.slot,
                                              'payload': receipt.payload})
        selected[receipt.slot] = (receipt.receipt_id, validated.payload, validated.original)
    return selected


def _solve_relations(manifest: BandManifest, selected) -> dict[str, RecoveredOriginal]:
    """Resolve the selected parity equations; at most two frames exist per issuance."""
    frame_length = manifest.frame_length
    known = {slot: payload for slot, (_, payload, _) in selected.items() if slot in ORIGINAL_SLOTS}
    equations = []
    for slot in PARITY_SLOTS:
        if slot not in selected:
            continue
        receipt_id, payload, _ = selected[slot]
        members = set(manifest.parities[slot][0])
        value = bytearray(payload)
        witness = {receipt_id}
        for member in members & known.keys():
            value[:] = _xor(bytes(value), _pad(known[member], frame_length))
            witness ^= {selected[member][0]}
        equations.append((members - known.keys(), bytes(value), frozenset(witness)))
    combinations = []
    for size in range(1, len(equations) + 1):
        for subset in itertools.combinations(equations, size):
            mask = set()
            value = bytes(frame_length)
            witness = frozenset()
            for row_mask, row_value, row_witness in subset:
                mask ^= row_mask
                value = _xor(value, row_value)
                witness ^= row_witness
            if not mask:
                if any(value):
                    raise RecoveryError('selected parity relations contradict the received originals')
            else:
                combinations.append((mask, value, witness))
    recovered = {}
    for mask, value, witness in combinations:
        if len(mask) != 1:
            continue
        target = next(iter(mask))
        if target in recovered:
            if recovered[target].payload != value:
                raise RecoveryError(f'selected evidence determines block {target} twice with different values')
            continue
        recovered[target] = RecoveredOriginal(value, witness)
    return recovered


def _verify_recovered(manifest: BandManifest, slot: str, recovered: RecoveredOriginal):
    length = manifest.originals[slot][0]
    body = recovered.payload
    if len(body) != manifest.frame_length or any(body[length:]):
        raise RecoveryError(f'recovered block {slot} has non-zero padding')
    original = body[:length]
    if digest(original) != manifest.originals[slot][1]:
        raise RecoveryError(f'recovered block {slot} does not match its manifest digest')
    return parse_original(original, manifest, slot)


def _build_band(manifest: BandManifest, band: int, slots: tuple[str, str],
                parsed, sources: dict[str, list[str]]) -> dict:
    reading_slot, status_slot = slots
    reading, status = parsed[reading_slot], parsed[status_slot]
    if reading.structure != status.structure:
        raise RecoveryError('the band columns declare different round structures')
    reading_values = {(r, p): v for r, p, v in reading.values}
    status_values = {(r, p): v for r, p, v in status.values}
    first, last = band_round_span(band, manifest.cut, manifest.total_rounds)
    if len(reading.structure) != last - first + 1:
        raise RecoveryError('the band declaration does not cover its rounds')
    rounds = []
    reading_count = status_count = 0
    recheck_positions = []
    for offset, (executed, names) in enumerate(reading.structure):
        round_number = first + offset
        probes = []
        for probe_number, name in enumerate(names, start=1):
            origin = (round_number, probe_number)
            reading_value = reading_values.get(origin)
            status_value = status_values.get(origin)
            probes.append({'name': name, 'reading': reading_value, 'status': status_value})
            reading_count += reading_value is not None
            status_count += status_value is not None
            if status_value == RECHECK_STATUS:
                recheck_positions.append([round_number, probe_number])
        rounds.append({'round_number': round_number, 'executed': executed, 'probes': probes})
    return {
        'band': band,
        'rounds': rounds,
        'reading_count': reading_count,
        'status_count': status_count,
        'recheck_positions': recheck_positions,
        'sources': {slot: sources[slot] for slot in slots},
    }


def assemble(manifest: BandManifest, selected) -> dict:
    recovered = _solve_relations(manifest, selected)
    available = {}
    sources: dict[str, list[str]] = {}
    for slot in ORIGINAL_SLOTS:
        if slot in selected:
            receipt_id, payload, original = selected[slot]
            available[slot] = original
            sources[slot] = [receipt_id]
    for slot, block in recovered.items():
        available[slot] = _verify_recovered(manifest, slot, block)
        sources[slot] = sorted(block.witnesses)
    bands = []
    pending = []
    for band, slots in BAND_SLOTS.items():
        if all(slot in available for slot in slots):
            bands.append(_build_band(manifest, band, slots,
                                     {slot: available[slot] for slot in slots}, sources))
        else:
            pending.append(band)
    return {
        'issue_id': manifest.issue_id,
        'frozen_number': manifest.frozen_number,
        'bands': bands,
        'pending': pending,
    }


def review_bands(intake, reports, manifest: bytes, receipt_ids: object, *,
                 before_commit=None) -> dict:
    parsed_manifest = parse_manifest(manifest)
    selected = _select_blocks(intake, parsed_manifest, receipt_ids)
    snapshot = assemble(parsed_manifest, selected)
    if not snapshot['bands']:
        return {'report_id': None, 'receipt_id': None, 'snapshot': snapshot}
    published = reports.commit_band(snapshot, before_commit=before_commit)
    return {'report_id': published.report_id, 'receipt_id': published.receipt_id,
            'snapshot': snapshot}
