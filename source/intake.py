from source.revisions import RevisionCatalog, SourceRevision
from source.rounds import require_label
from source.validation import validate_rounds


def register(catalog: RevisionCatalog, volume_id: str, revision_id: str, rounds: object) -> str:
    volume_id = require_label(volume_id, 'volume_id')
    revision_id = require_label(revision_id, 'revision_id')
    return catalog.save(SourceRevision(volume_id, revision_id, validate_rounds(rounds)))
