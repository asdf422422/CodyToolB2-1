# 콘솔 가계부

Python 표준 라이브러리로 동작하는 파일 기반 콘솔 가계부입니다. Python 3.10 이상이 필요합니다.

## 실행

프로젝트 루트에서 다음과 같이 실행합니다.

```sh
python -m budget_app --help
```

기본 데이터 디렉터리는 `./data`입니다. 다른 디렉터리를 사용하려면 전역 옵션인 `--data-dir`을 명령보다 앞에 둡니다.

```sh
python -m budget_app --data-dir ./my-data list
```

## 프로젝트 구조

```text
budget_app/
  __main__.py   모듈 실행 진입점
  cli.py        argparse 명령과 사용자 입출력
  services.py   거래·카테고리·예산 업무 규칙
  repositories.py CSV 저장소
  models.py     데이터 모델
  validators.py 입력 검증
  errors.py     사용자 표시용 오류
  utils.py      공통 데코레이터
docs/           요구사항, 아키텍처, 결정, 테스트 문서
tests/          표준 라이브러리 unittest
data/           기본 CSV 데이터 디렉터리 (최초 실행 시 생성)
```

## 명령어

### 카테고리

처음 실행하면 카테고리 파일이 비어 있을 때 기본 카테고리가 생성됩니다: `FOOD`, `TRANSPORTATION`, `SHOPPING`, `HOUSING`, `ENTERTAINMENT`, `OTHERS`, `SALARY`, `SIDEINCOME`.

```sh
python -m budget_app category list
python -m budget_app category add --name HEALTH
python -m budget_app category remove --name HEALTH
```

이름 앞뒤 공백은 제거되며 중복 여부는 대소문자를 구분하지 않고 판단합니다. 거래에서 사용하는 카테고리는 삭제할 수 없습니다.

### 거래

`add`는 날짜, 유형, 카테고리, 금액을 대화형으로 입력받습니다. 메모와 태그는 선택 입력입니다.

```sh
python -m budget_app add
python -m budget_app list
python -m budget_app list --limit 5
python -m budget_app search --from 2026-10-01 --to 2026-10-31 --category FOOD --type expense --q lunch --tag work
```

거래 수정은 필드 옵션이나 대화형 방식으로 할 수 있습니다.

```sh
python -m budget_app update --id <거래ID> --amount 12000 --memo 점심
python -m budget_app update --id <거래ID> --interactive
python -m budget_app delete --id <거래ID>
```

목록과 검색은 최신 날짜 순서이며 기본 표시 한도는 20건입니다. 같은 날짜의 거래는 등록 순서를 따릅니다.

### 예산과 월별 요약

```sh
python -m budget_app budget set --month 2026-10 --amount 300000
python -m budget_app summary --month 2026-10
python -m budget_app summary --month 2026-10 --top 3
```

같은 월 예산을 다시 설정하면 기존 금액이 갱신됩니다. 요약에는 수입, 지출, 잔액, 지출 카테고리 합계와 설정된 예산의 사용률이 표시됩니다.

### CSV 가져오기와 내보내기

가져오기 파일은 필수 컬럼 `date`, `type`, `category`, `amount`와 선택 컬럼 `memo`, `tags`를 사용합니다. `tags`는 쉼표로 구분합니다. 유효하지 않은 행은 건너뛰고 행 번호를 알립니다. 날짜·금액·카테고리·유형·메모가 기존 거래와 모두 같은 행은 태그가 달라도 중복으로 건너뜁니다.

```sh
python -m budget_app import --from ./transactions-to-import.csv
python -m budget_app export --out ./october.csv --month 2026-10
python -m budget_app export --out ./range.csv --from 2026-10-01 --to 2026-10-31
```

내보내기에는 월(`--month`) 또는 날짜 구간(`--from`, `--to`)이 필요합니다. 가져오기와 내보내기는 UTF-8 CSV를 사용합니다.

### CSV 스키마

내부 데이터는 `--data-dir` 아래의 세 파일에 저장됩니다.

| 파일 | 헤더 순서 | 설명 |
| --- | --- | --- |
| `transactions.csv` | `id,type,date,amount,category,memo,tags` | `tags`는 JSON 문자열 배열로 저장합니다. |
| `categories.csv` | `name` | 카테고리 이름을 저장합니다. |
| `budgets.csv` | `month,amount` | 월별 예산을 저장합니다. |

가져오기·내보내기 CSV는 다음 헤더 순서를 사용합니다.

```text
date,type,category,amount,memo,tags
```

날짜 형식은 `YYYY-MM-DD`, 유형은 `income` 또는 `expense`, 금액은 양의 정수입니다. 모든 파일은 UTF-8이며 헤더를 포함합니다.

## 오류 처리

잘못된 입력, 업무 규칙 위반, 저장 파일 오류는 원인을 설명하는 메시지로 표시하고 오류 종료 코드로 끝납니다. 입력 오류에는 가능한 경우 수정 안내를 함께 출력합니다. 예상하지 못한 내부 오류는 내부 스택 트레이스 대신 일반 오류 메시지를 표시합니다. 저장 작업에 실패하면 성공으로 보고하지 않으며, 파일 갱신은 임시 파일을 통한 교체 방식으로 기존 데이터를 보호합니다.

## 검증

표준 라이브러리 테스트 러너로 테스트를 실행할 수 있습니다.

```sh
python -m unittest discover -s tests -v
```

테스트 범위와 최종 검수 항목은 [docs/testing.md](docs/testing.md)를 참고하세요. 저장 정책과 아키텍처는 [docs/decision.md](docs/decision.md), [docs/architecture.md](docs/architecture.md), 필수 기능은 [docs/requirements.md](docs/requirements.md)에 정리되어 있습니다.
