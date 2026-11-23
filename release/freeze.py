from release.catalog import FrozenCatalog
from release.content import capture_content
from source.revisions import RevisionCatalog


def freeze(revisions: RevisionCatalog, frozen: FrozenCatalog, volume_id: str, revision_id: str) -> str:
    source = revisions.get(volume_id, revision_id)
    return frozen.bind(source.volume_id, source.revision_id, capture_content(source))
