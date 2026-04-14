from types import SimpleNamespace

import gateway.builtin_hooks.boot_md as boot_md


def test_run_boot_agent_uses_resolved_runtime(monkeypatch):
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run_conversation(self, prompt):
            captured["prompt"] = prompt
            return {"final_response": "[SILENT]"}

    monkeypatch.setattr(boot_md, "_resolve_boot_runtime", lambda: {
        "model": "gpt-5.4-mini",
        "provider": "openai-codex",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "api_mode": "codex_responses",
        "api_key": "token",
    })

    import run_agent
    monkeypatch.setattr(run_agent, "AIAgent", FakeAgent)

    boot_md._run_boot_agent("1. check status\n2. report if needed")

    assert captured["model"] == "gpt-5.4-mini"
    assert captured["provider"] == "openai-codex"
    assert captured["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert captured["api_mode"] == "codex_responses"
    assert captured["api_key"] == "token"
    assert captured["quiet_mode"] is True
    assert captured["skip_context_files"] is True
    assert captured["skip_memory"] is True
    assert captured["max_iterations"] == 20
    assert "BOOT.md" in captured["prompt"]
    assert "check status" in captured["prompt"]
