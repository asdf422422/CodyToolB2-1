import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from budget_app.cli import build_parser, main
from budget_app.repositories import CategoryRepository, TransactionRepository
from budget_app.utils import log_execution_time


class CliTests(unittest.TestCase):
    def test_execution_decorator_preserves_metadata_return_and_exception(self):
        def work(value):
            """sample function"""
            return value

        wrapped = log_execution_time(work)
        with patch("budget_app.utils.logging.getLogger") as get_logger:
            self.assertEqual(wrapped(7), 7)
            get_logger.return_value.debug.assert_called_once()
        self.assertEqual(wrapped.__name__, work.__name__)
        self.assertEqual(wrapped.__doc__, work.__doc__)

        def fail():
            raise RuntimeError("same exception")

        wrapped_fail = log_execution_time(fail)
        with self.assertRaisesRegex(RuntimeError, "same exception"):
            wrapped_fail()

    def test_every_command_has_help(self):
        parser = build_parser()
        for argv in (
            ["category", "--help"], ["category", "add", "--help"],
            ["category", "list", "--help"], ["category", "remove", "--help"],
            ["add", "--help"], ["list", "--help"], ["search", "--help"],
            ["update", "--help"], ["delete", "--help"], ["budget", "--help"],
            ["budget", "set", "--help"], ["summary", "--help"],
            ["import", "--help"], ["export", "--help"],
        ):
            with self.subTest(argv=argv), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    parser.parse_args(argv)
                self.assertEqual(raised.exception.code, 0)

    def test_help_and_expected_error_exit_codes(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("category", output.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                code = main(["--data-dir", directory, "list", "--limit", "0"])
        self.assertNotEqual(code, 0)
        self.assertIn("오류:", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_interactive_add_and_update_and_import_row_reporting(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = io.StringIO()
            with contextlib.redirect_stdout(empty):
                self.assertEqual(main(["--data-dir", directory, "list"]), 0)
                self.assertEqual(main(["--data-dir", directory, "search", "--q", "absent"]), 0)
            self.assertIn("거래가 없습니다", empty.getvalue())

            output = io.StringIO()
            with patch("builtins.input", side_effect=["invalid", "2026-10-09", "expense", "FOOD", "100", "memo", "tag"]), contextlib.redirect_stdout(output):
                self.assertEqual(main(["--data-dir", directory, "add"]), 0)
            self.assertIn("거래를 등록했습니다", output.getvalue())
            self.assertIn("입력 오류:", output.getvalue())
            transactions = TransactionRepository(directory)
            txid = next(transactions.iter_all()).id

            summary_output = io.StringIO()
            with contextlib.redirect_stdout(summary_output):
                self.assertEqual(main(["--data-dir", directory, "summary", "--month", "2026-10", "--top", "1"]), 0)
            self.assertIn("FOOD: 100원", summary_output.getvalue())

            update_output = io.StringIO()
            with patch("builtins.input", side_effect=["", "", "", "", "-", "-"]), contextlib.redirect_stdout(update_output):
                self.assertEqual(main(["--data-dir", directory, "update", "--id", txid, "--interactive"]), 0)
            updated = transactions.get_by_id(txid)
            self.assertEqual((updated.memo, updated.tags), ("", []))

            source = Path(directory) / "import.csv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "type", "category", "amount"])
                writer.writeheader()
                writer.writerow({"date": "invalid", "type": "expense", "category": "FOOD", "amount": "1"})
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--data-dir", directory, "import", "--from", str(source)]), 0)
            self.assertIn("CSV 2행", errors.getvalue())

            duplicate = Path(directory) / "duplicate.csv"
            with duplicate.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "type", "category", "amount", "memo", "tags"])
                writer.writeheader()
                writer.writerow({"date": "2026-10-09", "type": "expense", "category": "FOOD", "amount": "100", "memo": "", "tags": ""})
            duplicate_output = io.StringIO()
            with contextlib.redirect_stdout(duplicate_output):
                self.assertEqual(main(["--data-dir", directory, "import", "--from", str(duplicate)]), 0)
            self.assertIn("중복된 행", duplicate_output.getvalue())

    def test_argparse_missing_arguments_and_unknown_options_exit_nonzero(self):
        parser = build_parser()
        for argv in (["budget", "set"], ["export", "--out", "out.csv", "--bad"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    parser.parse_args(argv)
                self.assertNotEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
