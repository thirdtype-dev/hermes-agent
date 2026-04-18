"""Built-in live-workers hook — assign ready beads to live workers.

This hook is intended to auto-spawn file-driven live workers without needing
manual delegation. The manifest format is intentionally conservative:

    ## Worker: planner
    assignee=planner
    provider=openai-codex
    model=gpt-5.4-mini
    max_iterations=40
    quiet_mode=true
    skip_context_files=true
    skip_memory=true
    session_id=live-worker-planner

    Keep an eye on live beads and report blockers.

The hook reads ``bd ready --json`` first, builds the set of ready assignees,
and only spawns workers whose ``assignee``/``assignees`` metadata matches a
ready bead. A startup/session-start call kicks off a background poller so the
same matching logic keeps running periodically, and each ``## Worker: ...``
block becomes one background thread when selected. Metadata lines are parsed
until the first blank line, after which the remaining block becomes the worker
prompt.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from hermes_constants import get_hermes_home

logger = logging.getLogger("hooks.live-workers")

HERMES_HOME = get_hermes_home()
LIVE_WORKERS_FILE = HERMES_HOME / "LIVE_WORKERS.md"

_BOOL_TRUE = {"true", "1", "yes", "on"}
_BOOL_FALSE = {"false", "0", "no", "off"}
_LIVE_WORKER_RETRY_DELAY_SECONDS = 5
_LIVE_WORKER_MAX_RETRY_DELAY_SECONDS = 60
_LIVE_WORKER_POLL_INTERVAL_SECONDS = 30
_NAS_BEADS_DIR = Path("/data/.beads")
_BD_CANDIDATES = (
    "bd",
    str(Path.home() / ".local/bin/bd"),
    "/opt/homebrew/bin/bd",
    "/usr/local/bin/bd",
)
# Backward-compat test hook: when set, this value takes precedence.
_BEADS_DIR: Path | None = None
_ALT_NAS_BEADS_DIR = Path("/volume1/docker/beads-local-work/.beads")
_PROFILE_LOCAL_BEADS_DIR = HERMES_HOME / ".beads"

_live_worker_registry_lock = threading.Lock()
_active_live_workers: set[str] = set()
_live_worker_poller_lock = threading.Lock()
_live_worker_poller_thread: threading.Thread | None = None
_live_worker_poller_stop_event: threading.Event | None = None


def _bd_binary() -> str:
    for candidate in _BD_CANDIDATES:
        if candidate == "bd":
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
            continue
        path = Path(candidate)
        if path.exists() and path.is_file():
            return str(path)
    return "bd"


def _bd_ready_env() -> dict[str, str]:
    env = os.environ.copy()
    env["BEADS_DIR"] = str(_resolve_beads_dir())
    return env


def _resolve_beads_dir() -> Path:
    """Resolve Beads DB directory deterministically.

    Priority:
      1) Explicit BEADS_DIR env var (if it points to an existing directory)
      2) Legacy test override via module-level _BEADS_DIR
      3) NAS default /data/.beads
      4) Synology alt path /volume1/docker/beads-local-work/.beads
      5) Profile-local ~/.hermes/.beads
      6) Explicit BEADS_DIR value even when missing (surface config errors)
      7) Profile-local fallback
    """
    env_value = (os.getenv("BEADS_DIR") or "").strip()
    if env_value:
        env_path = Path(env_value).expanduser()
        if env_path.is_dir():
            return env_path

    if _BEADS_DIR is not None and Path(_BEADS_DIR).is_dir():
        return Path(_BEADS_DIR)

    for candidate in (_NAS_BEADS_DIR, _ALT_NAS_BEADS_DIR, _PROFILE_LOCAL_BEADS_DIR):
        if candidate.is_dir():
            return candidate

    if env_value:
        return Path(env_value).expanduser()
    return _PROFILE_LOCAL_BEADS_DIR


def _parse_boolish(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
    return default


def _parse_intish(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _parse_metadata_value(raw: str) -> Any:
    """Best-effort parsing for manifest metadata values."""
    if not isinstance(raw, str):
        return raw

    stripped = raw.strip()
    if not stripped:
        return ""

    lowered = stripped.lower()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False

    try:
        return ast.literal_eval(stripped)
    except (ValueError, SyntaxError):
        return stripped


def parse_live_workers_manifest(content: str) -> List[Dict[str, Any]]:
    """Parse LIVE_WORKERS.md into worker definitions.

    A worker block starts with ``## Worker: <name>``. Metadata lines follow until
    the first blank line. The rest of the block is treated as the worker prompt.
    """
    workers: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    prompt_lines: List[str] = []
    in_prompt = False

    def flush_current() -> None:
        nonlocal current, prompt_lines, in_prompt
        if not current:
            return
        prompt = "\n".join(prompt_lines).strip()
        current["prompt"] = prompt
        workers.append(current)
        current = None
        prompt_lines = []
        in_prompt = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("## Worker:"):
            flush_current()
            name = stripped.split(":", 1)[1].strip()
            if not name:
                continue
            current = {
                "name": name,
                "metadata": {},
                "prompt": "",
            }
            continue

        if current is None:
            continue

        if not in_prompt:
            if stripped == "":
                in_prompt = True
                continue

            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key:
                    current["metadata"][key] = _parse_metadata_value(value)
                    continue

            in_prompt = True

        prompt_lines.append(line)

    flush_current()
    return workers


def _iter_issue_records(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("issues", "items", "results", "nodes"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    data = payload.get("data")
    if isinstance(data, dict):
        return _iter_issue_records(data)
    return []


def _normalize_assignee_tokens(value: Any) -> List[str]:
    tokens: List[str] = []
    if value is None:
        return tokens
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        return [part for part in parts if part]
    if isinstance(value, dict):
        for key in ("name", "username", "login", "handle", "slug", "email", "id"):
            nested = value.get(key)
            tokens.extend(_normalize_assignee_tokens(nested))
            if tokens:
                return tokens
        for nested in value.values():
            tokens.extend(_normalize_assignee_tokens(nested))
        return tokens
    if isinstance(value, list):
        for item in value:
            tokens.extend(_normalize_assignee_tokens(item))
    return [token for token in tokens if token]


def _ready_assignees_from_bd() -> set[str]:
    try:
        completed = subprocess.run(
            [_bd_binary(), "ready", "--json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            env=_bd_ready_env(),
        )
    except FileNotFoundError:
        logger.warning("bd command not found; skipping live worker spawn")
        return set()
    except subprocess.TimeoutExpired:
        logger.warning("bd ready --json timed out; skipping live worker spawn")
        return set()
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or exc.stdout or "").strip()
        if stderr:
            logger.warning("bd ready --json failed: %s", stderr)
        else:
            logger.warning("bd ready --json failed with exit code %s", exc.returncode)
        return set()

    raw_output = (completed.stdout or "").strip()
    if not raw_output:
        return set()

    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse bd ready --json output: %s", exc)
        return set()

    assignees: set[str] = set()
    for issue in _iter_issue_records(payload):
        for field in ("assignee", "assignees", "assigned_to", "assignedTo", "owner", "owners"):
            assignees.update(_normalize_assignee_tokens(issue.get(field)))

        nested_user = issue.get("user")
        assignees.update(_normalize_assignee_tokens(nested_user))

    return {assignee for assignee in assignees if assignee}


def _worker_assignment_keys(worker: Dict[str, Any]) -> set[str]:
    metadata = worker.get("metadata", {})
    keys: set[str] = set()
    for field in ("assignee", "assignees"):
        keys.update(_normalize_assignee_tokens(metadata.get(field)))
    if not keys:
        keys.add(worker.get("name", "").strip())
    return {key for key in keys if key}


def _build_live_worker_prompt(worker: Dict[str, Any]) -> str:
    metadata = worker.get("metadata", {})
    lines = [
        f"You are Hermes live worker '{worker['name']}'. Follow the instructions below exactly.",
        "",
        "---",
    ]

    for key in ("provider", "model", "api_mode", "base_url", "api_key", "session_id"):
        if key in metadata and metadata[key] not in (None, ""):
            lines.append(f"{key}={metadata[key]}")

    lines.append("---")
    lines.append("")
    if worker.get("prompt"):
        lines.append(worker["prompt"])
    else:
        lines.append("[SILENT]")
    return "\n".join(lines)


def _worker_key(worker: Dict[str, Any]) -> str:
    return str(worker.get("name", "")).strip()


def _mark_worker_active(worker_name: str) -> bool:
    worker_name = worker_name.strip()
    if not worker_name:
        return False
    with _live_worker_registry_lock:
        if worker_name in _active_live_workers:
            return False
        _active_live_workers.add(worker_name)
        return True


def _release_worker_active(worker_name: str) -> None:
    worker_name = worker_name.strip()
    if not worker_name:
        return
    with _live_worker_registry_lock:
        _active_live_workers.discard(worker_name)


def _spawn_worker_with_registry(worker: Dict[str, Any]) -> None:
    worker_name = _worker_key(worker)
    try:
        _spawn_worker(worker)
    finally:
        _release_worker_active(worker_name)


def _spawn_worker(worker: Dict[str, Any]) -> None:
    """Spawn one worker in a background thread.

    If the worker fails during startup or execution, keep retrying with a small
    backoff so gateway startup can still converge to a live worker once the
    underlying provider/auth/runtime issue clears.
    """
    metadata = dict(worker.get("metadata", {}))
    session_id = metadata.get("session_id") or f"live-worker-{worker['name']}"
    quiet_mode = _parse_boolish(metadata.get("quiet_mode"), default=True)
    skip_context_files = _parse_boolish(metadata.get("skip_context_files"), default=True)
    skip_memory = _parse_boolish(metadata.get("skip_memory"), default=True)
    max_iterations = _parse_intish(metadata.get("max_iterations"), default=20)
    platform = metadata.get("platform") or "gateway"

    agent_kwargs = {
        "model": metadata.get("model") or "",
        "provider": metadata.get("provider") or None,
        "api_mode": metadata.get("api_mode") or None,
        "base_url": metadata.get("base_url") or None,
        "api_key": metadata.get("api_key") or None,
        "max_iterations": max_iterations,
        "quiet_mode": quiet_mode,
        "skip_context_files": skip_context_files,
        "skip_memory": skip_memory,
        "session_id": session_id,
        "platform": platform,
    }

    # Keep only explicit non-empty values.
    agent_kwargs = {k: v for k, v in agent_kwargs.items() if v not in (None, "")}
    prompt = _build_live_worker_prompt(worker)
    retry_delay = _LIVE_WORKER_RETRY_DELAY_SECONDS

    while True:
        try:
            from run_agent import AIAgent

            logger.info(
                "Spawning live worker '%s' (session_id=%s, model=%s, provider=%s)",
                worker["name"],
                session_id,
                agent_kwargs.get("model", ""),
                agent_kwargs.get("provider", ""),
            )

            agent = AIAgent(**agent_kwargs)
            result = agent.run_conversation(prompt)
            response = ""
            if isinstance(result, dict):
                response = str(result.get("final_response", "") or "")
            elif result is not None:
                response = str(result)

            if response and "[SILENT]" not in response:
                logger.info("live worker '%s' completed: %s", worker["name"], response[:200])
            else:
                logger.info("live worker '%s' completed (nothing to report)", worker["name"])
            return
        except Exception as e:
            logger.error(
                "live worker '%s' failed: %s; retrying in %ds",
                worker.get("name", "<unknown>"),
                e,
                retry_delay,
            )
            time.sleep(retry_delay)
            retry_delay = min(_LIVE_WORKER_MAX_RETRY_DELAY_SECONDS, retry_delay * 2)


async def handle(event_type: str, context: dict) -> None:
    """Gateway startup/session-start handler — run LIVE_WORKERS.md if it exists."""
    start_live_worker_poller()
    trigger_live_workers_once(event_type=event_type)


def _poll_live_workers_loop(
    *,
    stop_event: threading.Event | None = None,
    interval_seconds: int | None = None,
) -> None:
    interval = _parse_intish(interval_seconds, default=_LIVE_WORKER_POLL_INTERVAL_SECONDS)
    if interval <= 0:
        interval = _LIVE_WORKER_POLL_INTERVAL_SECONDS

    while True:
        trigger_live_workers_once(event_type="poll")
        if stop_event is not None:
            if stop_event.wait(interval):
                return
        else:
            time.sleep(interval)


def start_live_worker_poller(
    interval_seconds: int | None = None,
    *,
    allow_when_testing: bool = True,
) -> bool:
    """Start the background poller once per process.

    Returns True when a new poller thread is started and False when a poller is
    already running.
    """
    if not allow_when_testing and (os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules):
        return False

    global _live_worker_poller_thread, _live_worker_poller_stop_event

    with _live_worker_poller_lock:
        thread = _live_worker_poller_thread
        if thread is not None and getattr(thread, "is_alive", lambda: False)():
            return False

        stop_event = threading.Event()
        thread = threading.Thread(
            target=_poll_live_workers_loop,
            kwargs={"stop_event": stop_event, "interval_seconds": interval_seconds},
            name="live-worker-poller",
            daemon=True,
        )
        _live_worker_poller_thread = thread
        _live_worker_poller_stop_event = stop_event
        thread.start()
        return True


def trigger_live_workers_once(event_type: str = "manual") -> Dict[str, Any]:
    """Best-effort live-worker spawn pass.

    Returns a diagnostic payload so callers (e.g. delegation recovery paths)
    can surface what happened.
    """
    beads_dir = _resolve_beads_dir()
    # Keep process-level default aligned so downstream worker actions and
    # terminal calls inherit the same Beads DB target within this process.
    os.environ["BEADS_DIR"] = str(beads_dir)

    if not LIVE_WORKERS_FILE.exists():
        logger.info("[%s] LIVE_WORKERS.md not found; skipping live workers", event_type)
        return {
            "ok": False,
            "reason": "manifest_missing",
            "event": event_type,
            "beads_dir": str(beads_dir),
            "spawned_workers": [],
        }

    ready_assignees = _ready_assignees_from_bd()
    if not ready_assignees:
        logger.info("[%s] No ready beads were assigned to live workers", event_type)
        return {
            "ok": False,
            "reason": "no_ready_assignees",
            "event": event_type,
            "beads_dir": str(beads_dir),
            "spawned_workers": [],
        }

    content = LIVE_WORKERS_FILE.read_text(encoding="utf-8").strip()
    if not content:
        logger.info("[%s] LIVE_WORKERS.md was empty; skipping live workers", event_type)
        return {
            "ok": False,
            "reason": "manifest_empty",
            "event": event_type,
            "beads_dir": str(beads_dir),
            "spawned_workers": [],
        }

    workers = parse_live_workers_manifest(content)
    if not workers:
        logger.info("[%s] LIVE_WORKERS.md had no valid worker blocks", event_type)
        return {
            "ok": False,
            "reason": "manifest_invalid",
            "event": event_type,
            "beads_dir": str(beads_dir),
            "spawned_workers": [],
        }

    selected_workers = []
    for worker in workers:
        assignment_keys = _worker_assignment_keys(worker)
        if assignment_keys & ready_assignees:
            selected_workers.append(worker)
        else:
            logger.info(
                "Skipping live worker '%s' — no ready bead assignee matched %s",
                worker["name"],
                sorted(assignment_keys),
            )

    if not selected_workers:
        logger.info(
            "[%s] No live workers matched ready bead assignees: %s",
            event_type,
            sorted(ready_assignees),
        )
        return {
            "ok": False,
            "reason": "no_matching_workers",
            "event": event_type,
            "beads_dir": str(beads_dir),
            "ready_assignees": sorted(ready_assignees),
            "spawned_workers": [],
        }

    logger.info(
        "[%s] Running LIVE_WORKERS.md (%d selected worker(s) from %d defined)",
        event_type,
        len(selected_workers),
        len(workers),
    )

    spawned: List[str] = []
    for worker in selected_workers:
        worker_name = _worker_key(worker)
        if not _mark_worker_active(worker_name):
            logger.info("Skipping live worker '%s' — already active", worker_name)
            continue

        thread = threading.Thread(
            target=_spawn_worker_with_registry,
            args=(worker,),
            name=f"live-worker-{worker['name']}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            _release_worker_active(worker_name)
            raise
        spawned.append(worker["name"])

    return {
        "ok": True,
        "reason": "spawned",
        "event": event_type,
        "beads_dir": str(beads_dir),
        "ready_assignees": sorted(ready_assignees),
        "spawned_workers": spawned,
    }
