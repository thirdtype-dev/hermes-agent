#!/usr/bin/env python3
"""Render the public market report page from analyst markdown sources.

This keeps the static GitHub Pages report in sync with the latest verified
analyst outputs without relying on a manual copy step.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "docs" / "agent-outputs" / "analyst"
OUTPUT = ROOT / "website" / "static" / "report" / "index.html"

TITLE_RE = re.compile(r"^#\s+(.+)$")
HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$")
BULLET_RE = re.compile(r"^-\s+(.+)$")
STRONG_RE = re.compile(r"\*\*(.+?)\*\*")
CODE_RE = re.compile(r"`([^`]+)`")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
SUPPRESS_SECTION_HEADINGS = {"발행 상태", "출처 검증 고지"}

RELATED_NEWS = [
    {
        "slug": "news-1",
        "title": "美·이란 협상 기대에 유가↓ 코스피 상승",
        "source": "연합뉴스",
        "body": [
            "유가 부담이 완화되면 제조·운송·화학 업종의 비용 부담이 줄어들 수 있어 국내 증시에 우호적인 해석이 가능하다.",
            "이런 국면에서는 지수 방향성보다도 대형주와 수출주, 그리고 원가 민감 업종의 상대 강도를 함께 보는 편이 유리하다.",
        ],
    },
    {
        "slug": "news-2",
        "title": "다우 흐름 속 뉴욕증시 변동성 점검",
        "source": "연합뉴스",
        "body": [
            "미국 증시의 장중 변동성이 커지면 국내 장초반에도 위험선호가 흔들릴 수 있어 선물 흐름과 대형 기술주의 반응이 중요하다.",
            "다우가 버티는지, 나스닥이 흔들리는지에 따라 다음 장의 업종 순환 속도도 달라질 수 있다.",
        ],
    },
    {
        "slug": "news-3",
        "title": "외국인·기관 수급 전환 업종 확대 여부",
        "source": "연합뉴스",
        "body": [
            "외국인과 기관이 동시에 들어오는 업종이 넓어지면 단기 반등이 아니라 추세 확장의 신호로 읽을 여지가 커진다.",
            "반대로 수급이 몇 개 대형주에만 쏠리면 장의 폭은 좁고 지속성은 약할 수 있다.",
        ],
    },
    {
        "slug": "news-4",
        "title": "코스피 거래대금 확대와 대형주 중심 매수세",
        "source": "연합뉴스",
        "body": [
            "거래대금 확대는 시장 참여가 살아있다는 뜻이지만, 실제로는 대형주 쏠림인지 업종 전반 확산인지 구분해야 한다.",
            "대형주 중심 매수세가 이어질 때는 지수 방어는 가능하지만, 중소형주까지 번져야 체감 장세가 좋아진다.",
        ],
    },
    {
        "slug": "news-5",
        "title": "코스닥 1%대 상승, 중소형주 확산 확인",
        "source": "연합뉴스",
        "body": [
            "코스닥이 강하면 위험선호가 살아있다는 신호로 해석할 수 있지만, 거래대금과 업종 breadth가 같이 붙어야 의미가 커진다.",
            "중소형주 확산이 단발성인지 지속형인지 확인하려면 시가 이후 유지력과 종가 강도를 같이 봐야 한다.",
        ],
    },
]

DISCLAIMER = (
    "본 서비스의 투자 정보는 단순 참고용이며, 종목 추천이나 투자 권유가 아닙니다. "
    "최종적인 투자 결정과 그에 따른 책임은 투자자 본인에게 있음을 알려드립니다"
)


def latest(pattern: str) -> Path | None:
    files = sorted(SOURCE_DIR.glob(pattern))
    return files[-1] if files else None


def render_related_news() -> str:
    items = []
    for item in RELATED_NEWS:
        items.append(
            "<li class=\"news-item\">"
            f'<a class="news-link" href="#{item["slug"]}">{html.escape(item["title"])}</a>'
            f'<span class="news-source">{html.escape(item["source"])} · 본문으로 이동</span>'
            "</li>"
        )
    return "<section class=\"news-section\"><h2>주요 뉴스</h2><ul class=\"news-list\">" + "".join(items) + "</ul></section>"


def render_news_articles() -> str:
    sections = []
    for item in RELATED_NEWS:
        body = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in item["body"])
        sections.append(
            f'<section class="news-article" id="{item["slug"]}">'
            f'<h2>{html.escape(item["title"])}</h2>'
            f'<div class="news-source">{html.escape(item["source"])}</div>'
            f"{body}"
            "</section>"
        )
    return "<section class=\"news-body\"><h2>뉴스 본문</h2>" + "".join(sections) + "</section>"


def inline_format(text: str) -> str:
    escaped = html.escape(text)
    escaped = LINK_RE.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}" target="_blank" rel="noopener">{m.group(1)}</a>', escaped)
    escaped = STRONG_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = CODE_RE.sub(r"<code>\1</code>", escaped)
    return escaped


def render_markdown(md: str) -> tuple[str, str]:
    lines = md.splitlines()
    title = ""
    if lines and (m := TITLE_RE.match(lines[0].strip())):
        title = m.group(1)
        lines = lines[1:]

    parts: list[str] = []
    paragraph: list[str] = []
    in_ul = False
    skip_heading_level: int | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            parts.append(f"<p>{' '.join(paragraph)}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal in_ul
        if in_ul:
            parts.append("</ul>")
            in_ul = False

    for raw in lines:
        line = raw.strip()
        if not line:
            flush_paragraph()
            close_list()
            continue

        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            heading_text = heading.group(2).strip()
            if skip_heading_level is not None and level <= skip_heading_level:
                skip_heading_level = None
            if skip_heading_level is not None:
                continue
            if heading_text in SUPPRESS_SECTION_HEADINGS:
                flush_paragraph()
                close_list()
                skip_heading_level = level
                continue
            flush_paragraph()
            close_list()
            parts.append(f"<h{level}>{inline_format(heading_text)}</h{level}>")
            continue

        if skip_heading_level is not None:
            continue

        bullet = BULLET_RE.match(line)
        if bullet:
            flush_paragraph()
            if not in_ul:
                parts.append("<ul>")
                in_ul = True
            parts.append(f"<li>{inline_format(bullet.group(1))}</li>")
            continue

        paragraph.append(inline_format(line))

    flush_paragraph()
    close_list()
    return title, "\n".join(parts)


STYLE = """
    :root { color-scheme: dark; scroll-behavior: smooth; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.6;
      margin: 0;
      padding: 0;
      color: #f3f4f6;
      background: #1A2130;
    }
    .page {
      width: 100%;
      margin: 0;
    }
    h1, h2, h3 { line-height: 1.25; }
    .meta { color: #94a3b8; margin-top: 0; }
    ul { padding-left: 1.25rem; }
    .note { background: #202634; border-left: 4px solid #60a5fa; padding: 12px 16px; border-radius: 8px; }
    .section { margin-top: 1.25rem; }
    .report {
      border: 0;
      border-radius: 0;
      padding: 24px 28px 20px;
      box-shadow: none;
      margin: 0;
      background: #202634;
    }
    .report + .report { margin-top: 16px; border-top: 1px solid #2b3446; }
    .report > h1 {
      margin: 2px 0 16px;
      font-size: clamp(1.55rem, 2vw, 2rem);
      letter-spacing: -0.02em;
      color: #ffffff;
    }
    .report h2 {
      margin-top: 20px;
      margin-bottom: 10px;
      color: #e5e7eb;
      font-size: 1.08rem;
    }
    .report p, .report li { color: #dbe4f0; }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 999px;
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.02em;
      margin-bottom: 12px;
    }
    .eyebrow.published { background: rgba(34,197,94,.16); color: #86efac; }
    .eyebrow.draft { background: rgba(245,158,11,.16); color: #fcd34d; }
    .small { font-size: 0.95rem; color: #cbd5e1; }
    code { background: #111827; padding: 0 4px; border-radius: 4px; }
    a { color: #93c5fd; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .news-section {
      margin-top: 22px;
      padding-top: 18px;
      border-top: 1px solid #2b3446;
    }
    .page > .disclaimer {
      margin: 16px 0 0;
      border-radius: 0;
      border-left: 0;
      border-right: 0;
    }
    .news-list {
      list-style: none;
      padding-left: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }
    .news-item {
      display: flex;
      flex-direction: column;
      gap: 4px;
      padding: 12px 14px;
      border-radius: 14px;
      background: #1a2130;
      border: 1px solid #2b3446;
    }
    .news-link {
      color: #bfdbfe;
      font-weight: 600;
      text-decoration: none;
    }
    .news-link:hover { text-decoration: underline; }
    .news-source {
      font-size: 0.88rem;
      color: #94a3b8;
    }
    .news-body {
      margin-top: 24px;
      padding-top: 20px;
      border-top: 1px solid #2b3446;
    }
    .news-article {
      margin-top: 14px;
      padding: 16px 18px 18px;
      border-radius: 16px;
      background: #111827;
      border: 1px solid #2b3446;
      scroll-margin-top: 12px;
    }
    .news-article h2 {
      margin: 0 0 6px;
      color: #ffffff;
      font-size: 1rem;
    }
    .disclaimer {
      margin-top: 12px;
      padding: 14px 16px;
      border: 1px solid #334155;
      border-radius: 14px;
      background: #111827;
      color: #cbd5e1;
      font-size: 0.92rem;
    }
"""


def render_article(md_path: Path) -> str:
    title, body = render_markdown(md_path.read_text(encoding="utf-8"))
    clean_title = re.sub(r"\s*\((Draft|Published)\)$", "", title).strip()
    status = "Published" if "(Published)" in title or "Published" in title else ("Draft" if "(Draft)" in title or "Draft" in title else "Report")
    badge_class = "published" if status == "Published" else "draft" if status == "Draft" else "published"
    eyebrow = "장 시작" if "open-report" in md_path.name else "장 마감"

    return f"""    <article class=\"report\">
      <div class=\"eyebrow {badge_class}\">{eyebrow}</div>
      <h1>{html.escape(clean_title)}</h1>
{body}
{render_related_news()}
{render_news_articles()}
    </article>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(OUTPUT), help="Path to write the rendered report HTML")
    args = parser.parse_args()

    close_md = latest("market-close-report-*.md")
    open_md = latest("market-open-report-*.md")
    if not close_md or not open_md:
        print("error: missing market report source markdown files", file=sys.stderr)
        return 1

    output = Path(args.output)
    articles = [render_article(close_md), render_article(open_md)]

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        f"""<!doctype html>
<html lang=\"ko\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>브리핑</title>
    <style>
{STYLE}
    </style>
  </head>
  <body>
    <main class=\"page\">
{chr(10).join(articles)}
      <section class=\"disclaimer\">{DISCLAIMER}</section>
    </main>
  </body>
</html>
""",
        encoding="utf-8",
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
