from export.manifest import parse_manifest, validate_block
from receive.catalog import IntakeCatalog, Receipt
from receive.validation import validate_column_envelope


def receive_column(intake: IntakeCatalog, wire: bytes) -> Receipt:
    return intake.accept(validate_column_envelope(wire))


def receive_block(intake: IntakeCatalog, manifest: bytes, block: object) -> str:
    return intake.accept_band(validate_block(parse_manifest(manifest), block)).receipt_id
