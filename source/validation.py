from source.observations import normalize_reading, normalize_status
from source.rounds import ProbeOccurrence, Round, require_label


def _validate_probe(data: object) -> ProbeOccurrence:
    if not isinstance(data, dict) or 'name' not in data or data.keys() - {'name', 'reading', 'status'}:
        raise ValueError('probe requires name and optional reading and status')
    return ProbeOccurrence(
        require_label(data['name'], 'probe name'),
        normalize_reading(data.get('reading')),
        normalize_status(data.get('status')),
    )


def _validate_round(data: object) -> Round:
    if not isinstance(data, dict) or data.keys() != {'executed', 'probes'}:
        raise ValueError('round requires executed and probes')
    executed, probes = data['executed'], data['probes']
    if type(executed) is not bool or not isinstance(probes, list):
        raise ValueError('round requires a boolean executed flag and a probe list')
    if not executed and probes:
        raise ValueError('an unexecuted round cannot have probes')
    return Round(executed, tuple(_validate_probe(probe) for probe in probes))


def validate_rounds(data: object) -> tuple[Round, ...]:
    if not isinstance(data, list):
        raise ValueError('rounds must be an ordered list')
    return tuple(_validate_round(round_data) for round_data in data)
