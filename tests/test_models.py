import unittest

from budget_app.errors import ValidationError
from budget_app.models import Budget, Category, Transaction


class ModelTests(unittest.TestCase):
    def test_transaction_normalizes_category_and_validates_fields(self):
        transaction = Transaction("id-1", "expense", "2026-10-01", "12", " FOOD ")
        self.assertEqual(transaction.category, "FOOD")
        self.assertEqual(transaction.amount, 12)
        self.assertEqual(transaction.tags, [])

        with self.assertRaises(ValidationError):
            Transaction("id-2", "expense", "2026-02-30", 1, "FOOD")
        for invalid in (
            ("id-3", "transfer", "2026-10-01", 1, "FOOD"),
            ("id-4", "expense", "2026-10-01", 0, "FOOD"),
            ("id-5", "expense", "2026-10-01", "1.5", "FOOD"),
        ):
            with self.subTest(transaction=invalid), self.assertRaises(ValidationError):
                Transaction(*invalid)
        with self.assertRaises(ValidationError):
            Transaction("id-6", "expense", "2026-10-01", 1, "FOOD", tags="tag")

    def test_category_and_budget_validation(self):
        self.assertEqual(Category("  food ").name, "food")
        with self.assertRaises(ValidationError):
            Budget("2026-13", 100)
        with self.assertRaises(ValidationError):
            Budget("2026-10", 0)


if __name__ == "__main__":
    unittest.main()
