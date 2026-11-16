# AI① ngrok 고정 도메인 운영 절차 (2026-11-10)

대상: AI① FastAPI (`127.0.0.1:8001`) + ngrok 고정 도메인
`https://vividness-pep-unboxed.ngrok-free.dev`

> 이 문서는 **점검 결과**와 **다음 재기동부터 적용할 절차**를 기록한다.
> 점검 시점에 실행 중인 서버·터널은 종료하지 않았다.

---

## 1. 점검 결과 요약

| 항목 | 결과 | 근거 |
|---|---|---|
| 현재 터널이 고정 도메인을 쓰는가 | **예** | `127.0.0.1:4040/api/tunnels` → `public_url = https://vividness-pep-unboxed.ngrok-free.dev` |
| 도메인이 **명시적으로 고정**되어 있는가 | **아니오** | ngrok 명령줄: `ngrok http 8001 --log stdout` (`--url` 없음) |
| 로컬 설정에 domain 정의가 있는가 | **없음** | `%LOCALAPPDATA%\ngrok\ngrok.yml` = 74바이트, `version`·`authtoken`만 |
| 그럼에도 같은 도메인인 이유 | ngrok이 **계정에 배정된 dev 도메인**을 자동 사용 | 로그: `started tunnel ... url=https://vividness-pep-unboxed.ngrok-free.dev` (지정 없이) |
| 자동 시작(부팅 시) 등록 | **없음** (수동 기동) | 서비스/작업 스케줄러 미등록 |
| 포트 | `127.0.0.1:8001` (LAN 비노출) | `config.addr = http://localhost:8001` |
| ngrok 버전 | **3.39.13** (최소 지원 3.28.0 충족) | `ngrok version` |
| Authtoken 위치 | `%LOCALAPPDATA%\ngrok\ngrok.yml` (저장소 밖) | 명령줄에 토큰 없음 |
| `.gitignore` | 인증·키 패턴 **보강 완료** | 아래 §5 |

**결론:** 지금은 결과적으로 고정 도메인을 쓰고 있지만, **보장은 없다**(계정 기본
도메인에 의존). 다음 재기동부터 `--url`로 **명시 고정**하면 계정 설정이 바뀌어도
같은 주소를 요구하게 된다. 도메인이 계정에 실제로 예약돼 있지 않으면 `--url` 기동이
실패하며, 그때는 ngrok 대시보드에서 도메인 예약 상태를 확인해야 한다.

### 현재 실행 상태 (점검 시점)

| 구성 | PID | 비고 |
|---|---|---|
| uvicorn (`.venv` 런처) | 18192 | `--host 127.0.0.1 --port 8001` |
| uvicorn (실제 워커) | 16596 | 8001 리스닝 |
| ngrok | 11504 | 터널 1개, `https://...ngrok-free.dev` |

---

## 2. 고정 도메인으로 실행하는 명령

### 2.1 수동 실행 (권장 형태)

```powershell
# 1) API Key 주입 (값은 파일/환경변수에서만, 화면 출력 금지)
$env:AI1_INTERNAL_API_KEYS = (Get-Content "$env:TEMP\ai1_internal_key.txt" -Raw).Trim()

# 2) FastAPI
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8001

# 3) 별도 터미널 — 도메인 고정
ngrok http 8001 --url https://vividness-pep-unboxed.ngrok-free.dev
```

- `--url`은 ngrok 3.39.13에서 지원 확인(`ngrok http --help`).
- Authtoken은 **로컬 설정 파일**에서 자동으로 읽는다. 명령줄·스크립트에 넣지 않는다.

### 2.2 설정 파일로 고정하는 대안 (선택)

`%LOCALAPPDATA%\ngrok\ngrok.yml`(저장소 밖)에 터널을 정의하면 `ngrok start ai1`로 뜬다.

```yaml
version: "2"
authtoken: <로컬에만 존재 — 문서·깃에 기재 금지>
tunnels:
  ai1:
    proto: http
    addr: 8001
    domain: vividness-pep-unboxed.ngrok-free.dev
```

> 이 파일은 authtoken을 포함하므로 **절대 저장소로 복사하지 않는다**.
> `.gitignore`에 `ngrok.yml`·`*.ngrok.yml`을 추가해 두었다.

---

## 3. 재사용 스크립트

**`scripts/start_ai1_tunnel.ps1`** — 점검/기동/중지/재시작을 한 곳에서 처리한다.

| 동작 | 명령 |
|---|---|
| 점검(기본, 아무것도 바꾸지 않음) | `powershell -ExecutionPolicy Bypass -File scripts\start_ai1_tunnel.ps1` |
| 기동(이미 떠 있으면 건너뜀) | `... -Action Start` |
| 교체(종료 후 재기동) | `... -Action Start -Restart` |
| 중지 | `... -Action Stop` |

안전 장치:

- **Authtoken 하드코딩 없음** — ngrok 로컬 설정 사용
- **API Key 출력 없음** — 환경변수 또는 `-KeyFile`(기본 `%TEMP%\ai1_internal_key.txt`)
- **중복 기동 방지** — 8001 리스너/같은 도메인 터널이면 건너뜀
- `Stop`은 **이 프로젝트의 uvicorn(`app.main:app`)과 4040 포트의 ngrok만** 종료
- 공개 `/health` 확인 시 `ngrok-skip-browser-warning: true` 사용 + 본문이 JSON인지 검사
- PowerShell 5.1 호환(UTF-8 BOM 저장)

점검 모드 출력 예:

```
[ai1] 고정 도메인: https://vividness-pep-unboxed.ngrok-free.dev
[ai1][ OK ] API Key: 설정됨(값 비출력)
[ai1][ OK ] 포트 8001 리스너: pid=16596
[ai1][ OK ] 로컬 /health 200 ({"status":"ok"})
[ai1] 터널: https://vividness-pep-unboxed.ngrok-free.dev -> http://localhost:8001
[ai1][ OK ] 터널이 고정 도메인을 사용 중
[ai1][ OK ] 공개 /health 200 ({"status":"ok"})
```

---

## 4. 재시작 후 동일 도메인 검증 절차

아래 순서로 확인한다. 각 단계의 기대값과 다르면 그 지점에서 멈춘다.

| # | 확인 | 명령 | 기대 |
|---|---|---|---|
| 1 | 현재 상태 기록 | `scripts\start_ai1_tunnel.ps1` | 전 항목 OK |
| 2 | 종료 | `... -Action Stop` | uvicorn·ngrok 종료 메시지 |
| 3 | 재기동(도메인 고정) | `... -Action Start` | `터널 고정 도메인 일치` |
| 4 | 로컬 헬스 | `curl.exe -s http://127.0.0.1:8001/health` | `{"status":"ok"}` |
| 5 | 터널 URL | `curl.exe -s http://127.0.0.1:4040/api/tunnels` | `public_url`이 `https://vividness-pep-unboxed.ngrok-free.dev` |
| 6 | 공개 헬스 | `curl.exe -s -H "ngrok-skip-browser-warning: true" https://vividness-pep-unboxed.ngrok-free.dev/health` | `{"status":"ok"}` |
| 7 | 인증 동작 | 키 없이 analyze → `401`, 키 있으면 `422`(본문 오류) 또는 `200` | 401/422/200 계약 유지 |
| 8 | Backend 재통보 | 도메인이 같으면 불필요, 바뀌었으면 즉시 통보 | Backend `AI1_BASE_URL` 갱신 |

### 7번 상세 (키 값 비출력)

```powershell
$key = (Get-Content "$env:TEMP\ai1_internal_key.txt" -Raw).Trim()
$base = "https://vividness-pep-unboxed.ngrok-free.dev"

# 키 없음 → 401
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "$base/api/v1/attempts/CHK-1/analyze" `
  -H "Content-Type: application/json" -d "{}"

# 키 있음 → 422(스키마) 또는 200
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "$base/api/v1/attempts/CHK-2/analyze" `
  -H "Content-Type: application/json" -H "X-Internal-Api-Key: $key" -d "{}"
```

> `curl`은 PowerShell에서 `Invoke-WebRequest` 별칭이다. **`curl.exe`**를 쓴다.

### 도메인이 바뀌었을 때

1. `%TEMP%` 로그에서 실제 URL 확인 (`ai1_ngrok.log`)
2. Backend에 새 주소 통보 + `AI1_BASE_URL` 갱신 요청
3. 이 문서·회신초안 D-05·HANDOFF의 주소 갱신
4. S2-3 `AI1_OCR_ALLOWED_HOSTS`는 **Backend 이미지 호스트**이므로 이 값과 무관(변경 불필요)

---

## 5. `.gitignore` 점검 결과

기존에 있던 항목(유지): `.env`·`.env.*`·`*.env`·`secrets/`·`*.secret`·`*.pem`·`*.key`·
`*service-account*.json`·`*google-credentials*.json`·`*application_default_credentials.json`·
`burnished-timer-...json`·`eval/_local/`.

**추가한 항목:**

```
ngrok.yml
*.ngrok.yml
.ngrok/
ngrok.log
*.ngrok.log
ai1_internal_key.txt
ai1_*key*.txt
*.local.ps1
```

- ngrok 설정은 저장소 밖(`%LOCALAPPDATA%\ngrok\ngrok.yml`)에만 두고, 실수로 복사해도
  커밋되지 않게 막았다.
- 로컬 키 파일(`%TEMP%\ai1_internal_key.txt`)과 로컬 전용 스크립트도 차단한다.
- `scripts/start_ai1_tunnel.ps1` 자체는 **비밀값이 없어** 커밋 가능하다.

---

## 6. S1-3 실제 호출 관측 기록 (2026-11-10 11:10)

Backend가 이 도메인으로 실제 호출한 기록(ngrok 로컬 API 요청 이력, 본문·키 미보존).
발신: `Python-urllib/3.14`, Backend 대역(14.42.86.31).

| 시각 | attempt | 결과 | detail.code |
|---|---|---|---|
| 11:10:21 | 900101 | **200** | — |
| 11:10:21 | 900102 | 422 | `E_BAD_PROBLEM` |
| 11:10:22 | 900103 | 422 | `E_UNRECOGNIZED` (`analysis_status:UNKNOWN`) |
| 11:10:22 | 900104 | 422 | `E_BAD_PROBLEM` |
| 11:10:23 | 900105 | 422 | `E_UNKNOWN_SKILL_CODE` |
| 11:10:23 | 900106 | **200** | — |

해석:

- **S1-3는 실제로 수행됐다** (키 인증 통과 → 200 2건). Backend가 `AI1_BASE_URL`에
  이 도메인을 등록한 것으로 판단한다(AI①은 Backend 환경변수를 직접 볼 수 없다).
- `E_UNKNOWN_SKILL_CODE` 1건은 **crosswalk 미승인 SKL 코드** 때문이다. 현재 역변환
  가능한 코드는 `SKL-1-004`(승인) 하나뿐이다. (2026-11-10 A2: `SKL-2-037` 은 공식
  Seed 밖이라 제거)
- `E_BAD_PROBLEM` 2건은 문제식/풀이 전제가 성립하지 않는 입력이다(구체 메시지는
  Backend 요청 본문 확인 필요).
- `E_UNRECOGNIZED` 1건은 단계 판정 불가(정상 정책, `UNKNOWN`).

---

## 7. 주의 사항

| 항목 | 내용 |
|---|---|
| 절전/종료 | PC가 절전·종료되면 터널이 끊긴다. 자동 복구 없음 |
| 무료 플랜 세션 | 계정당 동시 에이전트 1개 — 다른 ngrok을 켜면 이 터널이 끊긴다 |
| 브라우저 접근 | 무료 플랜은 브라우저 UA에 안내 HTML(200)을 넣는다. API 클라이언트는 무영향 |
| URL 공유 | 2026-11-10 11:20~11:21에 카카오톡 스크랩 봇/브라우저가 `/`·`/favicon.ico`를 조회(모두 404). **키는 URL에 넣지 않는다** |
| 키 교체 | `AI1_INTERNAL_API_KEYS`는 프로세스 환경변수라 **재시작해야 반영**된다 |
| 로그 | `%TEMP%\ai1_uvicorn_8001.log`·`.err`, `%TEMP%\ai1_ngrok.log`·`.err` (키·URL 원문 미기록) |
