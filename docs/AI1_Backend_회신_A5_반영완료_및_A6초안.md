# AI① → Backend 회신 — A5 반영 완료 · A6 계약 초안 공유 (2026-11-10)

- 작성: AI① Learning Intelligence
- 대상: Backend의 A1~A8 후속 확인 회신
- 성격: 전송용. 키·토큰·학생 데이터 없음

## 0. 요약

| 항목 | AI① 상태 |
|---|---|
| A5 `E_NOT_EQUATION` | **코드 반영 완료.** 실행 중 서버 재시작 후 실제 적용. 배포 시점 별도 통보 |
| A2 시드 밖 코드 | **반영 완료.** `provisional_*` 현재 빈 배열 |
| A7 `skill` 의미 | **반영 완료**(스키마 설명 명시) |
| A6 계약 초안 | **작성 완료** — §3 요약 + `docs/AI1_표현식문제_계약초안_A6.md` |
| `correct` 널 허용 | 확인. Backend 변경 불필요하다는 답변대로 진행 |
| A3 후보 승인 | 기획 회신 대기 |
| A1 키 교체 | 일정 회신 대기 |
| A8 O-06 | 서버 경유 로그 공유 대기 |

---

## 1. A5 `E_NOT_EQUATION` — 반영 완료

Backend가 먼저 배포했다는 점 확인했습니다. AI①도 같은 의미로 분리했습니다.

| 코드 | HTTP | 판정 시점 | `analysis_status` | 저장 |
|---|---|---|---|---|
| `E_BAD_PROBLEM` | 422 | `parse_line(problem_latex)` 가 `None` (수식 해석 실패) | 없음 | 현행 유지(FAILED·재시도 허용) |
| `E_NOT_EQUATION` | 422 | 파싱은 됐지만 등식이 아님(`expr`) | **없음** | **하지 않음** |

- 두 경로(attempt 분석 / legacy `/analyze-solution`) 모두 적용했습니다.
- `E_NOT_EQUATION` 에는 `analysis_status` 를 붙이지 않습니다. 입력 오류를 UNKNOWN 으로
  섞지 않는다는 기존 정책과 같습니다.
- attempt 분석 경로에서 이 오류가 나면 **attempt·분석을 저장하지 않습니다**(검증 완료).
- 응답 예시:

```json
{ "detail": { "code": "E_NOT_EQUATION",
              "message": "문제식이 등식이 아닙니다. 등식 형태의 문제만 지원합니다." } }
```

**중요:** 실행 중인 서버는 재시작 전이라 아직 이전 동작(`E_BAD_PROBLEM`)입니다.
재시작 일정이 잡히면 배포 시점을 값 없이 통보하겠습니다. 그때까지는
표현식 문제도 `E_BAD_PROBLEM` 으로 오니, Backend의 새 분기가 아직 실행되지 않는 것이
정상입니다.

---

## 2. A2 시드 밖 코드 — 반영 완료

공식 Seed 범위 확인 감사합니다.

- `SKL-2-037` / `MIS-2-001` / `MIS-2-002` 를 `provisional_*` 에서 **제거**했습니다.
- `problem_skills` 역방향 수용도 **차단**했습니다. `SKL-2-037` 을 보내면
  `422 E_UNKNOWN_SKILL_CODE` 입니다(기본 Skill 로 치환하지 않음).
- 따라서 `provisional_skill_codes` / `provisional_misconception_codes` 는 **현재 `[]`** 입니다.
  승인 후보가 확정되면 그때 다시 채웁니다.
- 내부 taxonomy 의 `transposition` / `3.1` / `3.2` 관측과 Knowledge State 갱신은 그대로
  동작합니다. 제거한 것은 **Backend 로 나가는 코드**뿐입니다.
- `SKL-2-036~040` · `MIS-2-xxx` 의 Seed 편입은 기획 결정 사항으로 남깁니다.

---

## 3. A6 — 표현식 문제 계약 초안

전체 문서: `docs/AI1_표현식문제_계약초안_A6.md` (기획 공유용, 11개 절)

### 3.1 요청 필드 (additive)

| 필드 | 타입 | 기본값 | 비고 |
|---|---|---|---|
| `problem_type` | `"equation" \| "expression"` | `"equation"` | 생략 시 현행과 **완전 동일** |
| `target_form` | `"integer" \| "expanded" \| "factored" \| null` | `null` | `expression` 일 때만 의미 |

Backend 제안대로 `target_form` 은 Skill 의 `answer_form` 값을 그대로 씁니다.
`equation` + `target_form` 조합은 모순이므로 `422 E_INVALID_TARGET_FORM`(신규 제안)으로
거절합니다.

### 3.2 판정 방식 (정답 생성 없음)

1. 각 풀이 줄 ↔ **직전 줄**(첫 줄은 문제식) 동치 비교 — 식끼리는 `simplify(a-b)==0`
2. 마지막 줄이 `target_form` 꼴인지 **의미 기반** 검사
3. 정답식 불필요 — 기준은 항상 "문제식" 또는 "직전 줄"

### 3.3 형태 판정

| 값 | 판정 | 예 |
|---|---|---|
| `integer` | 자유 기호 없음 | `12` O / `2x` X |
| `expanded` | 전개 후 미전개 곱·괄호 없음 | `x^2+3x+2` O / `(x+1)(x+2)` X |
| `factored` | 상수 아닌 인수의 곱 2개 이상 | `(x+1)(x+2)` O / `x^2+3x+2` X |

"형태"는 문자열 모양이 아니라 의미로 봅니다(`x(x+2)` 와 `(x)(x+2)` 는 둘 다 `factored`).

### 3.4 항등식 오탐 분리

- `expression` 모드에서는 등식을 풀지 않으므로 **해 집합 비교 경로가 사라집니다.**
- `equation` 모드에서 문제가 항등식(잔차 0)이면 `analysis_status="REVIEW_REQUIRED"`,
  `correct=null` 로 보냅니다. 현재 `correct=true` 오탐
  (`(x+1)(x+2)=x^2+3x+1` → True)이 이 계약으로 제거됩니다.

### 3.5 기획 결정 필요 6건

| # | 질문 | AI① 제안 |
|---|---|---|
| 1 | 표현식 문제를 1차 범위에 넣는가 | 전개·인수분해부터 |
| 2 | `factored` 에 완전 인수분해를 강제하는가 | **느슨함 기본**(과잉 오판 방지) |
| 3 | 형태 불일치의 `error_type` | 전개 계열은 `concept_error`/`factoring_confusion`, 그 외 `procedure`/`method_mismatch`. taxonomy 신규 값은 만들지 않음 |
| 4 | `target_form` 을 `answer_form` 3종으로 고정하는가 | 고정 |
| 5 | APP 이 `correct: null` 을 처리하는가 | 확인 요청 |
| 6 | `integer`(계산 문제)도 대상인가 | 2차 |

### 3.6 일정

기획 우선순위 확정 후 착수합니다. 1~2단계(필드 추가·동치 판정)는 기획 결정 없이도
가능하지만, 3~4단계(형태 판정·항등식 분기)는 위 2번·5번에 의존합니다. 지금 날짜를
약속하지 않습니다.

---

## 4. A7 — `skill` 필드 의미 (반영 완료)

응답 스키마 설명을 아래로 명시했습니다.

> `skill`: 이번 시도에서 **관측된 대표 Skill**. 정답이면 문제의 대표 추적 Skill,
> 오답이면 **첫 오류가 귀속된 Skill**. 요청의 `problem_skills`(문제 선언 Skill)와
> 다를 수 있다.

`mastery` 설명에도 "관측 Skill 기준 BKT 값이며 `problem_skills`/공식 코드 매핑과 1:1
대응을 보장하지 않는다"를 추가했습니다. 값·동작은 바뀌지 않았습니다.

---

## 5. `correct` 널 허용

Backend 답변("`analysis_status=REVIEW_REQUIRED` 면 `correct` 값과 무관하게 미정 처리")
확인했습니다. AI①은 P1 단계에서 `correct` 를 `bool | null` 로 넓힐 예정이며,
그때도 `ANALYZED` 응답은 계속 `true`/`false` 만 냅니다. `null` 은 `REVIEW_REQUIRED`
계열에서만 나옵니다. 서버 변경은 필요 없다는 점 확인했습니다.

---

## 6. 남은 대기 항목

| # | 항목 | 대기 대상 |
|---|---|---|
| A1 | 키 교체 일정·담당 | Backend |
| A3 | 후보 매핑 행별 승인 | 기획 |
| A8 | 서버 경유 O-06 로그 | Backend |
| A5 | 배포(재시작) 시점 통보 | AI① 내부 일정 |

A3 승인 후 §S1-3 R1~R6 을 재실행합니다. 현재 예상:

- R1(`2(x+1)=4` + `SKL-1-004`) → 200, `official_skill_codes=["SKL-1-004"]`
- R4(`SKL-1-002`) → 승인 전까지 `422 E_UNKNOWN_SKILL_CODE` 유지(정상)
- R3(항등식) → 재시작 후에도 여전히 오탐 기록용(계약 초안의 근거)

---

## 7. AI① 후속 작업 현황

| # | 작업 | 상태 |
|---|---|---|
| 1 | A5 `E_NOT_EQUATION` + 테스트 | **완료** |
| 2 | A2 `provisional_*` 시드 밖 코드 제거 | **완료** |
| 3 | A7 `skill` 설명 명시 | **완료** |
| 4 | A6 계약 초안 | **완료** |
| 5 | A3 후보표 갱신 | 진행 예정 |
| 6 | 서버 재시작·배포 통보 | 일정 확정 후 |
| 7 | A8 로그 확인 | Backend 로그 대기 |

전체 테스트 `1717 passed`, 정적 검사 0건입니다.
