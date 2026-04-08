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
    :root { color-scheme: dark; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.6;
      margin: 0;
      padding: 24px 16px 40px;
      color: #f3f4f6;
      background: #1A2130;
    }
    .page {
      width: min(1280px, calc(100vw - 32px));
      margin: 0 auto;
    }
    h1, h2, h3 { line-height: 1.25; }
    .meta { color: #94a3b8; margin-top: 0; }
    ul { padding-left: 1.25rem; }
    .note { background: #202634; border-left: 4px solid #60a5fa; padding: 12px 16px; border-radius: 8px; }
    .section { margin-top: 1.25rem; }
    .report {
      border: 1px solid #2b3446;
      border-radius: 20px;
      padding: 24px 28px 14px;
      box-shadow: 0 18px 40px rgba(0,0,0,.24);
      margin-bottom: 18px;
      background: #202634;
    }
    .report + .report { margin-top: 18px; }
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
"""


def render_article(md_path: Path) -> str:
    title, body = render_markdown(md_path.read_text(encoding="utf-8"))
    clean_title = re.sub(r"\s*\((Draft|Published)\)$", "", title).strip()
    status = "Published" if "(Published)" in title or "Published" in title else ("Draft" if "(Draft)" in title or "Draft" in title else "Report")
    badge_class = "published" if status == "Published" else "draft" if status == "Draft" else "published"
    eyebrow = "장 마감" if status == "Published" else "장 시작"

    return f"""    <article class=\"report\">
      <div class=\"eyebrow {badge_class}\">{eyebrow}</div>
      <h1>{html.escape(clean_title)}</h1>
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
    <title>브리핑</title>
    <style>
{STYLE}
    </style>
  </head>
  <body>
    <main class=\"page\">
{chr(10).join(articles)}
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
