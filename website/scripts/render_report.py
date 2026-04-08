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


def latest(pattern: str) -> Path | None:
    files = sorted(SOURCE_DIR.glob(pattern))
    return files[-1] if files else None


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
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            parts.append(f"<h{level}>{inline_format(heading.group(2))}</h{level}>")
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
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; margin: 40px auto; max-width: 920px; padding: 0 20px; color: #1f2937; background: #fff; }
    h1, h2, h3 { line-height: 1.25; }
    h1 { margin-bottom: 0.25rem; }
    .meta { color: #6b7280; margin-top: 0; }
    .published, .draft { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 0.92rem; margin: 0.25rem 0 1rem; }
    .published { background: #dcfce7; color: #166534; }
    .draft { background: #fef3c7; color: #92400e; }
    ul { padding-left: 1.25rem; }
    .note { background: #f9fafb; border-left: 4px solid #60a5fa; padding: 12px 16px; border-radius: 8px; }
    .section { margin-top: 1.25rem; }
    .report { border: 1px solid #e5e7eb; border-radius: 18px; padding: 22px 22px 10px; box-shadow: 0 10px 30px rgba(0,0,0,.04); margin-bottom: 18px; }
    .report + .report { margin-top: 18px; }
    .small { font-size: 0.95rem; color: #4b5563; }
    code { background: #f3f4f6; padding: 0 4px; border-radius: 4px; }
    a { color: #2563eb; text-decoration: none; }
    a:hover { text-decoration: underline; }
"""


def render_article(md_path: Path) -> str:
    title, body = render_markdown(md_path.read_text(encoding="utf-8"))
    status = "Published" if "(Published)" in title or "Published" in title else ("Draft" if "(Draft)" in title or "Draft" in title else "Report")
    badge_class = "published" if status == "Published" else "draft" if status == "Draft" else "published"
    badge_text = "Published · 장 마감 반영" if status == "Published" else "Draft · 출처 검증 대기" if status == "Draft" else "Published"

    return f"""    <article class=\"report\">
      <h1>{html.escape(title)}</h1>
      <p class=\"meta\">Source: <code>{html.escape(str(md_path.relative_to(ROOT)))}</code></p>
      <div class=\"{badge_class}\">{badge_text}</div>
{body}
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
    <title>시장 리포트</title>
    <style>
{STYLE}
    </style>
  </head>
  <body>
    <h1>시장 리포트</h1>
    <p class=\"meta\">GitHub Pages publish · public page: <code>/report/index.html</code></p>
{chr(10).join(articles)}
  </body>
</html>
""",
        encoding="utf-8",
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
