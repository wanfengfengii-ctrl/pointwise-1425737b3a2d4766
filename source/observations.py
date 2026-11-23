RECHECK_STATUS = '复检'
QC_STATUSES = frozenset({'合格', RECHECK_STATUS})


def normalize_reading(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError('reading must be an integer in millivolts or null')
    return value


def normalize_status(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in QC_STATUSES:
        raise ValueError('status must be 合格, 复检, or null')
    return value
