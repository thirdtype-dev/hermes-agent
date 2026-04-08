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
    assert "2026-04-07 장시작 리포트" in html
    assert "2026-04-07 장종료 리포트" in html
    assert "Draft · 출처 검증 대기" in html
    assert "<article class=\"report\">" in html
    assert "Source: <code>docs/agent-outputs/analyst/market-open-report-2026-04-07.md</code>" in html
