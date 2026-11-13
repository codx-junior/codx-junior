"""Offline tests for the media store (jobs, leases, retries, assets, inbox)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from codx.junior.media.model import EpisodeIR, MediaJob, Shot
from codx.junior.media.store import MediaStore


@pytest.fixture()
def store(tmp_path):
    return MediaStore(tmp_path / ".codx" / "media")


def make_job(created_at=None, **kwargs):
    payload = dict(project="demo", kind="episode", payload={"episode_id": "ep-1"})
    if created_at is not None:
        payload["created_at"] = created_at
    payload.update(kwargs)
    return MediaJob(**payload)


def test_constructor_creates_layout_and_for_project(store, tmp_path):
    assert store.root == tmp_path / ".codx" / "media"
    for directory in ("inbox", "jobs", "episodes", "assets", "tmp"):
        assert (store.root / directory).is_dir()

    project_store = MediaStore.for_project(tmp_path)
    assert project_store.root == tmp_path / ".codx" / "media"
    # Tests must never touch the real project store
    assert tmp_path in project_store.root.parents


def test_put_get_list_roundtrip(store):
    older = datetime(2024, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2024, 6, 1, tzinfo=timezone.utc)
    second = store.put_job(make_job(created_at=newer, payload={"n": 2}))
    first = store.put_job(make_job(created_at=older, payload={"n": 1}))
    assert first.job_id and second.job_id
    assert first.job_id != second.job_id

    loaded = store.get_job(first.job_id)
    assert loaded is not None
    assert loaded.job_id == first.job_id
    assert loaded.payload == {"n": 1}
    assert loaded.state == "queued"

    jobs = store.list_jobs()
    assert [job.job_id for job in jobs] == [first.job_id, second.job_id]

    store.put_job(make_job(state="done"))
    assert len(store.list_jobs(state="queued")) == 2
    assert len(store.list_jobs(state="done")) == 1
    assert store.get_job("missing") is None


def test_claim_next_twice_does_not_return_same_job(store):
    first = store.put_job(make_job(created_at=datetime(2024, 1, 1, tzinfo=timezone.utc)))
    second = store.put_job(make_job(created_at=datetime(2024, 1, 2, tzinfo=timezone.utc)))

    claimed_first = store.claim_next("worker-a")
    claimed_second = store.claim_next("worker-a")

    assert claimed_first is not None and claimed_second is not None
    assert claimed_first.job_id == first.job_id
    assert claimed_second.job_id == second.job_id
    assert claimed_first.job_id != claimed_second.job_id
    assert claimed_first.state == "running"
    assert claimed_first.worker_id == "worker-a"
    assert claimed_first.heartbeat_at is not None
    assert claimed_first.attempts == 1

    # Nothing left to claim: exhausted queue
    assert store.claim_next("worker-b") is None


def test_stale_lease_is_reclaimed(store):
    job = store.put_job(make_job(max_attempts=3))
    claimed = store.claim_next("worker-a", ttl_seconds=300)
    assert claimed is not None

    claimed.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=3600)
    store.put_job(claimed)

    reclaimed = store.claim_next("worker-b", ttl_seconds=300)
    assert reclaimed is not None
    assert reclaimed.job_id == job.job_id
    assert reclaimed.worker_id == "worker-b"
    assert reclaimed.attempts == 2
    assert reclaimed.state == "running"

    # A fresh lease is never stolen
    assert store.claim_next("worker-c", ttl_seconds=300) is None


def test_heartbeat_refreshes_lease(store):
    store.put_job(make_job())
    assert store.heartbeat("missing") is False
    claimed = store.claim_next("worker-a")
    assert claimed is not None
    previous = claimed.heartbeat_at
    assert store.heartbeat(claimed.job_id) is True
    refreshed = store.get_job(claimed.job_id)
    assert refreshed is not None
    assert refreshed.heartbeat_at >= previous

    store.finish(claimed.job_id)
    assert store.heartbeat(claimed.job_id) is False


def test_fail_requeues_then_fails_after_max_attempts(store):
    job = store.put_job(make_job(max_attempts=2))

    claimed = store.claim_next("worker-a")
    assert claimed is not None and claimed.attempts == 1
    requeued = store.fail(claimed.job_id, "boom 1")
    assert requeued is not None
    assert requeued.state == "queued"
    assert requeued.error == "boom 1"
    assert requeued.worker_id is None

    claimed = store.claim_next("worker-a")
    assert claimed is not None and claimed.attempts == 2
    failed = store.fail(claimed.job_id, "boom 2")
    assert failed is not None
    assert failed.job_id == job.job_id
    assert failed.state == "failed"
    assert failed.error == "boom 2"
    assert store.claim_next("worker-a") is None


def test_finish_records_assets_and_cost(store):
    store.put_job(make_job())
    claimed = store.claim_next("worker-a")
    assert claimed is not None
    asset = store.put_asset(b"cover-bytes", kind="image", mime="image/png", provider="local")

    finished = store.finish(claimed.job_id, assets=[asset], cost=0.25)
    assert finished is not None
    assert finished.state == "done"
    assert [item.asset_id for item in finished.assets] == [asset.asset_id]
    assert finished.cost == pytest.approx(0.25)
    assert store.finish("missing") is None


def test_put_asset_is_idempotent_and_content_addressed(store):
    content = b"\x89PNG\r\n\x1a\nfake-image"
    first = store.put_asset(content, kind="image", mime="image/png", provider="local")
    second = store.put_asset(content, kind="image", mime="image/png", provider="local")

    assert first.asset_id == second.asset_id
    assert first.sha256 == second.sha256
    assert first.path == second.path
    assert first.path.startswith(f"assets/{first.sha256[:2]}/")
    assert first.path.endswith(".png")
    assert first.bytes == len(content)

    stored = store.root / first.path
    assert stored.is_file()
    assert stored.read_bytes() == content
    assert len(list((store.root / "assets" / first.sha256[:2]).iterdir())) == 1

    other = store.put_asset(b"different bytes", kind="image", mime="image/png")
    assert other.asset_id != first.asset_id


def test_put_asset_derives_extension_from_mime(store):
    asset = store.put_asset(b"audio", kind="audio", mime="audio/wav")
    assert asset.path.endswith(".wav")
    explicit = store.put_asset(b"audio", kind="audio", mime="audio/wav", ext="bin")
    assert explicit.path.endswith(".bin")
    assert explicit.asset_id == asset.asset_id


def test_episode_roundtrip(store):
    ir = EpisodeIR(
        episode_id="ep-42",
        title="Chapter 42",
        project="demo",
        shots=[Shot(shot_id="s1", kind="title", duration=3.0, narration="hello")],
        sources=["docs/readme.md"],
    )
    store.put_episode(ir)

    path = store.root / "episodes" / "ep-42.episode.json"
    assert path.is_file()

    loaded = store.get_episode("ep-42")
    assert loaded is not None
    assert loaded.title == "Chapter 42"
    assert [shot.shot_id for shot in loaded.shots] == ["s1"]
    assert store.get_episode("missing") is None


def test_take_inbox_moves_files_to_done(store):
    store.inbox_done_dir.mkdir(parents=True, exist_ok=True)
    valid = EpisodeIR(episode_id="ep-inbox", title="Dropped")
    valid_path = store.inbox_dir / "dropped.episode.json"
    valid_path.write_text(json.dumps(valid.model_dump(mode="json")), encoding="utf-8")
    broken_path = store.inbox_dir / "broken.episode.json"
    broken_path.write_text("{not json", encoding="utf-8")
    ignored = store.inbox_dir / "notes.txt"
    ignored.write_text("ignore me", encoding="utf-8")

    assert [path.name for path in store.inbox()] == [
        "broken.episode.json",
        "dropped.episode.json",
    ]

    paths, episodes = store.take_inbox()
    assert [path.name for path in paths] == ["dropped.episode.json"]
    assert [ir.episode_id for ir in episodes] == ["ep-inbox"]
    assert (store.inbox_done_dir / "dropped.episode.json").is_file()
    assert not valid_path.exists()
    # Unreadable files are kept for inspection
    assert broken_path.is_file()
    assert not (store.inbox_done_dir / "broken.episode.json").exists()
    assert store.take_inbox() == ([], [])
