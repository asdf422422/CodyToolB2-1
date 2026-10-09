"""Command line parser and application entry function."""

import argparse
from pathlib import Path
import sys
from typing import Callable, Sequence, TypeVar

from budget_app.errors import BudgetAppError, ValidationError
from budget_app.models import Category, Transaction
from budget_app.repositories import (
    BudgetRepository,
    CategoryRepository,
    TransactionRepository,
)
from budget_app.services import BudgetService, CategoryService, TransactionService
from budget_app.utils import log_execution_time
from budget_app.validators import (
    category_key,
    validate_date,
    validate_positive_int,
    validate_transaction_type,
)

_T = TypeVar("_T")


def _read_input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError as error:
        raise ValidationError("입력이 종료되어 거래를 처리하지 못했습니다.") from error


def _prompt_validated(prompt: str, validator: Callable[[str], _T]) -> _T:
    while True:
        value = _read_input(prompt)
        try:
            return validator(value)
        except ValidationError as error:
            print(f"입력 오류: {error.message}")


def _create_transaction_service(data_dir: Path) -> TransactionService:
    categories = CategoryRepository(data_dir)
    transactions = TransactionRepository(data_dir)
    CategoryService(categories, transactions)
    return TransactionService(transactions, categories)


def _create_budget_service(data_dir: Path) -> BudgetService:
    return BudgetService(
        budgets=BudgetRepository(data_dir),
        transactions=TransactionRepository(data_dir),
    )


def _run_category_command(args: argparse.Namespace) -> int:
    service = CategoryService(
        categories=CategoryRepository(args.data_dir),
        transactions=TransactionRepository(args.data_dir),
    )
    if args.category_action == "add":
        category = service.add_category(args.name)
        print(f"카테고리를 등록했습니다: {category.name}")
    elif args.category_action == "list":
        for category in service.list_categories():
            print(category.name)
    elif args.category_action == "remove":
        category = service.remove_category(args.name)
        print(f"카테고리를 삭제했습니다: {category.name}")
    return 0


def _run_add_command(args: argparse.Namespace) -> int:
    service = _create_transaction_service(args.data_dir)
    transaction_date = _prompt_validated("날짜 (YYYY-MM-DD): ", validate_date)
    transaction_type = _prompt_validated(
        "거래 유형 (income/expense): ", validate_transaction_type
    )

    categories = list(service.list_categories())
    category_names = ", ".join(item.name for item in categories)
    while True:
        category_input = _read_input(f"카테고리 ({category_names}): ")
        try:
            candidate = Category(name=category_input)
        except ValidationError as error:
            print(f"입력 오류: {error.message}")
            continue
        category = next(
            (
                item.name
                for item in categories
                if category_key(item.name) == category_key(candidate.name)
            ),
            None,
        )
        if category is None:
            print("등록되지 않은 카테고리입니다. 표시된 목록에서 선택하세요.")
            continue
        break

    amount = _prompt_validated("금액 (양의 정수): ", validate_positive_int)
    memo = _read_input("메모 (선택, 없으면 Enter): ")
    raw_tags = _read_input("태그 (쉼표로 구분, 없으면 Enter): ")
    tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]

    transaction = service.add_transaction(
        transaction_type=transaction_type,
        transaction_date=transaction_date,
        amount=amount,
        category=category,
        memo=memo,
        tags=tags,
    )
    print(
        f"거래를 등록했습니다: {transaction.id} "
        f"{transaction.date} {transaction.type} {transaction.amount} {transaction.category}"
    )
    return 0


def _print_transaction(transaction: Transaction) -> None:
    tags = ",".join(transaction.tags)
    print(
        f"{transaction.date} | {transaction.type} | {transaction.amount} | "
        f"{transaction.category} | {transaction.memo} | {tags} | {transaction.id}"
    )


def _run_list_command(args: argparse.Namespace) -> int:
    service = _create_transaction_service(args.data_dir)
    found = False
    for transaction in service.list_transactions(args.limit):
        _print_transaction(transaction)
        found = True
    if not found:
        print("등록된 거래가 없습니다.")
    return 0


def _run_search_command(args: argparse.Namespace) -> int:
    service = _create_transaction_service(args.data_dir)
    found = False
    for transaction in service.search_transactions(
        start=args.start,
        end=args.end,
        category=args.category,
        transaction_type=args.type,
        memo_query=args.q,
        tag=args.tag,
        limit=args.limit,
    ):
        _print_transaction(transaction)
        found = True
    if not found:
        print("조건에 맞는 거래가 없습니다.")
    return 0


def _prompt_optional_update(
    label: str, current: str, validator: Callable[[str], _T]
) -> _T | None:
    while True:
        value = _read_input(f"{label} [{current}] (Enter: 유지): ")
        if value == "":
            return None
        try:
            return validator(value)
        except ValidationError as error:
            print(f"입력 오류: {error.message}")


def _run_update_command(args: argparse.Namespace) -> int:
    service = _create_transaction_service(args.data_dir)
    option_values = (
        args.date,
        args.type,
        args.category,
        args.amount,
        args.memo,
        args.tags,
    )
    if args.interactive and any(value is not None for value in option_values):
        raise ValidationError("--interactive와 필드 옵션은 함께 사용할 수 없습니다.")
    if not args.interactive and not any(value is not None for value in option_values):
        raise ValidationError("수정할 필드 옵션을 지정하거나 --interactive를 사용하세요.")

    current = service.get_transaction(args.id)
    if args.interactive:
        transaction_date = _prompt_optional_update(
            "날짜 (YYYY-MM-DD)", current.date, validate_date
        )
        transaction_type = _prompt_optional_update(
            "거래 유형 (income/expense)", current.type, validate_transaction_type
        )
        categories = list(service.list_categories())
        categories_by_key = {category_key(item.name): item.name for item in categories}

        def validate_category(value: str) -> str:
            candidate = Category(name=value)
            canonical = categories_by_key.get(category_key(candidate.name))
            if canonical is None:
                raise ValidationError("등록된 카테고리 중에서 선택하세요.")
            return canonical

        category = _prompt_optional_update(
            f"카테고리 ({', '.join(categories_by_key.values())})",
            current.category,
            validate_category,
        )
        amount = _prompt_optional_update(
            "금액 (양의 정수)", str(current.amount), validate_positive_int
        )
        memo_input = _read_input(
            f"메모 [{current.memo}] (Enter: 유지, -: 비우기): "
        )
        memo = "" if memo_input == "-" else (memo_input if memo_input else None)
        current_tags = ",".join(current.tags)
        tags_input = _read_input(
            f"태그 [{current_tags}] (쉼표 구분, Enter: 유지, -: 비우기): "
        )
        if tags_input == "-":
            tags = []
        elif tags_input:
            tags = [item.strip() for item in tags_input.split(",") if item.strip()]
        else:
            tags = None
    else:
        transaction_date = args.date
        transaction_type = args.type
        category = args.category
        amount = args.amount
        memo = args.memo
        tags = (
            [item.strip() for item in args.tags.split(",") if item.strip()]
            if args.tags is not None
            else None
        )

    updated = service.update_transaction(
        args.id,
        transaction_date=transaction_date,
        transaction_type=transaction_type,
        category=category,
        amount=amount,
        memo=memo,
        tags=tags,
    )
    print(f"거래를 수정했습니다: {updated.id}")
    return 0


def _run_delete_command(args: argparse.Namespace) -> int:
    service = _create_transaction_service(args.data_dir)
    deleted = service.delete_transaction(args.id)
    print(f"거래를 삭제했습니다: {deleted.id}")
    return 0


def _run_budget_set_command(args: argparse.Namespace) -> int:
    budget = _create_budget_service(args.data_dir).set_budget(args.month, args.amount)
    print(f"{budget.month} 예산을 {budget.amount}원으로 설정했습니다.")
    return 0


def _run_summary_command(args: argparse.Namespace) -> int:
    summary = _create_budget_service(args.data_dir).summarize(args.month)
    print(f"월별 요약: {summary.month}")
    if not summary.has_transactions:
        print("해당 월 거래 데이터가 없습니다.")
    print(f"총수입: {summary.income_total}원")
    print(f"총지출: {summary.expense_total}원")
    print(f"잔액: {summary.balance}원")

    category_expenses = summary.category_expenses
    if args.top is not None:
        top_count = validate_positive_int(args.top)
        category_expenses = category_expenses[:top_count]
    if category_expenses:
        print("카테고리별 지출:")
        for category, amount in category_expenses:
            print(f"  {category}: {amount}원")

    if summary.budget_amount is not None:
        print(f"예산: {summary.budget_amount}원")
        print(f"예산 사용률: {summary.usage_rate:.1f}%")
        if summary.over_budget:
            print("예산을 초과했습니다.")
    return 0


def _run_import_command(args: argparse.Namespace) -> int:
    result = _create_transaction_service(args.data_dir).import_transactions(args.source)
    for line_number, message in result.invalid_rows:
        print(f"CSV {line_number}행을 건너뛰었습니다: {message}", file=sys.stderr)
    print(
        f"가져온 행: {result.imported_count}, "
        f"건너뛴 행: {result.skipped_count}"
    )
    if result.duplicate_count:
        print("중복된 행이 있어 가져오지 않았습니다.")
    return 0


def _run_export_command(args: argparse.Namespace) -> int:
    count = _create_transaction_service(args.data_dir).export_transactions(
        args.output,
        month=args.month,
        start=args.start,
        end=args.end,
    )
    print(f"{count}건을 CSV로 내보냈습니다: {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the root parser and currently available command interfaces."""
    parser = argparse.ArgumentParser(
        prog="budget_app",
        description="파일 기반 콘솔 가계부",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("./data"),
        help="데이터 저장 디렉터리 (기본값: ./data)",
    )
    commands = parser.add_subparsers(dest="command")

    category_parser = commands.add_parser("category", help="카테고리 관리")
    category_actions = category_parser.add_subparsers(
        dest="category_action", required=True
    )
    category_add = category_actions.add_parser("add", help="카테고리 추가")
    category_add.add_argument("--name", required=True, help="추가할 카테고리 이름")
    category_add.set_defaults(handler=_run_category_command)

    category_list = category_actions.add_parser("list", help="카테고리 목록 조회")
    category_list.set_defaults(handler=_run_category_command)

    category_remove = category_actions.add_parser("remove", help="카테고리 삭제")
    category_remove.add_argument("--name", required=True, help="삭제할 카테고리 이름")
    category_remove.set_defaults(handler=_run_category_command)

    add_parser = commands.add_parser("add", help="거래 추가")
    add_parser.set_defaults(handler=_run_add_command)

    list_parser = commands.add_parser("list", help="거래 목록 조회")
    list_parser.add_argument("--limit", help="표시할 최대 거래 수")
    list_parser.set_defaults(handler=_run_list_command)

    search_parser = commands.add_parser("search", help="거래 검색")
    search_parser.add_argument("--from", dest="start", help="기간 시작일 (YYYY-MM-DD)")
    search_parser.add_argument("--to", dest="end", help="기간 종료일 (YYYY-MM-DD)")
    search_parser.add_argument("--category", help="카테고리")
    search_parser.add_argument("--type", help="거래 유형 (income/expense)")
    search_parser.add_argument("--q", help="메모 키워드")
    search_parser.add_argument("--tag", help="태그")
    search_parser.add_argument("--limit", help="표시할 최대 거래 수")
    search_parser.set_defaults(handler=_run_search_command)

    update_parser = commands.add_parser("update", help="거래 수정")
    update_parser.add_argument("--id", required=True, help="수정할 거래 ID")
    update_parser.add_argument(
        "--interactive", action="store_true", help="대화형으로 수정할 필드 입력"
    )
    update_parser.add_argument("--date", help="날짜 (YYYY-MM-DD)")
    update_parser.add_argument("--type", help="거래 유형 (income/expense)")
    update_parser.add_argument("--category", help="카테고리")
    update_parser.add_argument("--amount", help="양의 정수 금액")
    update_parser.add_argument("--memo", help="메모 (빈 문자열로 지우기)")
    update_parser.add_argument("--tags", help="쉼표 구분 태그 (빈 문자열로 지우기)")
    update_parser.set_defaults(handler=_run_update_command)

    delete_parser = commands.add_parser("delete", help="거래 삭제")
    delete_parser.add_argument("--id", required=True, help="삭제할 거래 ID")
    delete_parser.set_defaults(handler=_run_delete_command)

    budget_parser = commands.add_parser("budget", help="예산 관리")
    budget_actions = budget_parser.add_subparsers(dest="budget_action", required=True)
    budget_set = budget_actions.add_parser("set", help="월 예산 설정")
    budget_set.add_argument("--month", required=True, help="예산 월 (YYYY-MM)")
    budget_set.add_argument("--amount", required=True, help="예산 금액 (양의 정수)")
    budget_set.set_defaults(handler=_run_budget_set_command)

    summary_parser = commands.add_parser("summary", help="월별 수입·지출 요약")
    summary_parser.add_argument("--month", required=True, help="요약 월 (YYYY-MM)")
    summary_parser.add_argument("--top", help="표시할 지출 상위 카테고리 수")
    summary_parser.set_defaults(handler=_run_summary_command)

    import_parser = commands.add_parser("import", help="CSV 거래 가져오기")
    import_parser.add_argument("--from", dest="source", required=True, help="가져올 CSV 파일")
    import_parser.set_defaults(handler=_run_import_command)

    export_parser = commands.add_parser("export", help="거래 CSV 내보내기")
    export_parser.add_argument("--out", dest="output", required=True, help="출력 CSV 파일")
    export_parser.add_argument("--month", help="대상 월 (YYYY-MM)")
    export_parser.add_argument("--from", dest="start", help="기간 시작일 (YYYY-MM-DD)")
    export_parser.add_argument("--to", dest="end", help="기간 종료일 (YYYY-MM-DD)")
    export_parser.set_defaults(handler=_run_export_command)
    return parser


@log_execution_time
def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI scaffold and return a process exit code."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        handler = getattr(args, "handler", None)
        if handler is not None:
            return handler(args)
        parser.print_help()
        return 0
    except BudgetAppError as error:
        print(f"오류: {error.message}", file=sys.stderr)
        if error.hint:
            print(f"안내: {error.hint}", file=sys.stderr)
        return 2
    except Exception:
        print("예상하지 못한 내부 오류가 발생했습니다.", file=sys.stderr)
        return 1
