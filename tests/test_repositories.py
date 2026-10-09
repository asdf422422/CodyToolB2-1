import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from budget_app.errors import ConflictError, DataFormatError, StorageError
from budget_app.models import Budget, Category, Transaction
from budget_app.repositories import BudgetRepository, CategoryRepository, TransactionRepository


class TransactionRepositoryTests(unittest.TestCase):
    def test_csv_round_trip_keeps_newest_first_and_registration_order(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = TransactionRepository(directory)
            repository.add(Transaction("older", "expense", "2026-09-30", 1, "FOOD"))
            memo = '한글, "quoted" memo\nsecond line'
            repository.add(Transaction("first", "expense", "2026-10-01", 2, "FOOD", memo, ["a", "b"]))
            repository.add(Transaction("second", "income", "2026-10-01", 3, "SALARY"))

            rows = list(repository.iter_all())
            self.assertEqual([item.id for item in rows], ["first", "second", "older"])
            self.assertEqual(rows[0].memo, memo)
            self.assertEqual(rows[0].tags, ["a", "b"])
            self.assertEqual([item.id for item in TransactionRepository(directory).iter_all()], ["first", "second", "older"])
            with (Path(directory) / "transactions.csv").open(encoding="utf-8", newline="") as handle:
                self.assertEqual(next(csv.reader(handle)), ["id", "type", "date", "amount", "category", "memo", "tags"])

    def test_duplicate_id_and_corrupt_storage_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = TransactionRepository(directory)
            transaction = Transaction("same", "expense", "2026-10-01", 1, "FOOD")
            repository.add(transaction)
            with self.assertRaises(ConflictError):
                repository.add(transaction)

            path = Path(directory) / "transactions.csv"
            path.write_text("id,type,date,amount,category,memo,tags\ninvalid,expense,2026-10-01,no,FOOD,,[]\n", encoding="utf-8")
            with self.assertRaises(DataFormatError):
                list(repository.iter_all())

    def test_update_reorders_dates_and_delete_preserves_remaining_records(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = TransactionRepository(directory)
            repository.add(Transaction("one", "expense", "2026-10-01", 1, "FOOD"))
            repository.add(Transaction("two", "expense", "2026-10-01", 2, "FOOD"))
            repository.update("one", Transaction("one", "expense", "2026-10-02", 1, "FOOD"))
            self.assertEqual([item.id for item in repository.iter_all()], ["one", "two"])
            repository.delete("one")
            self.assertEqual([item.id for item in repository.iter_all()], ["two"])

    def test_empty_header_only_missing_and_malformed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = TransactionRepository(directory)
            path = Path(directory) / "transactions.csv"
            self.assertEqual(list(repository.iter_all()), [])
            path.write_text("", encoding="utf-8")
            self.assertEqual(list(repository.iter_all()), [])
            path.write_text("id,type,date,amount,category,memo,tags\n", encoding="utf-8")
            self.assertEqual(list(repository.iter_all()), [])
            path.write_text("wrong,header\n", encoding="utf-8")
            with self.assertRaises(DataFormatError):
                list(repository.iter_all())
            path.write_bytes(b"\xff\xfe\xfa")
            with self.assertRaises(DataFormatError):
                list(repository.iter_all())
            path.unlink()
            with self.assertRaises(StorageError):
                list(repository.iter_all())

    def test_read_permission_failure_is_wrapped_as_storage_error(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = TransactionRepository(directory)
            original_open = Path.open

            def denied(path, *args, **kwargs):
                if path == Path(directory) / "transactions.csv":
                    raise PermissionError("denied")
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", denied):
                with self.assertRaises(StorageError):
                    list(repository.iter_all())

    def test_category_and_budget_files_persist_and_reject_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            categories = CategoryRepository(directory)
            categories.add(Category(" FOOD "))
            with self.assertRaises(ConflictError):
                categories.add(Category("food"))
            self.assertEqual([category.name for category in CategoryRepository(directory).iter_all()], ["FOOD"])
            budgets = BudgetRepository(directory)
            budgets.set_budget(Budget("2026-10", 100))
            budgets.set_budget(Budget("2026-10", 250))
            self.assertEqual(list(BudgetRepository(directory).iter_all()), [Budget("2026-10", 250)])


if __name__ == "__main__":
    unittest.main()
