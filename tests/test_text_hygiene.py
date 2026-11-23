"""텍스트 인코딩 위생 검사 — 저장소 문서·코드에 U+FFFD(깨진 문자)가 없는지.

배경: `app/integration/backend_adapter.py` 와 `tests/test_alignment_confidence.py`
에 U+FFFD(replacement character)가 섞인 한글이 origin/main 부터 존재했다. 코드
동작에는 영향이 없지만 문서/주석 신뢰도를 깨므로, 같은 손상이 다시 들어오지
않도록 고정한다.

대상: `app/`, `tests/`, `scripts/`, `docs/` 및 저장소 루트의 문서 파일
제외: `__pycache__`, `.venv`, `eval/_local`, `docs/AI2_전달_*`(생성물)

파일 수가 많지 않아 수 초 이내에 끝난다. 실패 시 파일/줄과 미리보기를 모아 보여준다.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("app", "tests", "scripts", "docs")
SUFFIXES = {".py", ".md", ".txt", ".csv", ".json"}
SKIP_DIR_NAMES = {"__pycache__", ".venv"}
# Office/Word 잠금 파일(~$...)은 저장소 텍스트가 아닌 편집기 임시 파일이다.
SKIP_FILE_PREFIXES = ("~$",)
SKIP_PREFIXES = (
    ROOT / "eval" / "_local",
    ROOT / "docs" / "AI2_전달_",
)


def _iter_text_files():
    for name in SCAN_DIRS:
        base = ROOT / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SUFFIXES:
                continue
            if path.name.startswith(SKIP_FILE_PREFIXES):
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if any(str(path).startswith(str(prefix)) for prefix in SKIP_PREFIXES):
                continue
            yield path
    # 저장소 루트 문서(README, AGENTS, 개발현황 등)도 같은 규칙으로 검사한다.
    for path in ROOT.iterdir():
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        if path.name.startswith(SKIP_FILE_PREFIXES):
            continue
        yield path


def test_repository_text_has_no_replacement_characters():
    problems: list[str] = []
    for path in sorted(_iter_text_files()):
        relative = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{relative}: UTF-8 디코딩 실패 ({exc.reason})")
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if "\ufffd" in line:
                snippet = line.strip()[:80]
                problems.append(f"{relative}:{number}: {snippet}")

    assert not problems, (
        "U+FFFD(깨진 문자) 또는 UTF-8 디코딩 실패가 발견되었습니다:\n"
        + "\n".join(problems)
    )
