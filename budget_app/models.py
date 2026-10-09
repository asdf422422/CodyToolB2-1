"""Typed data models and their local invariants."""

from dataclasses import dataclass, field
from decimal import Decimal

from budget_app.errors import ValidationError
from budget_app.validators import (
    normalize_category_name,
    validate_date,
    validate_month,
    validate_positive_int,
    validate_transaction_type,
)


@dataclass
class Transaction:
    """A single income or expense record."""

    id: str
    type: str
    date: str
    amount: int
    category: str
    memo: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValidationError("거래 ID는 비워 둘 수 없습니다.")
        self.id = self.id.strip()
        self.type = validate_transaction_type(self.type)
        self.date = validate_date(self.date)
        self.amount = validate_positive_int(self.amount)
        self.category = normalize_category_name(self.category)
        if not isinstance(self.memo, str):
            raise ValidationError("메모는 문자열이어야 합니다.")
        if not isinstance(self.tags, list) or any(
            not isinstance(tag, str) for tag in self.tags
        ):
            raise ValidationError("태그는 문자열 목록이어야 합니다.")


@dataclass
class Category:
    """A normalized category name."""

    name: str

    def __post_init__(self) -> None:
        self.name = normalize_category_name(self.name)


@dataclass
class Budget:
    """A positive budget amount associated with a calendar month."""

    month: str
    amount: int

    def __post_init__(self) -> None:
        self.month = validate_month(self.month)
        self.amount = validate_positive_int(self.amount)


@dataclass(frozen=True)
class MonthlySummary:
    """Calculated totals and optional budget status for one month."""

    month: str
    income_total: int
    expense_total: int
    balance: int
    category_expenses: tuple[tuple[str, int], ...]
    budget_amount: int | None
    usage_rate: Decimal | None
    over_budget: bool | None
    has_transactions: bool


@dataclass(frozen=True)
class ImportResult:
    """Counts and row-level validation errors from one transaction import."""

    imported_count: int
    invalid_rows: tuple[tuple[int, str], ...]
    duplicate_count: int

    @property
    def skipped_count(self) -> int:
        return len(self.invalid_rows) + self.duplicate_count
