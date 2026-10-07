"""Markdown → PDF · 발행 — 담당 C (agent/ow-quality)

검토용 Markdown을 PDF로 변환하고 제출 전 품질 점검을 수행한다.

원칙:
- validation error가 있으면 제출본을 생성하지 않는다.
- 검토용 초안과 최종 제출본 경로를 분리한다.
- SUMMARY / 한글 / 표 / REFERENCE 기본 품질을 점검한다.
- PDF 라이브러리 문제가 전체 그래프 실행을 중단시키지 않도록
  이 모듈은 그래프와 분리해서 실행한다.

Orchestrator-Workers (계획서 §7-2):
- 렌더링한 PDF 의 **실제 쪽수**를 잰다(`count_pages`). 10쪽 상한은 문자 수 추정이 아니라 이 값으로 본다.
- 발행 직전 publish guard 로 synthesis·report·quality_eval 버전과 평가한 PDF 를 대조한다.
- 품질 미달이면 partial 로 발행한다 — 첫머리·§6 에 미달 기준을 적고, 10쪽을 넘으면 최소 표시
  수준으로 압축해 다시 렌더링한다. 10쪽 초과 상태로는 발행하지 않는다.

사용:
    uv sync --extra pdf
    uv run python -m src.output.pdf                       # output/report.md → 제출본
    uv run python -m src.output.pdf --measure runs/<run_id>/report.md   # 렌더링 쪽수만 잰다
"""

import json
import shutil
import sys
from pathlib import Path

from src.settings import settings

RUNS_DIR = Path("runs")
DEFAULT_REPORT = Path("output/report.md")

FINAL_FILENAME = (
    "RAG-Output_판교_8반_권수진+권예리+김민+박인기+정승원.pdf"
)


CSS = """
@page {
    size: A4;
    margin: 20mm 18mm;
}

body {
    font-family:
        "Apple SD Gothic Neo",
        "AppleSDGothicNeo",
        "NanumGothic",
        "Malgun Gothic",
        sans-serif;
    font-size: 10.5pt;
    line-height: 1.6;
}

h1 {
    font-size: 18pt;
}

h2 {
    font-size: 14pt;
    margin-top: 1.4em;
    page-break-after: avoid;
}

h3 {
    font-size: 12pt;
    page-break-after: avoid;
}

table {
    border-collapse: collapse;
    width: 100%;
    font-size: 9.5pt;
    table-layout: fixed;
}

th,
td {
    border: 1px solid #999;
    padding: 4px 6px;
    text-align: left;
    overflow-wrap: anywhere;
    word-break: break-word;
}

tr {
    page-break-inside: avoid;
}

code {
    font-size: 9pt;
    overflow-wrap: anywhere;
}

pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}
"""


def _read_validation_errors(run_dir: Path) -> list:
    """현재 run의 validation error를 읽는다."""
    candidates = [
        run_dir / "validation.json",
        run_dir / "validation-errors.json",
        run_dir / "final-validation.json",
    ]

    for path in candidates:
        if not path.exists():
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            return data.get("validation_errors") or data.get("errors") or []

    return []


def _extract_section(markdown_text: str, heading: str, next_heading: str) -> str:
    """Markdown의 두 heading 사이 내용을 가져온다."""
    start = markdown_text.find(heading)

    if start == -1:
        return ""

    start += len(heading)

    end = markdown_text.find(next_heading, start)

    if end == -1:
        end = len(markdown_text)

    return markdown_text[start:end].strip()


def check_summary(markdown_text: str) -> tuple[bool, str]:
    """SUMMARY가 과도하게 긴지 간단히 점검한다."""
    summary = _extract_section(
        markdown_text,
        "## SUMMARY",
        "## 1. 분석 배경",
    )

    if not summary:
        return False, "SUMMARY를 찾지 못함"

    chars = len(summary.replace("\n", " ").strip())

    # 실제 반 페이지 여부는 렌더링 환경에 따라 달라지므로
    # 문자 수는 사전 경고용 heuristic으로만 사용한다.
    if chars > 1200:
        return False, f"SUMMARY가 길 수 있음 ({chars}자)"

    return True, f"SUMMARY 길이 확인 ({chars}자)"


def check_korean(markdown_text: str) -> tuple[bool, str]:
    """원문에 한글이 존재하는지 점검한다."""
    has_korean = any("가" <= char <= "힣" for char in markdown_text)

    if not has_korean:
        return False, "한글 텍스트를 찾지 못함"

    return True, "한글 텍스트 존재 확인"


def check_tables(markdown_text: str) -> tuple[bool, str]:
    """Markdown 표 존재 여부와 과도하게 긴 행을 점검한다."""
    table_lines = [
        line for line in markdown_text.splitlines()
        if line.strip().startswith("|")
    ]

    if not table_lines:
        return True, "Markdown 표 없음"

    longest = max(len(line) for line in table_lines)

    if longest > 500:
        return False, f"매우 긴 표 행 존재 ({longest}자)"

    return True, f"표 기본 점검 완료 ({len(table_lines)}개 행)"


def check_reference(markdown_text: str) -> tuple[bool, str]:
    """REFERENCE가 존재하고 본문 뒤쪽에 위치하는지 확인한다."""
    pos = markdown_text.rfind("## REFERENCE")

    if pos == -1:
        return False, "REFERENCE 섹션 없음"

    ratio = pos / max(len(markdown_text), 1)

    if ratio < 0.6:
        return False, "REFERENCE가 문서 후반부에 있지 않음"

    return True, "REFERENCE 위치 확인"


def quality_checks(markdown_text: str) -> list[dict]:
    """제출 전 자동 품질 점검."""
    checks = []

    for name, fn in [
        ("summary", check_summary),
        ("korean", check_korean),
        ("table", check_tables),
        ("reference", check_reference),
    ]:
        passed, message = fn(markdown_text)

        checks.append({
            "check": name,
            "passed": passed,
            "message": message,
        })

    return checks


def render_pdf(markdown_path: Path, pdf_path: Path) -> None:
    """Markdown을 PDF로 변환한다."""
    try:
        import markdown
        from weasyprint import CSS as WCSS
        from weasyprint import HTML

    except ImportError:
        raise RuntimeError(
            "PDF 의존성 미설치. "
            "uv sync --extra pdf 를 실행하고, "
            "macOS에서는 필요 시 brew install pango 를 실행하세요."
        )

    markdown_text = markdown_path.read_text(encoding="utf-8")

    html = markdown.markdown(
        markdown_text,
        extensions=[
            "tables",
            "fenced_code",
        ],
    )

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    HTML(string=html).write_pdf(
        pdf_path,
        stylesheets=[WCSS(string=CSS)],
    )


def count_pages(pdf_path: Path) -> int | None:
    """렌더링된 PDF 의 쪽수. 읽을 수 없으면 None (쪽수를 모른다는 뜻이지 0쪽이 아니다)."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return None


def max_pages() -> int:
    return int((settings().get("report") or {}).get("max_pages") or 10)


def build_submission(
    markdown_path: Path,
    final_path: Path,
    validation_errors: list | None = None,
) -> dict:
    """검증 통과 시에만 최종 제출 PDF를 만든다."""
    validation_errors = validation_errors or []

    if validation_errors:
        return {
            "generated": False,
            "reason": "validation_errors 존재",
            "validation_errors": validation_errors,
            "checks": [],
            "path": None,
        }

    if not markdown_path.exists():
        return {
            "generated": False,
            "reason": f"Markdown 파일 없음: {markdown_path}",
            "validation_errors": [],
            "checks": [],
            "path": None,
        }

    markdown_text = markdown_path.read_text(encoding="utf-8")
    checks = quality_checks(markdown_text)

    failed_checks = [
        check
        for check in checks
        if not check["passed"]
    ]

    if failed_checks:
        return {
            "generated": False,
            "reason": "PDF 품질 사전 점검 실패",
            "validation_errors": [],
            "checks": checks,
            "path": None,
        }

    render_pdf(markdown_path, final_path)

    return {
        "generated": True,
        "reason": "ok",
        "validation_errors": [],
        "checks": checks,
        "path": str(final_path),
    }


def measure(markdown_path: Path) -> int | None:
    """표시 상한 보정용 — Markdown 하나를 렌더링해 쪽수를 출력한다(제출본은 만들지 않는다)."""
    pdf_path = markdown_path.with_suffix(".measure.pdf")
    render_pdf(markdown_path, pdf_path)
    pages = count_pages(pdf_path)
    print(f"{markdown_path}: {pages}쪽 (상한 {max_pages()}쪽)")
    return pages


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--measure":
        measure(Path(sys.argv[2]))
        return

    markdown_path = DEFAULT_REPORT
    final_dir = Path("output/final")
    final_path = final_dir / FINAL_FILENAME

    validation_errors = []

    result = build_submission(
        markdown_path=markdown_path,
        final_path=final_path,
        validation_errors=validation_errors,
    )

    print("\n[PDF 제출 전 점검]")

    for check in result["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        print(f"- {check['check']}: {mark} — {check['message']}")

    if not result["generated"]:
        print(f"\n제출본 생성 안 함: {result['reason']}")
        sys.exit(1)

    print(f"\n완료: {result['path']}")


if __name__ == "__main__":
    main()

def _render_checked(markdown: str, markdown_path: Path, final_path: Path) -> dict:
    """Markdown 을 저장하고 제출본을 만든다. PDF 의존성이 없어도 그래프를 죽이지 않는다."""
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    try:
        result = build_submission(markdown_path=markdown_path, final_path=final_path)
    except Exception as exc:
        # 선택 의존성이다. weasyprint 가 깔려 있어도 libpango 같은 시스템 라이브러리가
        # 없으면 OSError 가 난다. PDF 하나 때문에 실행 전체를 버리지 않는다.
        result = {"generated": False, "reason": f"{type(exc).__name__}: {exc}".strip()[:300],
                  "validation_errors": [], "checks": quality_checks(markdown), "path": None}
    result["pages"] = count_pages(final_path) if result["generated"] else None
    return result


def _failed_criteria(quality: dict) -> list[tuple[str, list[str]]]:
    return [(name, list(v.get("reasons") or []))
            for name, v in (quality.get("verdicts") or {}).items() if v.get("status") == "fail"]


def publish(state) -> dict:
    """레이아웃 확인 후 제출본 저장 (설계서 §9).

    품질 평가가 없는 옛 흐름(review → final_check → report → publish)은 그대로 둔다.
    품질 평가가 있으면(계획서 §7-2):
    1. publish guard — 구버전이면 제출본을 만들지 않고 failed 로 끝낸다(라우터가 먼저 막는다).
    2. 통과면 품질 평가가 쪽수를 잰 그 PDF 를 제출본으로 옮긴다(평가한 것 = 제출한 것).
    3. 미달이면 partial 표시를 넣어 다시 렌더링하고, 10쪽을 넘으면 최소 표시 수준으로 압축한다.
    """
    if not state.get("quality_eval"):
        return _publish_legacy(state)

    from src.agents import report as report_module
    from src.orchestrator.versions import check_publish_guard
    from src.state import run_dir
    from src.tools.web_store import save_json

    directory = run_dir(state)
    markdown_path = directory / "report.md"
    final_path = directory / "final" / FINAL_FILENAME
    quality = state["quality_eval"]
    stale = check_publish_guard(state)          # 같은 버전 파일 손상은 예외로 실행을 끝낸다
    if stale:
        result = {"generated": False, "reason": f"publish guard: 구버전 {', '.join(stale)}",
                  "validation_errors": [], "checks": [], "path": None, "pages": None}
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        save_json(directory / "submission.json", result)
        return {"run_status": "failed", "stop_reason": result["reason"], "report_paths": [],
                "trace": [{"node": "publish", "status": "failed", "attempt": 1,
                           "reason": result["reason"]}]}

    limit = max_pages()
    version = state.get("report_version") or 1
    compaction = int((state.get("report_manifest") or {}).get("compaction") or 0)
    partial = None
    if quality.get("passed"):
        markdown = state.get("report", "")
        evaluated = Path(quality.get("pdf_path") or "")
        if quality.get("pdf_path") and evaluated.exists():
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(markdown, encoding="utf-8")
            final_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(evaluated, final_path)
            result = {"generated": True, "reason": "ok", "validation_errors": [],
                      "checks": quality_checks(markdown), "path": str(final_path),
                      "pages": count_pages(final_path)}
        else:
            result = _render_checked(markdown, markdown_path, final_path)
    else:
        partial = {"stop_reason": state.get("stop_reason") or "품질 기준 미달",
                   "failed": _failed_criteria(quality)}
        markdown, _ = report_module.build(state, version=version, compaction=compaction,
                                          partial=partial)
        result = _render_checked(markdown, markdown_path, final_path)
        most = (settings().get("report") or {}).get("max_compaction", 2)
        if result["pages"] is not None and result["pages"] > limit and compaction < most:
            # 10쪽 초과로는 발행하지 않는다. 최소 표시 수준(칸마다 1개)으로 다시 만든다.
            compaction = most
            markdown, _ = report_module.build(state, version=version, compaction=compaction,
                                              partial=partial)
            result = _render_checked(markdown, markdown_path, final_path)
        result["compaction"] = compaction

    if result["pages"] is not None and result["pages"] > limit:
        # 최소 표시 수준으로도 넘으면 제출본을 지운다 — 상한을 넘긴 PDF 를 제출본 경로에 두지 않는다.
        final_path.unlink(missing_ok=True)
        result |= {"generated": False, "path": None,
                   "reason": f"pages: {result['pages']}쪽 > {limit}쪽 (최소 표시 수준에서도 초과)"}

    run_status = "completed" if quality.get("passed") else "partial"
    if not result["generated"] and run_status == "completed":
        run_status = "partial"            # 제출본 PDF 없이 Markdown 만 남은 발행
    save_json(directory / "submission.json", result | {"run_status": run_status, "partial": partial})

    paths = [str(markdown_path)] + ([result["path"]] if result["generated"] else [])
    update = {"report_paths": paths, "run_status": run_status,
              "trace": [{"node": "publish", "status": "ok" if run_status == "completed" else "partial",
                         "attempt": 1, "reason": result["reason"], "pages": result["pages"],
                         "run_status": run_status, "report_version": version,
                         "checks": {c["check"]: c["passed"] for c in result["checks"]}}]}
    if run_status == "partial" and not state.get("stop_reason"):
        update["stop_reason"] = partial["stop_reason"] if partial else result["reason"]
    return update


def _publish_legacy(state) -> dict:
    """레이아웃 확인 후 제출본 저장 (설계서 §9, 부록 A 의 `Z` 노드).

    Markdown 을 실행 저장소에 남기고, 검증을 통과했을 때만 제출본 PDF 를 만든다.
    §8 — 검토용 초안은 별도 경로에 저장하며 제출본과 구분한다. 제출본은
    `runs/<run_id>/final/` 에 둔다.

    PDF 의존성(weasyprint·pango)이 없어도 그래프를 죽이지 않는다. 그 사실을
    submission.json 과 trace 에 남기고 Markdown 경로만 돌려준다.
    """
    from src.state import run_dir
    from src.tools.web_store import save_json

    directory = run_dir(state)
    markdown_path = directory / "report.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(state.get("report", ""), encoding="utf-8")

    errors = (state.get("validation") or {}).get("errors") or []
    final_path = directory / "final" / FINAL_FILENAME

    try:
        result = build_submission(
            markdown_path=markdown_path,
            final_path=final_path,
            validation_errors=errors,
        )
    except Exception as exc:
        # 선택 의존성이다. weasyprint 가 깔려 있어도 libpango 같은 시스템 라이브러리가
        # 없으면 OSError 가 난다. PDF 하나 때문에 실행 전체를 버리지 않는다.
        result = {
            "generated": False,
            "reason": f"{type(exc).__name__}: {exc}".strip()[:300],
            "validation_errors": [],
            "checks": quality_checks(state.get("report", "")),
            "path": None,
        }

    save_json(directory / "submission.json", result)

    paths = [str(markdown_path)]
    if result["generated"]:
        paths.append(result["path"])

    return {
        "report_paths": paths,
        "trace": [{
            "node": "publish",
            "status": "ok" if result["generated"] else "partial",
            "attempt": 1,
            "reason": result["reason"],
            "checks": {check["check"]: check["passed"] for check in result["checks"]},
        }],
    }
