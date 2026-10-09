"""Validation helpers shared by models, services, and the CLI."""

from datetime import date
import re

from budget_app.errors import ValidationError

_INTEGER_PATTERN = re.compile(r"^[0-9]+$")


def validate_date(value: str) -> str:
    """Return a valid YYYY-MM-DD date string."""
    if not isinstance(value, str):
        raise ValidationError("날짜는 YYYY-MM-DD 형식의 문자열이어야 합니다.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValidationError(
            "날짜는 실제 달력에 존재하는 YYYY-MM-DD 형식이어야 합니다."
        ) from error
    if parsed.isoformat() != value:
        raise ValidationError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    return value


def validate_month(value: str) -> str:
    """Return a valid YYYY-MM month string."""
    if not isinstance(value, str):
        raise ValidationError("월은 YYYY-MM 형식의 문자열이어야 합니다.")
    match = re.fullmatch(r"([0-9]{4})-([0-9]{2})", value)
    if match is None:
        raise ValidationError("월은 YYYY-MM 형식이어야 합니다.")
    year, month = int(match.group(1)), int(match.group(2))
    try:
        parsed = date(year, month, 1)
    except ValueError as error:
        raise ValidationError("월은 유효한 YYYY-MM 형식이어야 합니다.") from error
    if parsed.year != year or parsed.month != month:
        raise ValidationError("월은 유효한 YYYY-MM 형식이어야 합니다.")
    return value


def validate_transaction_type(value: str) -> str:
    """Accept only the two supported transaction types."""
    if not isinstance(value, str) or value not in {"income", "expense"}:
        raise ValidationError("거래 유형은 income 또는 expense여야 합니다.")
    return value


def validate_positive_int(value: int | str) -> int:
    """Parse and return a positive integer, rejecting booleans and fractions."""
    if isinstance(value, bool):
        raise ValidationError("금액은 양의 정수여야 합니다.")
    if isinstance(value, int):
        amount = value
    elif isinstance(value, str):
        candidate = value.strip()
        if not _INTEGER_PATTERN.fullmatch(candidate):
            raise ValidationError("금액은 양의 정수여야 합니다.")
        amount = int(candidate)
    else:
        raise ValidationError("금액은 양의 정수여야 합니다.")
    if amount <= 0:
        raise ValidationError("금액은 0보다 커야 합니다.")
    return amount


def normalize_category_name(value: str) -> str:
    """Trim a category name and reject an empty result."""
    if not isinstance(value, str):
        raise ValidationError("카테고리 이름은 문자열이어야 합니다.")
    normalized = value.strip()
    if not normalized:
        raise ValidationError("카테고리 이름은 비워 둘 수 없습니다.")
    return normalized


def category_key(value: str) -> str:
    """Return the case-insensitive key used to compare category names."""
    return normalize_category_name(value).casefold()


def validate_search_period(
    start: str | None, end: str | None
) -> tuple[str | None, str | None]:
    """Validate optional date bounds and ensure the period is not reversed."""
    valid_start = validate_date(start) if start is not None else None
    valid_end = validate_date(end) if end is not None else None
    if valid_start is not None and valid_end is not None and valid_start > valid_end:
        raise ValidationError("기간 시작일은 종료일보다 늦을 수 없습니다.")
    return valid_start, valid_end
