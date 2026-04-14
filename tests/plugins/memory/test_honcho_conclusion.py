from types import SimpleNamespace

from plugins.memory.honcho.session import HonchoSessionManager


class _FakeConclusionsScope:
    def __init__(self):
        self.created = []

    def create(self, items):
        self.created.extend(items)


class _FakePeer:
    def __init__(self, scope):
        self._scope = scope

    def conclusions_of(self, _peer_id):
        return self._scope


def test_create_conclusion_recreates_missing_session(monkeypatch):
    manager = HonchoSessionManager(config=SimpleNamespace(
        write_frequency="async",
        dialectic_reasoning_level="low",
        dialectic_dynamic=True,
        dialectic_max_chars=600,
        observation_mode="directional",
        user_observe_me=True,
        user_observe_others=True,
        ai_observe_me=True,
        ai_observe_others=True,
        message_max_chars=25000,
        dialectic_max_input_chars=10000,
        peer_name="회인",
        ai_peer="hermes",
        context_tokens=None,
    ))

    scope = _FakeConclusionsScope()
    fake_peer = _FakePeer(scope)

    created = []

    def fake_get_or_create(session_key):
        created.append(session_key)
        return SimpleNamespace(
            key=session_key,
            user_peer_id="user-peer",
            assistant_peer_id="assistant-peer",
            honcho_session_id="honcho-session",
        )

    monkeypatch.setattr(manager, "get_or_create", fake_get_or_create)
    monkeypatch.setattr(manager, "_get_or_create_peer", lambda _peer_id: fake_peer)

    ok = manager.create_conclusion("telegram:123", "User prefers action-first replies")

    assert ok is True
    assert created == ["telegram:123"]
    assert scope.created == [
        {"content": "User prefers action-first replies", "session_id": "honcho-session"}
    ]
