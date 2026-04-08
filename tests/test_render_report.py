from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path("/Users/thirdtype/.hermes/hermes-agent/website/scripts/render_report.py")


def load_render_report_module():
    spec = importlib.util.spec_from_file_location("render_report", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_report_writes_to_custom_output(tmp_path, monkeypatch):
    module = load_render_report_module()
    output = tmp_path / "report" / "index.html"
    monkeypatch.setattr(sys, "argv", ["render_report.py", "--output", str(output)])

    exit_code = module.main()

    assert exit_code == 0
    assert output.exists()
    html = output.read_text(encoding="utf-8")
    assert html.count("2026-04-07 브리핑") >= 2
    assert "장 시작" in html
    assert "GitHub Pages publish" not in html
    assert "Source:" not in html
    assert "Draft · 출처 검증 대기" not in html
    assert "<article class=\"report\">" in html
    assert "주요 뉴스" in html
    assert "美·이란 협상 기대에 유가↓ 코스피 상승" in html
    assert "본 서비스의 투자 정보는 단순 참고용이며" in html
    assert "<h1>시장 리포트</h1>" not in html
    assert "발행 상태" not in html
    assert "출처 검증 고지" not in html
