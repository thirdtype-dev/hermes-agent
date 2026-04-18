---
sidebar_position: 7
title: "Live Workers"
description: "Auto-spawn background Hermes workers from a LIVE_WORKERS.md manifest on gateway startup"
---

# Live Workers

Hermes can auto-spawn background workers from a `LIVE_WORKERS.md` manifest in the active profile's `HERMES_HOME`.

This happens on gateway startup, so it works best when the gateway is running as a persistent service:

```bash
hermes -p coder gateway install
```

`hermes -p coder` selects the profile, but it does not start the gateway by itself.

## Manifest location

Place the manifest here:

```text
~/.hermes/LIVE_WORKERS.md
```

For a named profile, that becomes:

```text
~/.hermes/profiles/<name>/LIVE_WORKERS.md
```

## Format

Each worker starts with a heading and then optional metadata lines, followed by the prompt text:

```markdown
## Worker: planner
provider=openai-codex
model=gpt-5.4-mini
max_iterations=40
quiet_mode=true
skip_context_files=true
skip_memory=true
session_id=live-worker-planner

Keep an eye on live beads and report blockers.
```

## Notes

- The gateway reads the manifest on startup.
- Each `## Worker: ...` block becomes one background worker thread.
- If a worker fails to start, Hermes retries with backoff.
- If the worker finishes normally, it stops; the auto-start happens again the next time the gateway starts.
- The built-in `bd ready --json` lookup will use `BEADS_DIR` when set; otherwise it auto-tries `/data/.beads` before falling back to the profile-local `.beads`.

## Recommended setup

For an always-on worker on a profile:

1. Put `LIVE_WORKERS.md` inside that profile's `HERMES_HOME`.
2. Install or run that profile's gateway service.
3. Keep the manifest conservative: one worker per job, explicit model/provider, and a short prompt.

If you need multiple workers, add multiple `## Worker: ...` blocks.
