"""Tests for the built-in live-workers startup hook."""

import pytest
from unittest.mock import patch

from gateway.builtin_hooks import live_workers


class DummyThread:
    created = []

    def __init__(self, target, args=(), name=None, daemon=None):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        DummyThread.created.append(self)

    def start(self):
        return None


class TestLiveWorkersParsing:
    def test_parse_workers_with_metadata_and_prompt(self):
        content = (
            "## Worker: planner\n"
            "model=gpt-5.4-mini\n"
            "max_iterations=40\n"
            "\n"
            "Keep an eye on live beads and report blockers.\n"
            "\n"
            "## Worker: executor\n"
            "\n"
            "Triage the next smallest implementation step and keep it moving.\n"
        )

        workers = live_workers._parse_workers(content)

        assert len(workers) == 2
        assert workers[0]["name"] == "planner"
        assert workers[0]["meta"]["model"] == "gpt-5.4-mini"
        assert workers[0]["meta"]["max_iterations"] == "40"
        assert "live beads" in workers[0]["prompt"]
        assert workers[1]["name"] == "executor"
        assert workers[1]["prompt"] == "Triage the next smallest implementation step and keep it moving."


class TestLiveWorkersHandle:
    @pytest.mark.asyncio
    async def test_handle_spawns_one_thread_per_worker(self, tmp_path):
        workers_file = tmp_path / "LIVE_WORKERS.md"
        workers_file.write_text(
            "## Worker: planner\n"
            "model=gpt-5.4-mini\n"
            "max_iterations=40\n"
            "\n"
            "Keep an eye on live beads and report blockers.\n"
            "\n"
            "## Worker: executor\n"
            "\n"
            "Triage the next smallest implementation step and keep it moving.\n",
            encoding="utf-8",
        )

        DummyThread.created = []
        with patch.object(live_workers, "WORKERS_FILE", workers_file), patch.object(
            live_workers.threading, "Thread", DummyThread
        ):
            await live_workers.handle("gateway:startup", {})

        assert len(DummyThread.created) == 2
        assert DummyThread.created[0].name.startswith("live-worker-planner")
        assert DummyThread.created[0].args[0] == "planner"
        assert "report blockers" in DummyThread.created[0].args[1]
        assert DummyThread.created[0].daemon is True
        assert DummyThread.created[1].name.startswith("live-worker-executor")
        assert DummyThread.created[1].args[0] == "executor"
