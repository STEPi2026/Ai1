# STEPI AI① Learning Intelligence 기술 구상서 (v2 — 피드백 반영)
> 기반: `P5_적응형학습튜터_프로젝트기획서.docx` + STEPI 발표 PDF (2026.09.22, 2조) + AI① 피드백 (Knowledge/BKT·수식OCR·평가우선)
> 작성일: 2026-09-22 / 대상: AI① 파트
> 한 줄 정의: **풀이 과정 → 최초 오류 → 오개념 → Skill별 Knowledge State**를 구조화 JSON으로 출력하는 분석 AI. 힌트 생성은 AI②로 위임 (ADR-01).

---

## 0. 이번 개정 핵심 (피드백 반영 요약)

1. **Knowledge State 단순 가감(+0.05/-0.05) 폐기** → `MVP 규칙 기반 → BKT 직접구현(PyTorch) → IRT 난이도 보정` 로드맵으로 고도화. DKT는 선택(필수 아님).
2. **일반 OCR과 수식 인식 분리** → `손글씨 사진 → 수식 인식 → LaTeX → 풀이 단계 구조화 → 오류 단계 특정 → Skill 분석` 고정. SymPy는 계산용이 아니라 **LLM 분석 결과 검증자(Verifier)**.
3. **평가를 개발 초기부터** → `20~30개 → MVP → 50개 → 100+ Golden → 자동 회귀(CI)` 테스트가 개발 기준점.
4. **5단계 점진 구축**: 텍스트MVP → SymPy검증 → 수식OCR → BKT고도화 → Golden/CI.
5. **오늘 착수안 확정**: 일차방정식 1단원, Skill 4개, 문제·풀이 20쌍, 입출력 JSON, `POST /analyze-solution`부터.

---

## 1. AI① 역할과 범위

### 1.1 기능 매핑 (P5 F-02~F-06, STEPI REQ-01/03/04)
| ID | 기능 | AI① 책임 | 출력 |
|---|---|---|---|
| F-03 | 풀이 사진 인식 | **수식 OCR → LaTeX** (일반 OCR과 분리) | `steps_latex[], ocr_confidence` |
| F-04 | 단계별 검증 | LLM 단계분할 + **SymPy 동치/역풀이 검증** | `valid[], first_error_index` |
| F-05 | 오류 단계 특정 | 첫 틀린 줄 지목 | `error_step` |
| F-06 | 오개념 분류 | 트리 노드 분류 (30~50개, 자유생성 금지 ADR-02) | `error_type, misconception_id` |
| F-02 | 지식 상태 추정 | Rule → **BKT(PyTorch 직접구현)** → IRT 연계 | `mastery, state` |
| F-09 연계 | AI② 전달 | 취약/학습가능/숙달 구분 | Student State JSON |

### 1.2 하지 않는 것
- ADR-01: 정답·힌트 문장 생성 안 함. ADR-03: 인식실패를 오답 처리 안 함(`unrecognized` 분리). ADR-04: 검증 미통과 문항 등록 안 함.

---

## 2. 최종 파이프라인 (확정)

```
손글씨 사진
 ↓
[수식 인식: Handwritten Math OCR → LaTeX]  ← 일반 OCR과 구분
 ↓
[풀이 단계 구조화: LLM이 줄 단위 LaTeX 분할]
 ↓
[오류 단계 특정: SymPy가 각 변환 검증 → 최초 오류 확정]  ← LLM 환각 차단
 ↓
[Skill 분석: 오류→Skill→오개념트리 매핑]
 ↓
[Knowledge State 갱신: Rule → BKT → IRT]
 ↓
Backend/AI② 전달 (FastAPI + MySQL)
```

### 2.1 LLM → SymPy 검증 패턴 (핵심)

LLM 단독 판단에 맡기면 환각 발생. 반드시 **LLM 제안 → SymPy 검증 → 최종 판단** 구조:

```
LLM: "2x + 5 = 13 → 2x = 8"  (error_step=2, concept_error 추측)
        ↓
SymPy: simplify((2*x+5)-(2*x+8)) = -3 ≠ 0 → not equivalent
       + 역풀이: x=4 대입 시 원식 성립, LLM의 2x=8은 오변환 확인
        ↓
최종: error_step=2 확정, error_type=concept_error(transposition), skill=transposition
```

구현 규칙:
- `is_equivalent(a,b) = simplify(a-b)==0`, 허용 변환(이항·분배)은 사전 검사.
- SymPy 파싱 실패 시 LLM 결과 채택 금지, `E_SYMPY_FAIL(판정불가)` 반환. 추측 금지 (기획서 5.3).
- LLM은 `Structured Output(JSON Schema)`로만 출력, 자유 서술 금지.

### 2.2 수식 OCR 스택 (일반 OCR과 구분)
| 층 | 선정 | 비고 |
|---|---|---|
| 전처리 | OpenCV(기울기보정·이진화) + CnSTD(줄 검출) | R1 대응 |
| 수식 인식 1순위 | GPT-4o-mini Vision (이미지+문제LaTeX → 줄단위 LaTeX) | "모르면 ? 표기, 추측금지" |
| 수식 인식 2순위(로컬/무료) | Texify2 / Pix2Tex + TrOCR(handwritten) / GOT-OCR2.0 / Qwen2.5-VL | 비용 0원 폴백, 베이스라인 비교 |
| 정규화 | sympy.parsing.latex.parse_latex | LaTeX→SymPy expr |
| 유료 옵션 | Mathpix | 정확도 최상, 예산 있을 때만 평가용 |

---

## 3. Knowledge State 고도화 로드맵 (단순 가감 폐기)

> 기존 `정답+0.05 / 오답-0.05 / 개념오류-0.10`은 MVP 데모용으로만 허용, 최종본 불가.

```
MVP: 규칙 기반 Skill State
        ↓
고도화: BKT (PyTorch 직접 구현) ← 최소 최종 목표
        ↓
가능하면: IRT 난이도 보정 (2-모수, py-irt or 직접구현)
DKT는 선택 (BKT를 제대로 구현·검증하는 것만으로 충분)
```

### 3.1 Stage 0 — MVP 규칙 기반 (1~2주, 텍스트 입력용)
- 목적: 파이프라인 개통 확인용.
- 규칙 예: `mastered / learning / needs_practice` 3상태. 연속 2정답 → 상향, 개념오류 → 해당 Skill `needs_practice` 고정 + `misconception_hit` 기록.
- 한계 명시: 난이도·추측/실수 미고려 → BKT로 교체 예정.

### 3.2 Stage 1 — BKT 직접 구현 (최종 필수, PyTorch)
- 수업계획서 P5 명시 요건 충족.
- 파라미터 Skill별 독립: `p_init, p_learn, p_slip, p_guess`.
  - 초기값: `p_init=진단기반(맞으면 0.6/틀리면 0.3), p_learn=0.15, p_slip=0.1, p_guess=0.2` → Golden 데이터로 EM fitting 후 튜닝.
- 갱신식 (정답시): `p = p*(1-slip)/(p*(1-slip)+(1-p)*guess)` / (오답시): `p = p*slip/(p*slip+(1-p)*(1-guess))` / 전이: `p = p+(1-p)*learn`.
- PyTorch `nn.Module`로 BKT 셀 정의, `mastery(student_id, concept_id, p_known)` 저장. `pyBKT`는 참고만.
- 출력: `mastery 0.0~1.0` + `>0.7 mastered / 0.4~0.7 learning / <0.4 needs_practice` → AI② 전달.
- 검증: Golden 100+건 AUC로 규칙기반 대비 향상 입증.

### 3.3 Stage 2 — IRT 난이도 보정 (가능하면)
- 2-모수: `P(θ)=1/(1+exp(-a(θ-b)))`, θ는 BKT p_known에서 초기화.
- 실제 정답률 30응답 누적 → `a,b` 재추정(`py-irt` or 직접 MLE) → 태그 수정 (기획서 5.5).
- AI① 역할: 추정 자체가 아니라 **BKT θ + 정오 로그를 문항 파트에 공급**.
- DKT(LSTM): 로그 1000+ 없으면 과적합 → 보류. BKT 완성 후 여력 되면 스파이크만.

---

## 4. 평가 우선 개발 (Test-First, 공통 필수 요건)

> 수업 요건: **100건+ Golden + 자동 회귀 평가**. "나중에 테스트" 금지.

```
20~30개 테스트 데이터 (금주)
        ↓
AI① MVP (텍스트 입력)
        ↓
결과 비교 → 오류 수정
        ↓
50개로 확장
        ↓
100+ Golden Dataset (GS-OCR/STEP/MIS)
        ↓
자동 회귀 테스트 (CI 게이트)
```

| 셋 | 규모 | AI① 지표 | 목표(M2→M5) |
|---|---|---|---|
| GS-OCR | 400장 | 줄단위 LaTeX 일치율 | 0.85 → 0.92 |
| GS-STEP | 200건 | first_error_index 일치율 | 0.75 → 0.85 |
| GS-MIS | 250건 | 오개념 macro-F1 | 0.75 → 0.85 |
| GS-LEAK/SAFE | 150+100건 | 정답누출 0건·부적절차단 100% | 차단 (BE/AI② 공유) |
| GS-ITEM | 150건 | 생성문항 오류율 ≤2% | 문항 파트 연계 |

- CI: `pytest tests/golden/` + Actions. `STEP<0.75 or MIS<0.75 → merge 차단`. 검증 완화 금지(ADR-04).
- 수집: 10인+ 필기, 조명·용지 증강. 미성년자는 보호자 동의 후 (5주차 심의 전 성인 데이터만).

---

## 5. 5단계 구축 순서 (최종)

```
1단계: 텍스트 풀이 입력 → LLM 풀이 단계 분석 → 오류 단계 특정
       → 오류 유형 분류 → Skill 매핑 → Knowledge State(Rule) → FastAPI → MySQL
2단계: 수식 검증 보강 (+ SymPy + 오류 판정 보강)
3단계: 손글씨 이미지 → 수식 OCR → LaTeX → AI① 분석
4단계: 규칙 기반 Knowledge State → BKT → Skill 숙련도 추정 고도화
5단계: 100+ Golden Dataset → 자동 평가 → CI 회귀 테스트
최종: 학생APP → Backend → AI①{Vision/OCR, LLM, SymPy, Skill Model, Knowledge State} → AI②
```

일정(15주 중 AI①):
- 1~2주: 1단계 + 테스트 20~30개 + `/analyze-solution` 개통.
- 3주: 오개념트리 30개 동결 + Skill 4개 확정.
- 4~5주: 2단계(SymPy) PoC + OCR 베이스라인 비교. 게이트 OCR≥0.75.
- 6~8주: 3단계 + GS 50→100건, MVP(STEP/MIS 0.75).
- 9주: HITL(재촬영/직접입력, 기타큐).
- 10~14주: 4단계 BKT fitting + 5단계 CI, IRT 연계.
- 12주 판단시점: OCR 미달 시 AI① 집중, 음성 축소.

---

## 6. 오늘 바로 시작안 (Step 1~5)

### Step 1 — 단원 1개 선정
**일차방정식** (중2, `2(x-3)=6` 유형). 범위 고정해야 발산 방지.

### Step 2 — Skill 4개 정의
```python
SKILLS = ["linear_equation", "transposition", "distribution", "arithmetic"]
# linear_equation: 전체 / transposition: 이항 부호변경
# distribution: 분배법칙 / arithmetic: 사칙·부호계산
```

### Step 3 — 문제 20개 + 풀이 20개
- 정상 8 + 계산오류 6 (예: `2x=12 → x=5`) + 개념오류 6 (예: `2(x-3)=2x-3`, 이항부호 누락).
- 텍스트 LaTeX로 먼저 구축, 이미지는 3단계에서 교체.

### Step 4 — 입출력 JSON 확정
최소 계약 (MVP):
```json
{
  "correct": false,
  "error_step": 2,
  "error_type": "concept_error",
  "skill": "distribution",
  "state": "needs_practice"
}
```
확장 계약 (BKT·BE 연동용, 하위호환):
```json
{
  "correct": false,
  "error_step": 2,
  "error_type": "concept_error",
  "skill": "distribution",
  "state": "needs_practice",
  "mastery": 0.34,
  "misconception_id": "2.1",
  "confidence": 0.87,
  "steps_latex": ["2(x-3)=6", "2x-3=6", "2x=9"],
  "valid": [true, false, false],
  "unrecognized": []
}
```

### Step 5 — FastAPI `/analyze-solution`부터 구현
```python
from fastapi import FastAPI
from pydantic import BaseModel
app = FastAPI()

class AnalyzeIn(BaseModel):
    problem_id: str
    problem_latex: str
    solution_text: str  # 1단계는 텍스트, 3단계에서 image_s3_key 추가

@app.post("/analyze-solution")
def analyze_solution(body: AnalyzeIn):
    return {"correct": False, "error_step": 2, "error_type": "concept_error",
            "skill": "distribution", "state": "needs_practice"}
```
- DB: MySQL 8.0 + `attempt, solution_photo, misconception_hit, mastery` (기획서 5.8).
- 다음 커밋: `tests/golden_20.json` + `pytest test_regression.py` 먼저.

---

## 7. 기술 스택 최종표 (v2)

| 구분 | 선정 | 활용 |
|---|---|---|
| Language | Python 3.11 | 전체 |
| 수식 OCR | GPT-4o-mini Vision + Texify2/TrOCR/GOT-OCR2.0/Qwen2.5-VL 비교 | 이미지→LaTeX, 일반OCR과 분리 |
| LLM | GPT-4o-mini Structured Output (Ollama Qwen 폴백) | 단계분할·오개념 후보, 최종판정 아님 |
| 검증 | SymPy (simplify, Eq, solveset, 역풀이 대입) | LLM 검증자, first_error 확정 |
| Skill/KB | Python Rule + MySQL | 오류→Skill→트리 매핑 |
| Knowledge | Rule(MVP) → BKT PyTorch 직접구현(최종) → IRT 연계(py-irt, DKT 보류) | p_known + mastered/learning/needs_practice |
| API/DB | FastAPI Pydantic v2 + MySQL 8.0 | POST /analyze-solution |
| 평가 | Golden 100+ + pytest + Actions (CI 게이트) | OCR/STEP/MIS + 비용·지연 로깅 |

> 최종: `학생APP → Backend → AI①{Vision/OCR, LLM, SymPy, Skill Model, Knowledge State(Rule→BKT→IRT)} → AI②`. LLM 환각은 SymPy가, 단순가감은 BKT가, 사후디버깅은 Golden CI가 막는다.
