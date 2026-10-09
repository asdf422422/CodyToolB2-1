"""Application services for category and transaction operations."""

from collections.abc import Iterator
import csv
from decimal import Decimal
from itertools import islice
import json
import os
from pathlib import Path
import tempfile
import uuid

from budget_app.errors import (
    ConflictError,
    NotFoundError,
    StorageError,
    ValidationError,
)
from budget_app.models import Budget, Category, ImportResult, MonthlySummary, Transaction
from budget_app.repositories import (
    BudgetRepository,
    CategoryRepository,
    TransactionRepository,
)
from budget_app.validators import (
    category_key,
    normalize_category_name,
    validate_month,
    validate_positive_int,
    validate_search_period,
    validate_transaction_type,
)


DEFAULT_CATEGORIES = (
    "FOOD",
    "TRANSPORTATION",
    "SHOPPING",
    "HOUSING",
    "ENTERTAINMENT",
    "OTHERS",
    "SALARY",
    "SIDEINCOME",
)
DEFAULT_TRANSACTION_LIMIT = 20
IMPORT_REQUIRED_FIELDS = {"date", "type", "category", "amount"}
IMPORT_ALLOWED_FIELDS = IMPORT_REQUIRED_FIELDS | {"memo", "tags"}
EXPORT_FIELDS = ("date", "type", "category", "amount", "memo", "tags")


class CategoryService:
    """Apply category policies while delegating persistence to repositories."""

    def __init__(
        self,
        categories: CategoryRepository,
        transactions: TransactionRepository,
    ) -> None:
        self._categories = categories
        self._transactions = transactions
        self._create_defaults_if_empty()

    def _create_defaults_if_empty(self) -> None:
        if next(self._categories.iter_all(), None) is None:
            self._categories.replace_all(
                Category(name=name) for name in DEFAULT_CATEGORIES
            )

    def list_categories(self) -> Iterator[Category]:
        """Stream categories in their saved order."""
        return self._categories.iter_all()

    def add_category(self, name: str) -> Category:
        """Normalize and persist a new category."""
        category = Category(name=name)
        self._categories.add(category)
        return category

    def remove_category(self, name: str) -> Category:
        """Remove an unused category; preserve it when transactions reference it."""
        key = category_key(name)
        existing = next(
            (item for item in self._categories.iter_all() if category_key(item.name) == key),
            None,
        )
        if existing is None:
            raise NotFoundError(f"등록되지 않은 카테고리입니다: {name}")

        for transaction in self._transactions.iter_all():
            if category_key(transaction.category) == key:
                raise ConflictError(
                    f"거래에서 사용 중인 카테고리는 삭제할 수 없습니다: {existing.name}"
                )
        return self._categories.remove(existing.name)


class TransactionService:
    """Validate and coordinate transaction creation, listing, and search."""

    def __init__(
        self,
        transactions: TransactionRepository,
        categories: CategoryRepository,
    ) -> None:
        self._transactions = transactions
        self._categories = categories

    def list_categories(self) -> Iterator[Category]:
        """Stream categories available for transaction entry."""
        return self._categories.iter_all()

    def get_transaction(self, transaction_id: str) -> Transaction:
        """Return a transaction by ID or raise a user-facing not-found error."""
        transaction = self._transactions.get_by_id(transaction_id)
        if transaction is None:
            raise NotFoundError(f"거래 ID를 찾을 수 없습니다: {transaction_id}")
        return transaction

    def add_transaction(
        self,
        *,
        transaction_type: str,
        transaction_date: str,
        amount: int | str,
        category: str,
        memo: str = "",
        tags: list[str] | None = None,
    ) -> Transaction:
        """Validate, assign a unique ID, and persist a transaction."""
        normalized_category = normalize_category_name(category)
        categories_by_key = {
            category_key(item.name): item.name for item in self._categories.iter_all()
        }
        canonical_category = categories_by_key.get(category_key(normalized_category))
        if canonical_category is None:
            raise ValidationError(
                f"등록되지 않은 카테고리입니다: {normalized_category}",
                "category list로 목록을 확인하거나 category add로 등록하세요.",
            )

        validated_type = validate_transaction_type(transaction_type)
        validated_amount = validate_positive_int(amount)
        for _ in range(5):
            transaction = Transaction(
                id=uuid.uuid4().hex,
                type=validated_type,
                date=transaction_date,
                amount=validated_amount,
                category=canonical_category,
                memo=memo,
                tags=list(tags or []),
            )
            try:
                self._transactions.add(transaction)
            except ConflictError:
                continue
            return transaction
        raise ConflictError("고유한 거래 ID를 만들지 못했습니다. 다시 시도하세요.")

    def update_transaction(
        self,
        transaction_id: str,
        *,
        transaction_date: str | None = None,
        transaction_type: str | None = None,
        amount: int | str | None = None,
        category: str | None = None,
        memo: str | None = None,
        tags: list[str] | None = None,
    ) -> Transaction:
        """Update specified fields and retain every unspecified field."""
        current = self._transactions.get_by_id(transaction_id)
        if current is None:
            raise NotFoundError(f"거래 ID를 찾을 수 없습니다: {transaction_id}")

        updated_category = current.category
        if category is not None:
            normalized_category = normalize_category_name(category)
            categories_by_key = {
                category_key(item.name): item.name for item in self._categories.iter_all()
            }
            updated_category = categories_by_key.get(category_key(normalized_category))
            if updated_category is None:
                raise ValidationError(
                    f"등록되지 않은 카테고리입니다: {normalized_category}",
                    "category list로 목록을 확인하거나 category add로 등록하세요.",
                )

        replacement = Transaction(
            id=current.id,
            type=(
                validate_transaction_type(transaction_type)
                if transaction_type is not None
                else current.type
            ),
            date=transaction_date if transaction_date is not None else current.date,
            amount=(
                validate_positive_int(amount) if amount is not None else current.amount
            ),
            category=updated_category,
            memo=memo if memo is not None else current.memo,
            tags=list(tags) if tags is not None else list(current.tags),
        )
        self._transactions.update(transaction_id, replacement)
        return replacement

    def delete_transaction(self, transaction_id: str) -> Transaction:
        """Delete and return a transaction, or raise if the ID does not exist."""
        return self._transactions.delete(transaction_id)

    def import_transactions(self, source: str | Path) -> ImportResult:
        """Import valid external CSV rows and atomically persist them as one batch."""
        source_path = Path(source)
        categories_by_key = {
            category_key(item.name): item.name for item in self._categories.iter_all()
        }
        existing_keys = {
            self._duplicate_key(transaction)
            for transaction in self._transactions.iter_all()
        }
        seen_keys = set(existing_keys)
        imported: list[Transaction] = []
        invalid_rows: list[tuple[int, str]] = []
        duplicate_count = 0
        reader = None

        try:
            with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, strict=True)
                headers = reader.fieldnames
                if headers is None:
                    raise ValidationError("가져올 CSV에 헤더가 없습니다.")
                if (
                    len(headers) != len(set(headers))
                    or not IMPORT_REQUIRED_FIELDS.issubset(headers)
                    or not set(headers).issubset(IMPORT_ALLOWED_FIELDS)
                ):
                    raise ValidationError(
                        "가져오기 CSV 헤더가 올바르지 않습니다.",
                        "date, type, category, amount를 포함하고 memo, tags만 선택적으로 사용할 수 있습니다.",
                    )

                for row in reader:
                    line_number = reader.line_num
                    if None in row or any(value is None for value in row.values()):
                        invalid_rows.append((line_number, "열 개수가 헤더와 일치하지 않습니다."))
                        continue
                    try:
                        category_input = normalize_category_name(row["category"])
                        category = categories_by_key.get(category_key(category_input))
                        if category is None:
                            raise ValidationError(
                                f"등록되지 않은 카테고리입니다: {category_input}"
                            )
                        transaction = Transaction(
                            id=uuid.uuid4().hex,
                            type=row["type"],
                            date=row["date"],
                            amount=row["amount"],
                            category=category,
                            memo=row.get("memo") or "",
                            tags=[
                                item.strip()
                                for item in (row.get("tags") or "").split(",")
                                if item.strip()
                            ],
                        )
                    except ValidationError as error:
                        invalid_rows.append((line_number, error.message))
                        continue

                    duplicate_key = self._duplicate_key(transaction)
                    if duplicate_key in seen_keys:
                        duplicate_count += 1
                        continue
                    seen_keys.add(duplicate_key)
                    imported.append(transaction)
        except ValidationError:
            raise
        except UnicodeError as error:
            raise ValidationError("가져오기 파일은 UTF-8 인코딩이어야 합니다.") from error
        except csv.Error as error:
            line_number = reader.line_num if reader is not None else 1
            raise ValidationError(
                f"가져오기 CSV 형식이 올바르지 않습니다 (행 {line_number})."
            ) from error
        except OSError as error:
            raise StorageError(
                f"가져오기 파일을 읽을 수 없습니다: {source_path}",
                "파일 경로와 읽기 권한을 확인하세요.",
            ) from error

        if imported:
            self._transactions.add_many(imported)
        return ImportResult(
            imported_count=len(imported),
            invalid_rows=tuple(invalid_rows),
            duplicate_count=duplicate_count,
        )

    @staticmethod
    def _duplicate_key(transaction: Transaction) -> tuple[str, int, str, str, str]:
        return (
            transaction.date,
            transaction.amount,
            category_key(transaction.category),
            transaction.type,
            transaction.memo,
        )

    def iter_export_transactions(
        self,
        *,
        month: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> Iterator[Transaction]:
        """Stream transactions matching exactly one required month or date range."""
        if month is not None:
            month = validate_month(month)
            if start is not None or end is not None:
                raise ValidationError("--month과 --from/--to는 함께 사용할 수 없습니다.")
        elif start is None and end is None:
            raise ValidationError("내보내기에는 --month 또는 --from/--to 기간이 필요합니다.")

        valid_start, valid_end = validate_search_period(start, end)

        def matching() -> Iterator[Transaction]:
            for transaction in self._transactions.iter_all():
                transaction_month = transaction.date[:7]
                if month is not None:
                    if transaction_month < month:
                        break
                    if transaction_month > month:
                        continue
                else:
                    if valid_start is not None and transaction.date < valid_start:
                        break
                    if valid_end is not None and transaction.date > valid_end:
                        continue
                yield transaction

        return matching()

    def export_transactions(
        self,
        destination: str | Path,
        *,
        month: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> int:
        """Atomically export filtered transactions using the public CSV schema."""
        transactions = self.iter_export_transactions(month=month, start=start, end=end)
        output_path = Path(destination)
        internal_paths = {
            (self._transactions.data_dir / filename).resolve()
            for filename in ("transactions.csv", "categories.csv", "budgets.csv")
        }
        try:
            if output_path.resolve() in internal_paths:
                raise ValidationError("내부 데이터 파일을 내보내기 대상으로 지정할 수 없습니다.")
        except OSError as error:
            raise StorageError(f"내보내기 경로를 확인할 수 없습니다: {output_path}") from error

        temporary_path: str | None = None
        exported_count = 0
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                dir=output_path.parent,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=EXPORT_FIELDS,
                    lineterminator="\n",
                )
                writer.writeheader()
                for transaction in transactions:
                    writer.writerow(
                        {
                            "date": transaction.date,
                            "type": transaction.type,
                            "category": transaction.category,
                            "amount": str(transaction.amount),
                            "memo": transaction.memo,
                            "tags": ",".join(transaction.tags),
                        }
                    )
                    exported_count += 1
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, output_path)
            temporary_path = None
        except (OSError, UnicodeError, csv.Error) as error:
            raise StorageError(
                f"CSV 파일을 내보내지 못했습니다: {output_path}",
                "출력 경로와 쓰기 권한을 확인하세요.",
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        return exported_count

    def list_transactions(self, limit: int | str | None = None) -> Iterator[Transaction]:
        """Stream the newest transactions up to the requested limit."""
        actual_limit = (
            DEFAULT_TRANSACTION_LIMIT
            if limit is None
            else validate_positive_int(limit)
        )
        return islice(self._transactions.iter_all(), actual_limit)

    def search_transactions(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        category: str | None = None,
        transaction_type: str | None = None,
        memo_query: str | None = None,
        tag: str | None = None,
        limit: int | str | None = None,
    ) -> Iterator[Transaction]:
        """Validate search options and lazily filter newest-first stored records."""
        valid_start, valid_end = validate_search_period(start, end)
        category_filter = category_key(category) if category is not None else None
        type_filter = (
            validate_transaction_type(transaction_type)
            if transaction_type is not None
            else None
        )
        query = memo_query.casefold() if memo_query is not None else None
        actual_limit = (
            DEFAULT_TRANSACTION_LIMIT
            if limit is None
            else validate_positive_int(limit)
        )

        def matches() -> Iterator[Transaction]:
            matched = 0
            for transaction in self._transactions.iter_all():
                if valid_start is not None and transaction.date < valid_start:
                    break
                if valid_end is not None and transaction.date > valid_end:
                    continue
                if (
                    category_filter is not None
                    and category_key(transaction.category) != category_filter
                ):
                    continue
                if type_filter is not None and transaction.type != type_filter:
                    continue
                if query is not None and query not in transaction.memo.casefold():
                    continue
                if tag is not None and tag not in transaction.tags:
                    continue
                yield transaction
                matched += 1
                if matched >= actual_limit:
                    return

        return matches()


class BudgetService:
    """Set monthly budgets and calculate monthly spending summaries."""

    def __init__(
        self,
        budgets: BudgetRepository,
        transactions: TransactionRepository,
    ) -> None:
        self._budgets = budgets
        self._transactions = transactions

    def set_budget(self, month: str, amount: int | str) -> Budget:
        budget = Budget(
            month=validate_month(month), amount=validate_positive_int(amount)
        )
        self._budgets.set_budget(budget)
        return budget

    def summarize(self, month: str) -> MonthlySummary:
        valid_month = validate_month(month)
        income_total = 0
        expense_total = 0
        expense_by_key: dict[str, tuple[str, int]] = {}
        has_transactions = False

        for transaction in self._transactions.iter_all():
            transaction_month = transaction.date[:7]
            if transaction_month < valid_month:
                break
            if transaction_month > valid_month:
                continue
            has_transactions = True
            if transaction.type == "income":
                income_total += transaction.amount
                continue
            expense_total += transaction.amount
            key = category_key(transaction.category)
            display_name, subtotal = expense_by_key.get(
                key, (transaction.category, 0)
            )
            expense_by_key[key] = (display_name, subtotal + transaction.amount)

        category_expenses = tuple(
            sorted(
                expense_by_key.values(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        )
        budget = self._budgets.get_by_month(valid_month)
        if budget is None:
            budget_amount = None
            usage_rate = None
            over_budget = None
        else:
            budget_amount = budget.amount
            usage_rate = Decimal(expense_total) * 100 / Decimal(budget.amount)
            over_budget = expense_total > budget.amount

        return MonthlySummary(
            month=valid_month,
            income_total=income_total,
            expense_total=expense_total,
            balance=income_total - expense_total,
            category_expenses=category_expenses,
            budget_amount=budget_amount,
            usage_rate=usage_rate,
            over_budget=over_budget,
            has_transactions=has_transactions,
        )

