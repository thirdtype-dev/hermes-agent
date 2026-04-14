"""Built-in live-workers hook — zero-downtime sync of background AIAgent workers.

This hook is intentionally file-driven. If ``~/.hermes/LIVE_WORKERS.md`` exists,
it is parsed as a list of worker sections and each worker is managed in its own
background thread.

Sync rules:
- Each worker section starts with ``## Worker: <name>``.
- Optional ``key=value`` metadata lines may appear immediately below the header.
- The first blank line ends metadata and begins the prompt body.
- Unknown metadata keys are ignored.
- When the hook runs again, new/changed workers are started first and then
  replaced/removed workers are interrupted. That gives a generation-based,
  zero-downtime swap instead of a gateway restart.
- If no file exists, the hook silently skips.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from hermes_cli.config import get_hermes_home

logger = logging.getLogger("hooks.live-workers")

HERMES_HOME = get_hermes_home()
WORKERS_FILE = HERMES_HOME / "LIVE_WORKERS.md"

_WORKER_HEADER_RE = re.compile(r"^##\s*Worker:\s*(.+?)\s*$", re.IGNORECASE)
_WORKER_STATE_LOCK = threading.RLock()


@dataclass
class LiveWorkerRuntime:
    name: str
    signature: str
    thread: threading.Thread | None = None
    agent_ref: list[Any] = field(default_factory=lambda: [None])
    started_at: float = 0.0
    status: str = "starting"
    interrupt_requested: bool = False
    interrupt_reason: str | None = None


_ACTIVE_WORKERS: Dict[str, LiveWorkerRuntime] = {}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-._")
    return slug or "worker"


def _parse_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _worker_signature(worker: Dict[str, Any]) -> str:
    payload = {
        "name": str(worker.get("name", "")).strip(),
        "meta": worker.get("meta", {}) or {},
        "prompt": str(worker.get("prompt", "")).strip(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_workers(content: str) -> List[Dict[str, Any]]:
    workers: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    in_prompt = False

    def finish_current() -> None:
        nonlocal current
        if current is None:
            return
        prompt = "\n".join(current.get("prompt_lines", [])).strip()
        if prompt:
            current["prompt"] = prompt
            workers.append(current)
        current = None

    for raw_line in content.splitlines():
        header = _WORKER_HEADER_RE.match(raw_line)
        if header:
            finish_current()
            current = {
                "name": header.group(1).strip(),
                "meta": {},
                "prompt_lines": [],
            }
            in_prompt = False
            continue

        if current is None:
            continue

        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if not in_prompt:
            if not stripped:
                in_prompt = True
                continue
            if "=" in stripped and not stripped.startswith("#"):
                key, value = stripped.split("=", 1)
                current["meta"][key.strip().lower()] = value.strip()
                continue
            in_prompt = True

        if in_prompt:
            current["prompt_lines"].append(line)

    finish_current()
    return workers


def _prune_dead_workers_locked() -> None:
    for name, runtime in list(_ACTIVE_WORKERS.items()):
        thread = runtime.thread
        if thread is None or not thread.is_alive():
            _ACTIVE_WORKERS.pop(name, None)


def _interrupt_runtime(runtime: LiveWorkerRuntime, reason: str) -> bool:
    runtime.interrupt_requested = True
    runtime.interrupt_reason = reason
    agent = runtime.agent_ref[0]
    if agent is None:
        logger.info("live-worker %s interrupt queued before agent start: %s", runtime.name, reason)
        runtime.status = "interrupt-queued"
        return False

    try:
        agent.interrupt(reason)
        runtime.status = "interrupting"
        return True
    except Exception as e:
        logger.warning("live-worker %s interrupt failed: %s", runtime.name, e)
        return False


def _run_live_worker(name: str, prompt: str, meta: Dict[str, Any], runtime: LiveWorkerRuntime) -> None:
    """Spawn one AIAgent worker and let it execute its startup prompt."""
    try:
        from run_agent import AIAgent

        kwargs: Dict[str, Any] = {
            "quiet_mode": _parse_bool(meta.get("quiet_mode"), True),
            "skip_context_files": _parse_bool(meta.get("skip_context_files"), True),
            "skip_memory": _parse_bool(meta.get("skip_memory"), True),
        }

        model = str(meta.get("model") or "").strip()
        if model:
            kwargs["model"] = model

        provider = str(meta.get("provider") or "").strip()
        if provider:
            kwargs["provider"] = provider

        api_mode = str(meta.get("api_mode") or "").strip()
        if api_mode:
            kwargs["api_mode"] = api_mode

        base_url = str(meta.get("base_url") or "").strip()
        if base_url:
            kwargs["base_url"] = base_url

        api_key = str(meta.get("api_key") or "").strip()
        if api_key:
            kwargs["api_key"] = api_key

        max_iterations = _parse_int(meta.get("max_iterations", "20"), 20)
        if max_iterations > 0:
            kwargs["max_iterations"] = max_iterations

        session_id = str(meta.get("session_id") or "").strip()
        if not session_id:
            worker_id = _slugify(name)
            session_id = f"live_worker_{worker_id}"
        kwargs["session_id"] = session_id
        kwargs["platform"] = str(meta.get("platform") or "gateway").strip() or "gateway"

        agent = AIAgent(**kwargs)
        runtime.agent_ref[0] = agent
        runtime.status = "running"

        if runtime.interrupt_requested:
            agent.interrupt(runtime.interrupt_reason or "Live worker replacement requested")

        result = agent.run_conversation(prompt)
        response = result.get("final_response", "") if isinstance(result, dict) else ""
        runtime.status = "finished"
        if response and "[SILENT]" not in response:
            logger.info("live-worker %s completed: %s", name, response[:200])
        else:
            logger.info("live-worker %s completed (silent)", name)
    except Exception as e:
        runtime.status = "failed"
        logger.error("live-worker %s failed: %s", name, e)
    finally:
        runtime.agent_ref[0] = None
        with _WORKER_STATE_LOCK:
            current = _ACTIVE_WORKERS.get(name)
            if current is runtime:
                _ACTIVE_WORKERS.pop(name, None)


def _spawn_runtime(worker: Dict[str, Any]) -> LiveWorkerRuntime:
    name = str(worker.get("name", "worker")).strip() or "worker"
    prompt = str(worker.get("prompt", "")).strip()
    meta = worker.get("meta", {}) or {}
    signature = _worker_signature(worker)
    runtime = LiveWorkerRuntime(name=name, signature=signature, started_at=time.time())

    thread = threading.Thread(
        target=_run_live_worker,
        args=(name, prompt, meta, runtime),
        name=f"live-worker-{_slugify(name)}-{int(runtime.started_at * 1000)}",
        daemon=True,
    )
    runtime.thread = thread

    with _WORKER_STATE_LOCK:
        _ACTIVE_WORKERS[name] = runtime

    thread.start()
    return runtime


def _sync_workers(workers: List[Dict[str, Any]], *, event_type: str) -> Dict[str, Any]:
    desired_by_name: Dict[str, Dict[str, Any]] = {}
    for worker in workers:
        name = str(worker.get("name", "")).strip()
        prompt = str(worker.get("prompt", "")).strip()
        if not name or not prompt:
            continue
        desired_by_name[name] = worker

    with _WORKER_STATE_LOCK:
        _prune_dead_workers_locked()
        current_snapshot = dict(_ACTIVE_WORKERS)

    started: List[str] = []
    kept: List[str] = []
    replaced: List[str] = []
    removed: List[str] = []
    interrupted: List[str] = []
    failed: List[str] = []
    old_runtimes: List[tuple[LiveWorkerRuntime, str]] = []

    for name, worker in desired_by_name.items():
        current = current_snapshot.get(name)
        signature = _worker_signature(worker)
        current_alive = bool(current and current.thread and current.thread.is_alive())
        current_stable = current_alive and not current.interrupt_requested and current.signature == signature

        if current_stable:
            kept.append(name)
            continue

        try:
            _spawn_runtime(worker)
            started.append(name)
            if current_alive:
                replaced.append(name)
                old_runtimes.append((current, f"Live worker '{name}' reloaded via {event_type}"))
        except Exception as e:
            failed.append(f"{name}: {e}")

    for name, current in current_snapshot.items():
        if name in desired_by_name:
            continue
        if current.thread and current.thread.is_alive():
            removed.append(name)
            old_runtimes.append((current, f"Live worker '{name}' removed from LIVE_WORKERS.md"))

    for runtime, reason in old_runtimes:
        if _interrupt_runtime(runtime, reason):
            interrupted.append(runtime.name)

    return {
        "event_type": event_type,
        "started": started,
        "kept": kept,
        "replaced": replaced,
        "removed": removed,
        "interrupted": interrupted,
        "failed": failed,
    }


async def handle(event_type: str, context: dict) -> Dict[str, Any]:
    """Sync worker threads from LIVE_WORKERS.md."""
    if not WORKERS_FILE.exists():
        return {
            "event_type": event_type,
            "started": [],
            "kept": [],
            "replaced": [],
            "removed": [],
            "interrupted": [],
            "failed": [],
        }

    content = WORKERS_FILE.read_text(encoding="utf-8").strip()
    if not content:
        return {
            "event_type": event_type,
            "started": [],
            "kept": [],
            "replaced": [],
            "removed": [],
            "interrupted": [],
            "failed": [],
        }

    workers = _parse_workers(content)
    if not workers:
        logger.info("LIVE_WORKERS.md present but no worker sections found")
        return {
            "event_type": event_type,
            "started": [],
            "kept": [],
            "replaced": [],
            "removed": [],
            "interrupted": [],
            "failed": [],
        }

    summary = _sync_workers(workers, event_type=event_type)
    logger.info(
        "Synced %d live worker(s): started=%s kept=%s replaced=%s removed=%s interrupted=%s failed=%s",
        len(workers),
        summary["started"],
        summary["kept"],
        summary["replaced"],
        summary["removed"],
        summary["interrupted"],
        summary["failed"],
    )
    return summary
