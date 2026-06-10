"""Tests for /api/queue routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from neme_anima.server.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    return create_app(state_dir=tmp_path / "state")


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def test_list_queue_empty(client):
    resp = await client.get("/api/queue")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_cancel_unknown_returns_404(client):
    resp = await client.delete("/api/queue/does-not-exist")
    assert resp.status_code == 404


async def test_cancel_running_job_releases_pause(tmp_path):
    """A job parked at the pause gate must be released when cancelled,
    otherwise the worker thread never observes the cancel token and the
    single-worker queue wedges."""
    import asyncio

    from httpx import ASGITransport, AsyncClient

    from neme_anima.server.app import create_app
    from neme_anima.server.queue import JobQueue, JobStatus

    app = create_app(state_dir=tmp_path / "state")

    class FakePausedProgress:
        def __init__(self):
            self.release = asyncio.Event()
            self.resumed = False
            self._paused = True

        @property
        def is_paused(self):
            return self._paused

        def resume(self):
            self.resumed = True
            self._paused = False
            self.release.set()

    progress = FakePausedProgress()

    async def runner(job_id, payload, broadcaster, cancel_token):
        app.state.active_progresses[job_id] = progress
        await progress.release.wait()  # parked "paused" until resumed

    q = JobQueue(runner=runner)
    await q.start()
    app.state.queue = q
    jid = await q.submit({"kind": "extract"})
    for _ in range(200):  # wait until the job is RUNNING and registered
        if jid in app.state.active_progresses:
            break
        await asyncio.sleep(0.01)
    assert jid in app.state.active_progresses

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.delete(f"/api/queue/{jid}")
    assert resp.status_code == 204

    await asyncio.wait_for(q.wait_idle(), timeout=5.0)
    assert progress.resumed
    statuses = {j.job_id: j.status for j in q.snapshot()}
    assert statuses[jid] == JobStatus.CANCELLED
    await q.stop()
