from receive.catalog import IntakeCatalog, Receipt
from receive.validation import validate_column_envelope


def receive_column(intake: IntakeCatalog, wire: bytes) -> Receipt:
    return intake.accept(validate_column_envelope(wire))
