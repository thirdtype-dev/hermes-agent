"""Tests for the built-in live-workers hook."""

import json
import run_agent
import shutil
import subprocess

import pytest

from gateway.builtin_hooks import live_workers


def test_parse_live_workers_manifest_parses_blocks_and_metadata():
    content = """## Worker: planner
provider=openai-codex
model=gpt-5.4-mini
max_iterations=40
quiet_mode=true
skip_context_files=true
skip_memory=true
session_id=live-worker-planner

Keep an eye on live beads and report blockers.

## Worker: executor
provider=custom
model=google/gemma-4-e4b
platform=gateway

Triage the next smallest implementation step and keep it moving.
"""

    workers = live_workers.parse_live_workers_manifest(content)

    assert [worker["name"] for worker in workers] == ["planner", "executor"]
    assert workers[0]["metadata"]["provider"] == "openai-codex"
    assert workers[0]["metadata"]["model"] == "gpt-5.4-mini"
    assert workers[0]["metadata"]["max_iterations"] == 40
    assert workers[0]["metadata"]["quiet_mode"] is True
    assert workers[0]["prompt"] == "Keep an eye on live beads and report blockers."
    assert workers[1]["metadata"]["platform"] == "gateway"
    assert workers[1]["prompt"] == "Triage the next smallest implementation step and keep it moving."


def test_bd_binary_prefers_real_binary_path_over_missing_path(monkeypatch, tmp_path):
    bd_path = tmp_path / "bin" / "bd"
    bd_path.parent.mkdir(parents=True)
    bd_path.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(live_workers.shutil, "which", lambda _name: None)
    monkeypatch.setattr(live_workers, "_BD_CANDIDATES", ("bd", str(bd_path)))

    assert live_workers._bd_binary() == str(bd_path)


def test_issue_is_spawnable_skips_active_leases():
    now = live_workers.datetime(2026, 1, 1, tzinfo=live_workers.timezone.utc)
    active_issue = {
        "status": "in_progress",
        "metadata": {"lease_expires_at": (now + live_workers.timedelta(hours=1)).isoformat()},
    }
    expired_issue = {
        "status": "in_progress",
        "metadata": {"lease_expires_at": (now - live_workers.timedelta(hours=1)).isoformat()},
    }

    assert live_workers._issue_is_spawnable(active_issue, now=now) is False
    assert live_workers._issue_is_spawnable(expired_issue, now=now) is True


@pytest.mark.asyncio
async def test_trigger_live_workers_once_spawns_in_progress_beads_with_lease_claim(tmp_path, monkeypatch):
    live_file = tmp_path / "LIVE_WORKERS.md"
    live_file.write_text(
        """## Worker: planner
provider=openai-codex
model=gpt-5.4-mini
session_id=live-worker-planner

Keep an eye on live beads and report blockers.
""",
        encoding="utf-8",
    )

    spawned = []
    update_calls = []

    class DummyThread:
        def __init__(self, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon

        def start(self):
            spawned.append((self.name, self.daemon))
            self.target(*self.args)

    class DummyHeartbeatStop:
        def set(self):
            pass

    class DummyHeartbeatThread:
        def join(self, timeout=None):
            pass

    def fake_start_worker_heartbeat(issue_id, worker_name, *, lease_seconds=0, heartbeat_interval_seconds=0):
        return DummyHeartbeatStop(), DummyHeartbeatThread()

    class DummyAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_conversation(self, prompt):
            return {"final_response": "[SILENT]"}

    def fake_run(cmd, *args, **kwargs):
        if cmd[1:3] == ["list", "--json"]:
            payload = {
                "issues": [
                    {
                        "id": "bd-123",
                        "assignee": {"name": "planner"},
                        "status": "in_progress",
                    }
                ]
            }
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(payload), stderr="")
        if len(cmd) > 1 and cmd[1] == "update":
            update_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(live_workers, "LIVE_WORKERS_FILE", live_file)
    monkeypatch.setattr(live_workers, "start_live_worker_poller", lambda interval_seconds=None: False)
    monkeypatch.setattr(live_workers.threading, "Thread", DummyThread)
    monkeypatch.setattr(live_workers, "_start_worker_heartbeat", fake_start_worker_heartbeat)
    monkeypatch.setattr(live_workers.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(run_agent, "AIAgent", DummyAgent)
    monkeypatch.setattr(live_workers.subprocess, "run", fake_run)

    result = live_workers.trigger_live_workers_once("manual")

    assert result["ok"] is True
    assert result["spawned_workers"] == ["planner"]
    assert spawned == [("live-worker-planner", True)]
    assert update_calls
    assert any("claimed_at" in part for part in update_calls[0])
    assert any("lease_expires_at" in part for part in update_calls[0])

@pytest.mark.asyncio
async def test_trigger_live_workers_once_skips_bd_lookup_when_manifest_missing(tmp_path, monkeypatch):
    missing_live_file = tmp_path / "LIVE_WORKERS.md"
    monkeypatch.setattr(live_workers, "LIVE_WORKERS_FILE", missing_live_file)
    monkeypatch.setattr(
        live_workers,
        "_ready_assignees_from_bd",
        lambda: (_ for _ in ()).throw(AssertionError("bd lookup should not run without a manifest")),
    )

    result = live_workers.trigger_live_workers_once("manual")

    assert result["reason"] == "manifest_missing"
    assert result["spawned_workers"] == []


@pytest.mark.asyncio
async def test_handle_skips_workers_without_matching_ready_assignees(tmp_path, monkeypatch):
    live_file = tmp_path / "LIVE_WORKERS.md"
    live_file.write_text(
        """## Worker: planner
provider=openai-codex
model=gpt-5.4-mini
session_id=live-worker-planner

Keep an eye on live beads and report blockers.
""",
        encoding="utf-8",
    )

    spawned = []

    class DummyThread:
        def __init__(self, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon

        def start(self):
            spawned.append((self.name, self.daemon))
            self.target(*self.args)

    monkeypatch.setattr(live_workers, "LIVE_WORKERS_FILE", live_file)
    monkeypatch.setattr(live_workers, "start_live_worker_poller", lambda interval_seconds=None: False)
    monkeypatch.setattr(live_workers.threading, "Thread", DummyThread)
    monkeypatch.setattr(live_workers.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        live_workers.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"issues": [{"assignee": {"name": "not-planner"}}]}),
            stderr="",
        ),
    )

    await live_workers.handle("gateway:startup", {})

    assert spawned == []


@pytest.mark.asyncio
async def test_handle_retries_transient_worker_failures(tmp_path, monkeypatch):
    live_file = tmp_path / "LIVE_WORKERS.md"
    live_file.write_text(
        """## Worker: planner
provider=openai-codex
model=gpt-5.4-mini
session_id=live-worker-planner

Keep an eye on live beads and report blockers.
""",
        encoding="utf-8",
    )

    attempts = {"count": 0}
    sleep_calls = []

    class FlakyAgent:
        def __init__(self, **kwargs):
            attempts["count"] += 1
            self._attempt = attempts["count"]

        def run_conversation(self, prompt):
            if self._attempt == 1:
                raise RuntimeError("temporary auth failure")
            return {"final_response": "[SILENT]"}

    class DummyThread:
        def __init__(self, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(live_workers, "LIVE_WORKERS_FILE", live_file)
    monkeypatch.setattr(live_workers, "start_live_worker_poller", lambda interval_seconds=None: False)
    monkeypatch.setattr(run_agent, "AIAgent", FlakyAgent)
    monkeypatch.setattr(live_workers.threading, "Thread", DummyThread)
    monkeypatch.setattr(live_workers.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr(
        live_workers.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"issues": [{"assignee": {"name": "planner"}}]}),
            stderr="",
        ),
    )

    await live_workers.handle("gateway:startup", {})

    assert attempts["count"] == 2
    assert sleep_calls == [5]


def test_poll_live_workers_loop_triggers_until_stopped(monkeypatch):
    calls = []

    monkeypatch.setattr(
        live_workers,
        "trigger_live_workers_once",
        lambda event_type="manual": calls.append(event_type),
    )

    class StopEvent:
        def __init__(self):
            self.wait_calls = []

        def wait(self, seconds):
            self.wait_calls.append(seconds)
            return True

    stop_event = StopEvent()

    live_workers._poll_live_workers_loop(stop_event=stop_event, interval_seconds=12)

    assert calls == ["poll"]
    assert stop_event.wait_calls == [12]


def test_start_live_worker_poller_starts_only_once(monkeypatch):
    started = []
    created = []

    class DummyEvent:
        pass

    class DummyThread:
        def __init__(self, target, kwargs=None, name=None, daemon=None):
            self.target = target
            self.kwargs = kwargs or {}
            self.name = name
            self.daemon = daemon
            self.alive = True
            created.append(self)

        def start(self):
            started.append(self.name)

        def is_alive(self):
            return self.alive

    monkeypatch.setattr(live_workers, "_live_worker_poller_thread", None)
    monkeypatch.setattr(live_workers, "_live_worker_poller_stop_event", None)
    monkeypatch.setattr(live_workers.threading, "Event", DummyEvent)
    monkeypatch.setattr(live_workers.threading, "Thread", DummyThread)

    assert live_workers.start_live_worker_poller(interval_seconds=7) is True
    assert live_workers.start_live_worker_poller(interval_seconds=7) is False
    assert started == ["live-worker-poller"]
    assert created[0].kwargs["interval_seconds"] == 7


@pytest.mark.asyncio
async def test_start_live_worker_poller_can_detect_pytest_without_nameerror(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(live_workers, "_live_worker_poller_thread", None)
    monkeypatch.setattr(live_workers, "_live_worker_poller_stop_event", None)

    result = live_workers.start_live_worker_poller(allow_when_testing=False)

    assert result is False


@pytest.mark.asyncio
async def test_handle_silently_skips_missing_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(live_workers, "LIVE_WORKERS_FILE", tmp_path / "missing.md")
    monkeypatch.setattr(live_workers, "start_live_worker_poller", lambda interval_seconds=None: False)
    monkeypatch.setattr(
        live_workers.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"issues": []}),
            stderr="",
        ),
    )

    # Should not raise.
    await live_workers.handle("gateway:startup", {})
