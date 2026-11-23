from dataclasses import dataclass

from export.identity import READING_COLUMN, STATUS_COLUMN
from receive.catalog import IntakeCatalog, Receipt
from receive.validation import ValidatedColumn, validate_column_envelope
from source.rounds import require_label


@dataclass(frozen=True)
class SelectedColumns:
    frozen_number: str
    reading_receipt_id: str
    status_receipt_id: str
    reading: ValidatedColumn
    status: ValidatedColumn


def available_columns(intake: IntakeCatalog, frozen_number: str) -> dict[str, Receipt]:
    return {receipt.column: receipt for receipt in intake.list_for_volume(frozen_number)}


def select_pair(intake: IntakeCatalog, frozen_number: str, first_receipt_id: str,
                second_receipt_id: str) -> SelectedColumns:
    require_label(frozen_number, 'frozen_number')
    receipts = {}
    for receipt_id in (first_receipt_id, second_receipt_id):
        try:
            receipt = intake.get(receipt_id)
        except KeyError as error:
            raise ValueError(f'column has not been received: {receipt_id}') from error
        if receipt.frozen_number != frozen_number:
            raise ValueError('receipt belongs to a different frozen volume')
        receipts[receipt.column] = receipt
    if receipts.keys() != {READING_COLUMN, STATUS_COLUMN}:
        raise ValueError('review requires both column roles')
    reading = receipts[READING_COLUMN]
    status = receipts[STATUS_COLUMN]
    return SelectedColumns(frozen_number, reading.receipt_id, status.receipt_id,
                           validate_column_envelope(reading.wire), validate_column_envelope(status.wire))
