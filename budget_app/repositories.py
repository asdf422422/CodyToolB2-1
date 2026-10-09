"""CSV repositories for transactions, categories, and monthly budgets."""

import csv
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable, Iterator, Mapping, Sequence

from budget_app.errors import (
    ConflictError,
    DataFormatError,
    NotFoundError,
    StorageError,
    ValidationError,
)
from budget_app.models import Budget, Category, Transaction
from budget_app.validators import category_key


TRANSACTION_FIELDS = ("id", "type", "date", "amount", "category", "memo", "tags")
CATEGORY_FIELDS = ("name",)
BUDGET_FIELDS = ("month", "amount")


class _CsvFile:
    """Read and atomically replace one UTF-8 CSV file."""

    def __init__(self, data_dir: str | Path, filename: str, fields: Sequence[str]) -> None:
        self.path = Path(data_dir) / filename
        self.fields = tuple(fields)
        self._ensure_file()

    def _ensure_file(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("x", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(self.fields)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            # The file already exists, or another process created it concurrently.
            return
        except (OSError, UnicodeError, csv.Error) as error:
            raise StorageError(
                f"저장 파일을 준비할 수 없습니다: {self.path}",
                "저장 디렉터리와 파일 권한을 확인하세요.",
            ) from error

    def iter_rows(self) -> Iterator[tuple[int, dict[str, str]]]:
        """Yield validated CSV rows with their physical line numbers."""
        try:
            try:
                file_size = self.path.stat().st_size
            except FileNotFoundError as error:
                raise StorageError(
                    f"저장 파일이 초기화 후 사라졌습니다: {self.path}",
                    "저장소를 다시 초기화하고 파일 상태를 확인하세요.",
                ) from error
            if file_size == 0:
                return
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle, strict=True)
                if reader.fieldnames is None:
                    return
                if tuple(reader.fieldnames) != self.fields:
                    raise DataFormatError(
                        f"CSV 헤더가 올바르지 않습니다: {self.path}",
                        f"필요한 헤더: {', '.join(self.fields)}",
                    )
                for row in reader:
                    line_number = reader.line_num
                    if None in row or any(value is None for value in row.values()):
                        raise DataFormatError(
                            f"CSV {line_number}행의 열 개수가 올바르지 않습니다: {self.path}"
                        )
                    yield line_number, row
        except (DataFormatError, StorageError):
            raise
        except UnicodeError as error:
            raise DataFormatError(
                f"저장 파일 인코딩이 UTF-8이 아닙니다: {self.path}"
            ) from error
        except csv.Error as error:
            raise DataFormatError(
                f"저장 파일의 CSV 형식이 올바르지 않습니다: {self.path}"
            ) from error
        except OSError as error:
            raise StorageError(
                f"저장 파일을 읽을 수 없습니다: {self.path}",
                "파일 인코딩과 읽기 권한을 확인하세요.",
            ) from error

    def replace_rows(self, rows: Iterable[Mapping[str, str]]) -> None:
        """Write a complete replacement in the same directory, then atomically swap it."""
        temporary_path: str | None = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=self.fields,
                    extrasaction="raise",
                    lineterminator="\n",
                )
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        except (OSError, UnicodeError, csv.Error) as error:
            raise StorageError(
                f"저장 파일을 안전하게 교체하지 못했습니다: {self.path}",
                "기존 데이터는 유지됩니다. 저장 공간과 파일 권한을 확인하세요.",
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    # The original file remains intact; a stale temporary file is safe to remove later.
                    pass


def _row_number_error(path: Path, line_number: int, error: Exception) -> DataFormatError:
    message = getattr(error, "message", str(error))
    return DataFormatError(
        f"저장 파일 {line_number}행의 데이터가 올바르지 않습니다: {path}", message
    )


def _transaction_to_row(transaction: Transaction) -> dict[str, str]:
    return {
        "id": transaction.id,
        "type": transaction.type,
        "date": transaction.date,
        "amount": str(transaction.amount),
        "category": transaction.category,
        "memo": transaction.memo,
        "tags": json.dumps(transaction.tags, ensure_ascii=False, separators=(",", ":")),
    }


def _transaction_from_row(row: Mapping[str, str], path: Path, line_number: int) -> Transaction:
    try:
        tags = json.loads(row["tags"])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("태그 열은 문자열 JSON 배열이어야 합니다.")
        return Transaction(
            id=row["id"],
            type=row["type"],
            date=row["date"],
            amount=row["amount"],
            category=row["category"],
            memo=row["memo"],
            tags=tags,
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise _row_number_error(path, line_number, error) from error


def _category_to_row(category: Category) -> dict[str, str]:
    return {"name": category.name}


def _category_from_row(row: Mapping[str, str], path: Path, line_number: int) -> Category:
    try:
        return Category(name=row["name"])
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise _row_number_error(path, line_number, error) from error


def _budget_to_row(budget: Budget) -> dict[str, str]:
    return {"month": budget.month, "amount": str(budget.amount)}


def _budget_from_row(row: Mapping[str, str], path: Path, line_number: int) -> Budget:
    try:
        return Budget(month=row["month"], amount=row["amount"])
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise _row_number_error(path, line_number, error) from error


class TransactionRepository:
    """Persist transactions in newest-date-first order."""

    def __init__(self, data_dir: str | Path = Path("./data")) -> None:
        self._file = _CsvFile(data_dir, "transactions.csv", TRANSACTION_FIELDS)

    @property
    def data_dir(self) -> Path:
        return self._file.path.parent

    def iter_all(self) -> Iterator[Transaction]:
        previous_date: str | None = None
        seen_ids: set[str] = set()
        for line_number, row in self._file.iter_rows():
            transaction = _transaction_from_row(row, self._file.path, line_number)
            if transaction.id in seen_ids:
                raise DataFormatError(
                    f"저장 파일 {line_number}행에 중복 거래 ID가 있습니다: {transaction.id}"
                )
            if previous_date is not None and transaction.date > previous_date:
                raise DataFormatError(
                    f"저장 파일 {line_number}행이 최신순 저장 순서를 따르지 않습니다."
                )
            seen_ids.add(transaction.id)
            previous_date = transaction.date
            yield transaction

    def get_by_id(self, transaction_id: str) -> Transaction | None:
        for transaction in self.iter_all():
            if transaction.id == transaction_id:
                return transaction
        return None

    def add(self, transaction: Transaction) -> None:
        """Insert without changing the registration order of same-date records."""
        def ordered_rows() -> Iterator[dict[str, str]]:
            inserted = False
            for existing in self.iter_all():
                if existing.id == transaction.id:
                    raise ConflictError(f"이미 사용 중인 거래 ID입니다: {transaction.id}")
                if not inserted and existing.date < transaction.date:
                    yield _transaction_to_row(transaction)
                    inserted = True
                yield _transaction_to_row(existing)
            if not inserted:
                yield _transaction_to_row(transaction)

        self._file.replace_rows(ordered_rows())

    def add_many(self, transactions: Iterable[Transaction]) -> int:
        """Atomically append a batch while preserving newest-first and tie order."""
        incoming = list(transactions)
        if not incoming:
            return 0

        incoming_ids: set[str] = set()
        for transaction in incoming:
            if transaction.id in incoming_ids:
                raise ConflictError(f"가져오기 데이터에 중복 거래 ID가 있습니다: {transaction.id}")
            incoming_ids.add(transaction.id)

        existing_ids = {transaction.id for transaction in self.iter_all()}
        collision = incoming_ids & existing_ids
        if collision:
            raise ConflictError(f"이미 사용 중인 거래 ID입니다: {min(collision)}")

        incoming.sort(key=lambda transaction: transaction.date, reverse=True)

        def merged_rows() -> Iterator[dict[str, str]]:
            existing = iter(self.iter_all())
            current = next(existing, None)
            for transaction in incoming:
                while current is not None and current.date >= transaction.date:
                    yield _transaction_to_row(current)
                    current = next(existing, None)
                yield _transaction_to_row(transaction)
            while current is not None:
                yield _transaction_to_row(current)
                current = next(existing, None)

        self._file.replace_rows(merged_rows())
        return len(incoming)

    def update(self, transaction_id: str, replacement: Transaction) -> Transaction:
        """Atomically replace a record and retain its relative tie order."""
        if replacement.id != transaction_id:
            raise ValidationError("수정 거래의 ID는 대상 거래 ID와 같아야 합니다.")

        original_index: int | None = None
        original: Transaction | None = None
        for index, transaction in enumerate(self.iter_all()):
            if transaction.id == transaction_id:
                original_index = index
                original = transaction
                break
        if original_index is None or original is None:
            raise NotFoundError(f"거래 ID를 찾을 수 없습니다: {transaction_id}")
        target_index = original_index

        def ordered_rows() -> Iterator[dict[str, str]]:
            inserted = False
            for index, transaction in enumerate(self.iter_all()):
                if transaction.id == transaction_id:
                    continue
                belongs_before = transaction.date < replacement.date
                same_date_after_original = (
                    transaction.date == replacement.date and index > target_index
                )
                if not inserted and (belongs_before or same_date_after_original):
                    yield _transaction_to_row(replacement)
                    inserted = True
                yield _transaction_to_row(transaction)
            if not inserted:
                yield _transaction_to_row(replacement)

        self._file.replace_rows(ordered_rows())
        return original

    def delete(self, transaction_id: str) -> Transaction:
        """Atomically remove a transaction by ID."""
        removed: Transaction | None = None

        def remaining_rows() -> Iterator[dict[str, str]]:
            nonlocal removed
            for transaction in self.iter_all():
                if transaction.id == transaction_id:
                    removed = transaction
                else:
                    yield _transaction_to_row(transaction)
            if removed is None:
                raise NotFoundError(f"거래 ID를 찾을 수 없습니다: {transaction_id}")

        self._file.replace_rows(remaining_rows())
        if removed is None:
            raise NotFoundError(f"거래 ID를 찾을 수 없습니다: {transaction_id}")
        return removed

    def replace_all(self, transactions: Iterable[Transaction]) -> None:
        """Atomically replace all records; input must already be in newest-first order."""
        def rows() -> Iterator[dict[str, str]]:
            previous_date: str | None = None
            seen_ids: set[str] = set()
            for transaction in transactions:
                if transaction.id in seen_ids:
                    raise ConflictError(f"중복 거래 ID입니다: {transaction.id}")
                if previous_date is not None and transaction.date > previous_date:
                    raise ValidationError("거래는 최신 날짜 순서로 저장해야 합니다.")
                seen_ids.add(transaction.id)
                previous_date = transaction.date
                yield _transaction_to_row(transaction)

        self._file.replace_rows(rows())


class CategoryRepository:
    """Persist categories with case-insensitive uniqueness."""

    def __init__(self, data_dir: str | Path = Path("./data")) -> None:
        self._file = _CsvFile(data_dir, "categories.csv", CATEGORY_FIELDS)

    def iter_all(self) -> Iterator[Category]:
        seen: set[str] = set()
        for line_number, row in self._file.iter_rows():
            category = _category_from_row(row, self._file.path, line_number)
            key = category_key(category.name)
            if key in seen:
                raise DataFormatError(
                    f"저장 파일 {line_number}행에 중복 카테고리가 있습니다: {category.name}"
                )
            seen.add(key)
            yield category

    def add(self, category: Category) -> None:
        def rows() -> Iterator[dict[str, str]]:
            found_duplicate = False
            for existing in self.iter_all():
                if category_key(existing.name) == category_key(category.name):
                    found_duplicate = True
                yield _category_to_row(existing)
            if found_duplicate:
                raise ConflictError(f"이미 등록된 카테고리입니다: {category.name}")
            yield _category_to_row(category)

        self._file.replace_rows(rows())

    def remove(self, name: str) -> Category:
        """Remove a category by its case-insensitive normalized name."""
        target_key = category_key(name)
        removed: Category | None = None

        def rows() -> Iterator[dict[str, str]]:
            nonlocal removed
            for existing in self.iter_all():
                if category_key(existing.name) == target_key:
                    if removed is not None:
                        raise DataFormatError(
                            f"중복 카테고리를 제거할 수 없습니다: {existing.name}"
                        )
                    removed = existing
                else:
                    yield _category_to_row(existing)
            if removed is None:
                raise NotFoundError(f"등록되지 않은 카테고리입니다: {name}")

        self._file.replace_rows(rows())
        if removed is None:
            raise NotFoundError(f"등록되지 않은 카테고리입니다: {name}")
        return removed

    def replace_all(self, categories: Iterable[Category]) -> None:
        def rows() -> Iterator[dict[str, str]]:
            seen: set[str] = set()
            for category in categories:
                key = category_key(category.name)
                if key in seen:
                    raise ConflictError(f"중복 카테고리입니다: {category.name}")
                seen.add(key)
                yield _category_to_row(category)

        self._file.replace_rows(rows())


class BudgetRepository:
    """Persist one budget per month; setting an existing month replaces it."""

    def __init__(self, data_dir: str | Path = Path("./data")) -> None:
        self._file = _CsvFile(data_dir, "budgets.csv", BUDGET_FIELDS)

    def iter_all(self) -> Iterator[Budget]:
        seen_months: set[str] = set()
        for line_number, row in self._file.iter_rows():
            budget = _budget_from_row(row, self._file.path, line_number)
            if budget.month in seen_months:
                raise DataFormatError(
                    f"저장 파일 {line_number}행에 중복 월 예산이 있습니다: {budget.month}"
                )
            seen_months.add(budget.month)
            yield budget

    def get_by_month(self, month: str) -> Budget | None:
        for budget in self.iter_all():
            if budget.month == month:
                return budget
        return None

    def set_budget(self, budget: Budget) -> None:
        def rows() -> Iterator[dict[str, str]]:
            replaced = False
            for existing in self.iter_all():
                if existing.month == budget.month:
                    if not replaced:
                        yield _budget_to_row(budget)
                        replaced = True
                else:
                    yield _budget_to_row(existing)
            if not replaced:
                yield _budget_to_row(budget)

        self._file.replace_rows(rows())

    def replace_all(self, budgets: Iterable[Budget]) -> None:
        def rows() -> Iterator[dict[str, str]]:
            seen_months: set[str] = set()
            for budget in budgets:
                if budget.month in seen_months:
                    raise ConflictError(f"중복 월 예산입니다: {budget.month}")
                seen_months.add(budget.month)
                yield _budget_to_row(budget)

        self._file.replace_rows(rows())
