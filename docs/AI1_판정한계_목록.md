# AI① 판정 한계 목록 — `review_reasons` 코드 정의

> 기준: 2026-11-23 · 브랜치 `main` · 코드 커밋 `3eebc1d` + 분기 답안 보완(미커밋)
> 대상: `POST /api/v1/attempts/{attempt_id}/analyze` 응답의 `review_reasons`

## 1. 계약 불변식

1. `analysis_status == "ANALYZED"` 이면 `review_reasons`는 **항상 `[]`** 다.
   (ANALYZED + 비어 있지 않은 배열은 계약 위반)
2. `review_reasons`가 비어 있지 않으면 `analysis_status == "REVIEW_REQUIRED"` 다.
3. `REVIEW_REQUIRED`인데 `review_reasons`가 `[]`인 경우가 있다.
   예: 일부 줄 인식 실패(`unrecognized`)로 보류된 경우.
   → 1차 판단은 `analysis_status`로 하고, 코드는 사유 기록용으로 쓴다.
4. 필드가 없는 구버전 응답은 `[]`로 본다.
5. 코드는 학생 화면에 노출하지 않는다. 로그·평가 기록용이다.
6. 모르는 코드가 오면 보류(REVIEW_REQUIRED)로 처리한다. AI①은 아래 문서에 있는
   코드만 내보낸다.

## 2. 코드 목록

| 코드 | 뜻 | 발생 조건 | 응답 상태 |
|---|---|---|---|
| `solution_block_notation` | 해를 여러 줄로 나눠 쓴 답 블록 | 단일 변수 등식 + 답 블록 값 집합 == 문제 해집합 + 검출된 오류가 모두 답 블록 안 | REVIEW_REQUIRED, `correct=null`, `final_answer_present=null`, 관측 없음 |
| `function_notation_in_solution` | 풀이에 함수 표기(`f(x)`) 포함 | 풀이 단계에 `f(...)` 형태가 있어 P1 범위 밖 | REVIEW_REQUIRED |
| `equation_line_in_expression_mode` | expression 문제에 등식 줄이 섞임 | `problem_type="expression"`인데 풀이 줄이 `=`를 포함 | REVIEW_REQUIRED |
| `expression_problem_not_expression` | expression 모드인데 문제식이 식이 아님 | `problem_type="expression"` + 문제식이 등식이거나 식이 아님 | REVIEW_REQUIRED |

> 명칭 주의: 2026-11-23 이전 메신저 회신에서 `expression_eq_line_in_expression_mode`로
> 적힌 적이 있다. **실제 코드 값은 `equation_line_in_expression_mode`** 다.

## 3. 코드별 예시 (실제 로컬 실행 결과)

### 3.1 `solution_block_notation`

요청
```json
{
  "problem_id": "P-1",
  "problem_latex": "x^2=9",
  "problem_skills": ["quadratic_equation"],
  "student_id": "S1",
  "solution_text": "x^2=9\nx=3\nx=-3"
}
```

응답 발췌
```json
{
  "analysis_status": "REVIEW_REQUIRED",
  "correct": null,
  "error_step": null,
  "final_answer_present": null,
  "review_reasons": ["solution_block_notation"],
  "valid": [true, false, false],
  "unrecognized": [],
  "observations": [],
  "states": {},
  "mastery_by_skill": {}
}
```

붙지 않는 경우
- `x^2=9` → `x=3` (일부 해만 제시): ANALYZED, `final_answer_present=false`
- `x^2=9` → `x=4` / `x=-4` (값 불일치): ANALYZED, `final_answer_present=true`
- `x^2=9` → `2x=6` / `x=3` / `x=-3` (블록 밖 오류): ANALYZED
- `x^2=9` → `x=3` / `x=-3` / `x=1` (값 집합 불일치): ANALYZED

### 3.2 `function_notation_in_solution`

요청: 문제 `x=2`, 풀이 `x=2` / `f(2)=4`
→ REVIEW_REQUIRED, `review_reasons=["function_notation_in_solution"]`

### 3.3 `equation_line_in_expression_mode`

요청: `problem_type="expression"`, 문제 `(x+1)(x+2)`, 풀이 `(x+1)(x+2)=x^2+3x+2`
→ REVIEW_REQUIRED, `review_reasons=["equation_line_in_expression_mode"]`

### 3.4 `expression_problem_not_expression`

요청: `problem_type="expression"`, 문제 `2x=6`, 풀이 `2x=6`
→ REVIEW_REQUIRED, `review_reasons=["expression_problem_not_expression"]`

## 4. 코드로 표현하지 않는 한계

아래는 보류 코드 없이 `null`/기존 규칙으로 처리한다.

| 한계 | 처리 |
|---|---|
| 다변수 등식 | `final_answer_present=null`, 동치 비교는 보수적으로 `None` |
| 문제식 자유변수 0개 | `final_answer_present=null` |
| `x^2=16 → x=4` (해 누락) | 오류로 판정 (`correct=false`). 교육적 판단은 별도 |
| 파서 범위 | 단일 문자 변수·`sqrt(...)`만. `sin`/`cos`/`log`, `\sqrt{}` 미지원 |
| `3.2` 탐지 범위 | 항 누락·일부 비대칭 연산만. 일방 곱셈·나눗셈 전체 아님 |
| 계산 오류의 오개념 | 설계상 MIS 코드 없음(ADR-02) |
| OCR raw_text | 문제/풀이 분리 없음, 구조화 OCR 미구현 |
| 저장 | in-memory. 재시작 시 이력 소실 |
| BKT | 파라미터 미피팅 |

## 5. AI② 사용 규칙 (합의)

1. `analysis_status`를 1차 기준으로 본다.
2. `review_reasons`가 비어 있지 않으면 코드 종류와 무관하게 보류한다.
3. ANALYZED + 비어 있지 않은 배열은 계약 위반으로 보고 보류한다.
4. 받은 코드는 `ai1_review_reasons`로 기록하고 학생 화면에는 노출하지 않는다.
5. 필드 누락은 `[]`로 처리한다.

## 6. 변경 이력

| 날짜 | 변경 |
|---|---|
| 2026-11-23 | `review_reasons` 필드 추가, `solution_block_notation` 첫 코드 |
| 2026-11-23 | 기존 A6 사유 3건(`function_notation_in_solution`, `equation_line_in_expression_mode`, `expression_problem_not_expression`) 응답 노출 |

새 코드를 추가할 때는 이 표와 예시를 함께 갱신하고, AI②에 코드/뜻/발생 조건을
전달한다.
