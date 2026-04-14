"""Built-in boot-md hook — run ~/.hermes/BOOT.md on gateway startup.

This hook is always registered. It silently skips if no BOOT.md exists.
To activate, create ``~/.hermes/BOOT.md`` with instructions for the
agent to execute on every gateway restart.

Example BOOT.md::

    # Startup Checklist

    1. Check if any cron jobs failed overnight
    2. Send a status update to Discord #general
    3. If there are errors in /opt/app/deploy.log, summarize them

The agent runs in a background thread so it doesn't block gateway
startup. If nothing needs attention, it replies with [SILENT] to
suppress delivery.
"""

import logging
import threading

logger = logging.getLogger("hooks.boot-md")

from hermes_cli.config import load_config
from hermes_cli.runtime_provider import resolve_runtime_provider
from hermes_constants import get_hermes_home
HERMES_HOME = get_hermes_home()
BOOT_FILE = HERMES_HOME / "BOOT.md"


def _build_boot_prompt(content: str) -> str:
    """Wrap BOOT.md content in a system-level instruction."""
    return (
        "You are running a startup boot checklist. Follow the BOOT.md "
        "instructions below exactly.\n\n"
        "---\n"
        f"{content}\n"
        "---\n\n"
        "Execute each instruction. If you need to send a message to a "
        "platform, use the send_message tool.\n"
        "If nothing needs attention and there is nothing to report, "
        "reply with ONLY: [SILENT]"
    )


def _resolve_boot_runtime() -> dict:
    """Resolve the main runtime before constructing the boot agent.

    AIAgent decides api_mode during __init__ from the constructor kwargs.
    If we let it start with defaults and only resolve the client later, a
    codex-configured main provider can end up on /chat/completions with an
    empty model. Resolve the runtime first so the agent sees a consistent
    model/provider/base_url/api_mode tuple up front.
    """
    config = load_config()
    model_cfg = config.get("model") if isinstance(config.get("model"), dict) else {}
    requested_provider = str(model_cfg.get("provider") or "").strip() or None
    runtime = resolve_runtime_provider(requested=requested_provider)
    model = str(model_cfg.get("default") or runtime.get("model") or "").strip()
    if model:
        runtime["model"] = model
    return runtime


def _run_boot_agent(content: str) -> None:
    """Spawn a one-shot agent session to execute the boot instructions."""
    try:
        from run_agent import AIAgent

        prompt = _build_boot_prompt(content)
        runtime = _resolve_boot_runtime()
        agent = AIAgent(
            model=str(runtime.get("model") or ""),
            provider=str(runtime.get("provider") or ""),
            base_url=str(runtime.get("base_url") or ""),
            api_mode=str(runtime.get("api_mode") or ""),
            api_key=str(runtime.get("api_key") or ""),
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            max_iterations=20,
        )
        result = agent.run_conversation(prompt)
        response = result.get("final_response", "")
        if response and "[SILENT]" not in response:
            logger.info("boot-md completed: %s", response[:200])
        else:
            logger.info("boot-md completed (nothing to report)")
    except Exception as e:
        logger.error("boot-md agent failed: %s", e)


async def handle(event_type: str, context: dict) -> None:
    """Gateway startup handler — run BOOT.md if it exists."""
    if not BOOT_FILE.exists():
        return

    content = BOOT_FILE.read_text(encoding="utf-8").strip()
    if not content:
        return

    logger.info("Running BOOT.md (%d chars)", len(content))

    # Run in a background thread so we don't block gateway startup.
    thread = threading.Thread(
        target=_run_boot_agent,
        args=(content,),
        name="boot-md",
        daemon=True,
    )
    thread.start()
