"""Tests for model-slot preemption and release in run_agent.py."""

from unittest.mock import MagicMock

import run_agent


class _FakeAgent:
    def __init__(self, session_id, model, provider):
        self.session_id = session_id
        self.model = model
        self.provider = provider
        self.interrupt = MagicMock()
        self.close = MagicMock()


class _SlotPolicy:
    def __init__(self, enabled=True, preempt_on_conflict=True):
        self.enabled = enabled
        self.preempt_on_conflict = preempt_on_conflict

    def __call__(self):
        return {
            "enabled": self.enabled,
            "preempt_on_conflict": self.preempt_on_conflict,
        }


def _clear_slots():
    with run_agent._MODEL_SLOT_LOCK:
        run_agent._MODEL_SLOT_OWNERS.clear()



def test_claim_model_slot_preempts_previous_owner(monkeypatch):
    monkeypatch.setattr(run_agent, "_load_model_slot_policy", _SlotPolicy())
    _clear_slots()

    previous = _FakeAgent("old-session", "gpt-5.4-nano", "openai-codex")
    current = _FakeAgent("new-session", "gpt-5.4-nano", "openai-codex")

    run_agent._claim_model_slot(previous, previous.model)
    run_agent._claim_model_slot(current, current.model)

    previous.interrupt.assert_called_once()
    previous.close.assert_called_once()

    with run_agent._MODEL_SLOT_LOCK:
        owner = run_agent._MODEL_SLOT_OWNERS["gpt-5.4-nano"]["ref"]()
    assert owner is current

    run_agent._release_model_slot(current)
    _clear_slots()



def test_release_model_slot_ignores_stale_owner(monkeypatch):
    monkeypatch.setattr(run_agent, "_load_model_slot_policy", _SlotPolicy())
    _clear_slots()

    previous = _FakeAgent("old-session", "gpt-5.4-nano", "openai-codex")
    current = _FakeAgent("new-session", "gpt-5.4-nano", "openai-codex")

    run_agent._claim_model_slot(previous, previous.model)
    run_agent._claim_model_slot(current, current.model)
    run_agent._release_model_slot(previous)

    with run_agent._MODEL_SLOT_LOCK:
        owner = run_agent._MODEL_SLOT_OWNERS["gpt-5.4-nano"]["ref"]()
    assert owner is current

    run_agent._release_model_slot(current)
    _clear_slots()



def test_claim_model_slot_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(run_agent, "_load_model_slot_policy", _SlotPolicy(enabled=False))
    _clear_slots()

    agent = _FakeAgent("session", "gpt-5.4-nano", "openai-codex")

    run_agent._claim_model_slot(agent, agent.model)

    with run_agent._MODEL_SLOT_LOCK:
        assert run_agent._MODEL_SLOT_OWNERS == {}

    agent.interrupt.assert_not_called()
    agent.close.assert_not_called()
    _clear_slots()
