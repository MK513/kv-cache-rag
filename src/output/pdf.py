"""Markdown → PDF — 담당: R5

그래프와 분리한다. 폰트·시스템 라이브러리 문제로 파이프라인이 죽으면 안 된다.

    uv sync --extra pdf
    uv run python -m src.output.pdf
"""

import sys
from pathlib import Path

SRC = Path("output/report.md")
DST = Path("output/RAG-Output_판교_8반_권수진+권예리+김민+박인기+정승원.pdf")

# 한글 폰트가 없으면 글자가 통째로 깨진다. 시스템 설치 이름에 맞출 것.
CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: "AppleSDGothicNeo", "NanumGothic", "Malgun Gothic", sans-serif;
       font-size: 10.5pt; line-height: 1.6; }
h1 { font-size: 18pt; } h2 { font-size: 14pt; margin-top: 1.4em; } h3 { font-size: 12pt; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #999; padding: 4px 6px; text-align: left; }
code { font-size: 9pt; }
"""


def main():
    try:
        import markdown
        from weasyprint import CSS as WCSS, HTML
    except ImportError:
        sys.exit("PDF 의존성 미설치.  uv sync --extra pdf  (macOS: brew install pango)")

    html = markdown.markdown(SRC.read_text(encoding="utf-8"),
                             extensions=["tables", "fenced_code"])
    HTML(string=html).write_pdf(DST, stylesheets=[WCSS(string=CSS)])
    print(f"완료: {DST}")


if __name__ == "__main__":
    main()
