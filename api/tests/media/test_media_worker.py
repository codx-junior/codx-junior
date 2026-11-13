"""Offline tests for the media worker: no network, no AI, no wall clock waiting.

The worker thread is never slept on from a test: the queue is drained through
``process_next`` / ``run_cycle`` and the start/stop test monkeypatches the cycle
so the thread only exists to prove the idempotent guard and the immediate stop.
"""

from types import SimpleNamespace

import pytest

from codx.junior.media import worker as media_worker
from codx.junior.media.budget import BudgetGate
from codx.junior.media.model import MediaJob
from codx.junior.media.store import MediaStore


@pytest.fixture()
def store(tmp_path):
    return MediaStore(tmp_path / ".codx" / "media")


@pytest.fixture(autouse=True)
def clean_worker():
    media_worker.stop_media_worker()
    yield
    media_worker.stop_media_worker()


def make_job(**kwargs):
    payload = dict(
        project="demo",
        kind="cover",
        payload={"title": "Episode 1", "project": "demo", "format": "16:9"},
    )
    payload.update(kwargs)
    return MediaJob(**payload)


def test_start_is_idempotent_and_stop_without_start_is_safe(monkeypatch):
    # Never started: stopping must be a no-op instead of raising
    media_worker.stop_media_worker()

    # The cycle is stubbed so the thread never discovers real projects
    monkeypatch.setattr(media_worker, "run_cycle", lambda *args, **kwargs: 0)

    media_worker.start_media_worker()
    first_thread = media_worker._thread
    assert first_thread is not None
    assert first_thread.is_alive()

    # Second start keeps the very same thread
    media_worker.start_media_worker()
    assert media_worker._thread is first_thread

    media_worker.stop_media_worker()
    assert media_worker._thread is None
    assert media_worker._stop_event is None
    assert not first_thread.is_alive()

    # Stopping twice stays safe
    media_worker.stop_media_worker()


def test_cover_job_is_claimed_and_finished_with_one_asset(store):
    pytest.importorskip("PIL", reason="the local cover provider needs Pillow")

    job = store.put_job(make_job(kind="cover"))

    result = media_worker.process_next(store, worker_id="test-worker")

    assert result is not None
    assert result.job_id == job.job_id
    assert result.state == "done"
    assert result.attempts == 1
    assert result.error is None
    assert len(result.assets) == 1

    asset = result.assets[0]
    assert asset.kind == "image"
    assert asset.mime == "image/png"
    assert asset.provider == "local-cover"
    assert (store.root / asset.path).is_file()

    # Empty queue: nothing to claim and no exception
    assert media_worker.process_next(store, worker_id="test-worker") is None


def test_failing_handler_requeues_then_fails_after_max_attempts(store, monkeypatch):
    def boom(job, media_store):
        raise RuntimeError("render exploded")

    monkeypatch.setitem(media_worker.HANDLERS, "episode", media_worker.JobHandler(run=boom))

    job = store.put_job(make_job(kind="episode", max_attempts=2, payload={}))

    first = media_worker.process_next(store, worker_id="test-worker")
    assert first is not None
    assert first.job_id == job.job_id
    assert first.attempts == 1
    assert first.state == "queued"
    assert "render exploded" in (first.error or "")
    assert first.worker_id is None

    second = media_worker.process_next(store, worker_id="test-worker")
    assert second is not None
    assert second.attempts == 2
    assert second.state == "failed"
    assert "render exploded" in (second.error or "")

    # Retries exhausted: the job is never claimed again
    assert store.claim_next("test-worker") is None


def test_budget_rejection_fails_the_job_without_running_it(store, monkeypatch):
    ran = []

    def paid(job, media_store):
        ran.append(job.job_id)
        return [], 0.0

    monkeypatch.setitem(
        media_worker.HANDLERS,
        "episode",
        media_worker.JobHandler(run=paid, estimate=lambda job: 5.0),
    )

    job = store.put_job(make_job(kind="episode", max_attempts=1, payload={}))
    gate = BudgetGate(run_budget=1.0)

    result = media_worker.process_next(store, worker_id="test-worker", budget=gate)

    assert ran == []
    assert result is not None
    assert result.job_id == job.job_id
    assert result.state == "failed"
    assert "budget" in (result.error or "")


def test_unsupported_kind_never_crashes_the_loop(store, tmp_path):
    job = store.put_job(make_job(kind="revoice", max_attempts=1, payload={}))

    project = SimpleNamespace(project_path=str(tmp_path), project_name="demo")
    processed = media_worker.run_cycle(worker_id="test-worker", projects=[project])

    assert processed == 1
    loaded = store.get_job(job.job_id)
    assert loaded is not None
    assert loaded.state == "failed"
    assert "unsupported job kind" in (loaded.error or "")

    # A project without a path is skipped instead of blowing up the cycle
    assert media_worker.run_cycle(
        worker_id="test-worker", projects=[SimpleNamespace(project_name="nopath")]
    ) == 0
