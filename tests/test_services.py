import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from budget_app.errors import ConflictError, NotFoundError, StorageError, ValidationError
from budget_app.repositories import BudgetRepository, CategoryRepository, TransactionRepository
from budget_app.services import BudgetService, CategoryService, TransactionService


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.transactions = TransactionRepository(self.temp.name)
        self.categories = CategoryRepository(self.temp.name)
        self.category_service = CategoryService(self.categories, self.transactions)
        self.transaction_service = TransactionService(self.transactions, self.categories)

    def test_category_policy_and_transaction_stream_order(self):
        self.assertEqual(len(list(self.category_service.list_categories())), 8)
        with self.assertRaises(ConflictError):
            self.category_service.add_category(" food ")
        first = self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-02", amount=10, category="food")
        second = self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-02", amount=20, category="FOOD")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual([item.id for item in self.transaction_service.list_transactions()], [first.id, second.id])
        with self.assertRaises(ConflictError):
            self.category_service.remove_category("food")
        self.assertEqual(self.category_service.remove_category("SHOPPING").name, "SHOPPING")

    def test_update_delete_budget_and_summary(self):
        transaction = self.transaction_service.add_transaction(transaction_type="income", transaction_date="2026-10-03", amount=100, category="SALARY")
        self.transaction_service.update_transaction(transaction.id, transaction_type="expense", amount=120)
        budgets = BudgetRepository(self.temp.name)
        budget_service = BudgetService(budgets, self.transactions)
        budget_service.set_budget("2026-10", 100)
        budget_service.set_budget("2026-10", 200)
        summary = budget_service.summarize("2026-10")
        self.assertEqual(summary.expense_total, 120)
        self.assertEqual(summary.balance, -120)
        self.assertEqual(summary.budget_amount, 200)
        self.assertFalse(summary.over_budget)
        self.transaction_service.delete_transaction(transaction.id)
        self.assertEqual(list(self.transactions.iter_all()), [])

    def test_summary_totals_top_categories_and_budget_thresholds(self):
        for kind, day, amount, category in (
            ("income", "2026-10-01", 500, "SALARY"),
            ("expense", "2026-10-02", 100, "FOOD"),
            ("expense", "2026-10-03", 100, "HOUSING"),
            ("expense", "2026-10-04", 50, "FOOD"),
        ):
            self.transaction_service.add_transaction(transaction_type=kind, transaction_date=day, amount=amount, category=category)
        service = BudgetService(BudgetRepository(self.temp.name), self.transactions)
        summary = service.summarize("2026-10")
        self.assertEqual((summary.income_total, summary.expense_total, summary.balance), (500, 250, 250))
        self.assertEqual(summary.category_expenses, (("FOOD", 150), ("HOUSING", 100)))
        self.assertIsNone(summary.usage_rate)
        service.set_budget("2026-10", 250)
        self.assertEqual(service.summarize("2026-10").usage_rate, 100)
        service.set_budget("2026-10", 200)
        self.assertTrue(service.summarize("2026-10").over_budget)
        empty = service.summarize("2026-11")
        self.assertFalse(empty.has_transactions)
        self.assertEqual(empty.expense_total, 0)

    def test_search_period_and_invalid_options(self):
        self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-03", amount=10, category="FOOD")
        self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-01", amount=10, category="FOOD")
        self.assertEqual(len(list(self.transaction_service.search_transactions(start="2026-10-03", end="2026-10-03"))), 1)
        for options in (
            {"start": "2026-10-04", "end": "2026-10-01"},
            {"start": "not-a-date"},
            {"transaction_type": "transfer"},
            {"limit": 0},
        ):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                list(self.transaction_service.search_transactions(**options))

    def test_transaction_update_fields_and_missing_ids(self):
        original = self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-03", amount=10, category="FOOD", memo="old", tags=["old"])
        updated = self.transaction_service.update_transaction(original.id, transaction_date="2026-10-04", transaction_type="income", amount=20, category="SALARY", memo="new", tags=["new"])
        self.assertEqual((updated.date, updated.type, updated.amount, updated.category, updated.memo, updated.tags), ("2026-10-04", "income", 20, "SALARY", "new", ["new"]))
        before = (Path(self.temp.name) / "transactions.csv").read_bytes()
        with self.assertRaises(NotFoundError):
            self.transaction_service.update_transaction("missing", amount=1)
        with self.assertRaises(NotFoundError):
            self.transaction_service.delete_transaction("missing")
        self.assertEqual((Path(self.temp.name) / "transactions.csv").read_bytes(), before)

    def test_import_errors_duplicates_and_atomic_failure(self):
        self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-01", amount=10, category="FOOD", memo="same")
        source = Path(self.temp.name) / "rows.csv"
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "type", "category", "amount", "memo", "tags"])
            writer.writeheader()
            writer.writerow({"date": "2026-10-01", "type": "expense", "category": "FOOD", "amount": "10", "memo": "same", "tags": "different"})
            writer.writerow({"date": "2026-10-02", "type": "expense", "category": "FOOD", "amount": "11", "memo": "valid"})
            writer.writerow({"date": "bad", "type": "expense", "category": "FOOD", "amount": "10"})
        result = self.transaction_service.import_transactions(source)
        self.assertEqual((result.imported_count, result.duplicate_count), (1, 1))
        self.assertEqual(result.invalid_rows[0][0], 4)
        before = (Path(self.temp.name) / "transactions.csv").read_bytes()
        failing_source = Path(self.temp.name) / "failing.csv"
        with failing_source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "type", "category", "amount"])
            writer.writeheader()
            writer.writerow({"date": "2026-10-05", "type": "expense", "category": "FOOD", "amount": "99"})
        with patch("budget_app.repositories.os.replace", side_effect=OSError("injected failure")):
            with self.assertRaises(StorageError):
                self.transaction_service.import_transactions(failing_source)
        self.assertEqual((Path(self.temp.name) / "transactions.csv").read_bytes(), before)

        bad_header = Path(self.temp.name) / "bad.csv"
        bad_header.write_text("wrong,header\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            self.transaction_service.import_transactions(bad_header)

        malformed = Path(self.temp.name) / "malformed.csv"
        malformed.write_text('date,type,category,amount\n"unclosed,expense,FOOD,1\n', encoding="utf-8")
        with self.assertRaises(ValidationError):
            self.transaction_service.import_transactions(malformed)
        invalid_encoding = Path(self.temp.name) / "encoding.csv"
        invalid_encoding.write_bytes(b"\xff\xfe\xfa")
        with self.assertRaises(ValidationError):
            self.transaction_service.import_transactions(invalid_encoding)
        with self.assertRaises(StorageError):
            self.transaction_service.import_transactions(Path(self.temp.name) / "missing.csv")

        original_open = Path.open

        def denied(path, *args, **kwargs):
            if path == source:
                raise PermissionError("denied")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", denied):
            with self.assertRaises(StorageError):
                self.transaction_service.import_transactions(source)

    def test_import_dedup_requires_all_policy_fields_to_match(self):
        self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-01", amount=10, category="FOOD", memo="same")
        source = Path(self.temp.name) / "dedup.csv"
        rows = [
            {"date": "2026-10-01", "type": "expense", "category": "FOOD", "amount": "10", "memo": "same", "tags": "other"},
            {"date": "2026-10-02", "type": "expense", "category": "FOOD", "amount": "10", "memo": "same"},
            {"date": "2026-10-01", "type": "expense", "category": "FOOD", "amount": "11", "memo": "same"},
            {"date": "2026-10-01", "type": "expense", "category": "SHOPPING", "amount": "10", "memo": "same"},
            {"date": "2026-10-01", "type": "income", "category": "FOOD", "amount": "10", "memo": "same"},
            {"date": "2026-10-01", "type": "expense", "category": "FOOD", "amount": "10", "memo": "different"},
            {"date": "2026-10-02", "type": "expense", "category": "FOOD", "amount": "10", "memo": "same", "tags": "again"},
        ]
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "type", "category", "amount", "memo", "tags"])
            writer.writeheader()
            writer.writerows(rows)
        result = self.transaction_service.import_transactions(source)
        self.assertEqual(result.duplicate_count, 2)
        self.assertEqual(result.imported_count, 5)

    def test_import_skips_invalid_and_duplicate_then_exports_schema(self):
        source = Path(self.temp.name) / "input.csv"
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "type", "category", "amount", "memo", "tags"])
            writer.writeheader()
            writer.writerow({"date": "2026-10-01", "type": "expense", "category": "FOOD", "amount": "10", "memo": "lunch", "tags": "x"})
            writer.writerow({"date": "2026-10-01", "type": "expense", "category": "FOOD", "amount": "10", "memo": "lunch", "tags": "y"})
            writer.writerow({"date": "bad", "type": "expense", "category": "FOOD", "amount": "10"})
        result = self.transaction_service.import_transactions(source)
        self.assertEqual(result.imported_count, 1)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(len(result.invalid_rows), 1)

        output = Path(self.temp.name) / "output.csv"
        self.assertEqual(self.transaction_service.export_transactions(output, month="2026-10"), 1)
        with output.open(encoding="utf-8", newline="") as handle:
            self.assertEqual(next(csv.reader(handle)), ["date", "type", "category", "amount", "memo", "tags"])

    def test_export_requires_filter(self):
        with self.assertRaises(ValidationError):
            self.transaction_service.export_transactions(Path(self.temp.name) / "out.csv")

    def test_search_filters_and_limit_preserve_stream_order(self):
        first = self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-03", amount=10, category="FOOD", memo="Coffee shop", tags=["daily"])
        self.transaction_service.add_transaction(transaction_type="income", transaction_date="2026-10-02", amount=20, category="SALARY", memo="payday")
        self.assertEqual(
            [item.id for item in self.transaction_service.search_transactions(category="food", memo_query="COFFEE", tag="daily", limit="1")],
            [first.id],
        )

    def test_failed_atomic_replacement_preserves_original_file(self):
        transaction = self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-03", amount=10, category="FOOD")
        path = Path(self.temp.name) / "transactions.csv"
        original = path.read_bytes()
        with patch("budget_app.repositories.os.replace", side_effect=OSError("injected failure")):
            with self.assertRaises(StorageError):
                self.transaction_service.update_transaction(transaction.id, amount=12)
        self.assertEqual(path.read_bytes(), original)
        with patch("budget_app.repositories.os.replace", side_effect=OSError("injected failure")):
            with self.assertRaises(StorageError):
                self.transaction_service.delete_transaction(transaction.id)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.transactions.get_by_id(transaction.id), transaction)

    def test_export_date_range_is_inclusive_and_protects_internal_files(self):
        self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-01", amount=10, category="FOOD")
        self.transaction_service.add_transaction(transaction_type="expense", transaction_date="2026-10-02", amount=20, category="FOOD")
        output = Path(self.temp.name) / "range.csv"
        self.assertEqual(self.transaction_service.export_transactions(output, start="2026-10-01", end="2026-10-01"), 1)
        with self.assertRaises(ValidationError):
            self.transaction_service.export_transactions(Path(self.temp.name) / "transactions.csv", month="2026-10")
        with self.assertRaises(StorageError):
            self.transaction_service.export_transactions(Path(self.temp.name) / "missing" / "out.csv", month="2026-10")

        with patch("budget_app.services.tempfile.mkstemp", side_effect=PermissionError("denied")):
            with self.assertRaises(StorageError):
                self.transaction_service.export_transactions(Path(self.temp.name) / "blocked.csv", month="2026-10")

        self.transaction_service.import_transactions(output)
        self.assertEqual(len(list(self.transactions.iter_all())), 2)


if __name__ == "__main__":
    unittest.main()
