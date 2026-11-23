from source.rounds import require_label


READING_COLUMN = 'reading'
STATUS_COLUMN = 'status'


def validate_column(column: object) -> str:
    if not isinstance(column, str) or column not in (READING_COLUMN, STATUS_COLUMN):
        raise ValueError('column must be reading or status')
    return column


def make_artifact_id(frozen_number: str, column: str) -> str:
    require_label(frozen_number, 'frozen_number')
    return f'{frozen_number}/{validate_column(column)}'
