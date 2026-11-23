def is_legacy_payload(payload: dict) -> bool:
    return payload.get('layout') == 'single-round'


def read_legacy_payload(payload: dict) -> tuple[list[dict], list[list]]:
    """Read the historical device's single executed round in its original order."""
    if not is_legacy_payload(payload) or payload.keys() != {'layout', 'records'}:
        raise ValueError('invalid single-round V1 payload')
    records = payload['records']
    if not isinstance(records, list):
        raise ValueError('legacy records must be an ordered list')
    probes = []
    values = []
    for probe_number, record in enumerate(records, start=1):
        if not isinstance(record, list) or len(record) != 2:
            raise ValueError('legacy record requires probe name and value')
        name, value = record
        probes.append(name)
        if value is not None:
            values.append([1, probe_number, value])
    return [{'executed': True, 'probes': probes}], values
