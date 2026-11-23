from export.catalog import ArtifactCatalog
from export.envelope import V1Envelope, encode_envelope
from export.identity import READING_COLUMN, make_artifact_id, validate_column
from export.structure import build_structure_evidence
from release.catalog import FrozenCatalog, FrozenVolume
from source.rounds import iter_occurrences


def project_column(volume: FrozenVolume, column: str) -> V1Envelope:
    artifact_id = make_artifact_id(volume.frozen_number, column)
    values = []
    for round_number, probe_number, occurrence in iter_occurrences(volume.content.rounds):
        column_value = occurrence.reading if column == READING_COLUMN else occurrence.status
        if column_value is not None:
            values.append([round_number, probe_number, column_value])
    payload = {
        'layout': 'sparse',
        'rounds': build_structure_evidence(volume.content),
        'values': values,
    }
    return V1Envelope(volume.frozen_number, column, artifact_id, payload)


def export_column(frozen: FrozenCatalog, artifacts: ArtifactCatalog, frozen_number: str, column: str) -> bytes:
    validate_column(column)
    issued = artifacts.get(frozen_number, column)
    if issued is not None:
        return issued
    envelope = project_column(frozen.get(frozen_number), column)
    return artifacts.save_or_get(encode_envelope(envelope))
