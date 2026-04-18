"""Gateway test fixtures and cleanup helpers."""

from __future__ import annotations

import pytest

from gateway.platforms.api_server import APIServerAdapter, ResponseStore


@pytest.fixture(autouse=True)
def _isolate_pairing_store(monkeypatch, tmp_path):
    """Keep pairing-state files test-local so rate limits never leak across tests."""
    monkeypatch.setattr("gateway.pairing.PAIRING_DIR", tmp_path / "pairing")


@pytest.fixture(autouse=True)
def _close_api_server_response_stores(request):
    """Ensure API server SQLite connections are released after each test.

    APIServerAdapter instances are created frequently across the gateway suite
    and some tests keep the adapter alive via aiohttp app state or fixture
    scopes long enough for sqlite file descriptors to accumulate under xdist.
    Closing the ResponseStore explicitly keeps the suite deterministic.
    """
    yield

    for value in getattr(request.node, "funcargs", {}).values():
        try:
            if isinstance(value, APIServerAdapter):
                value.close()
            elif isinstance(value, ResponseStore):
                value.close()
        except Exception:
            pass
