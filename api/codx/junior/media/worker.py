"""Background worker of the Project's Channel media pipeline.

One daemon thread drains the ``<project>/.codx/media/jobs`` queues: it claims a
job with a lease, asks the budget gate whether the work may be paid for, runs
the handler of that job kind and records the outcome with
``MediaStore.finish`` / ``MediaStore.fail``. Leases and retries live in the
store, so a restart simply resumes the interrupted work (an expired lease is
reclaimed by ``claim_next``).

Local first: the only handlers wired today are the inbox ingest of an episode
ir and the Pillow cover renderer, so nothing here touches the network, an AI
model or an external service.

Public API (wired from ``codx.junior.background``):

    start_media_worker(stop_event=None)
    stop_media_worker()

Test helpers (no thread, no sleeping on the wall clock):

    process_next(store, worker_id=None, budget=None)
    run_cycle(worker_id=None, stop_event=None, projects=None, budget=None)
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from codx.junior.media.budget import BudgetGate
from codx.junior.media.model import MediaAsset, MediaJob
from codx.junior.media.store import MediaStore

logger = logging.getLogger(__name__)

# Seconds between two draining cycles of every project queue
POLL_INTERVAL_SECONDS: int = 10
# Lease lifetime of a claimed job; a crashed worker's job is reclaimed after it
LEASE_TTL_SECONDS: int = 300
# Upper bound of jobs a single cycle takes from one project queue
MAX_JOBS_PER_PROJECT_CYCLE: int = 10
# How long stop() waits for the thread to leave its cycle
STOP_TIMEOUT_SECONDS: float = 5.0

# Identifier reported in the job leases of this process
WORKER_ID: str = f"media-worker-{uuid.uuid4().hex[:8]}"

# Thread state, guarded by _THREAD_LOCK
_THREAD_LOCK = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None

# Lazily created process wide budget gate (unlimited unless a caller sets limits)
_budget_gate: Optional[BudgetGate] = None


@dataclass(frozen=True)
class JobHandler:
    """One runnable job kind: the cost estimate first, the work afterwards."""

    run: Callable[[MediaJob, MediaStore], Tuple[List[MediaAsset], float]]
    estimate: Callable[[MediaJob], float] = lambda job: 0.0


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def _run_cover(job: MediaJob, store: MediaStore) -> Tuple[List[MediaAsset], float]:
    """Render the cover of ``job.payload`` with the local (offline) provider."""
    from codx.junior.media.providers.image_local import LocalCoverProvider

    provider = LocalCoverProvider()
    request = dict(job.payload or {})
    request.setdefault("project", job.project)
    asset = provider.render(request, store)
    return [asset], float(provider.estimate(request) or 0.0)


def _estimate_cover(job: MediaJob) -> float:
    from codx.junior.media.providers.image_local import LocalCoverProvider

    return float(LocalCoverProvider().estimate(dict(job.payload or {})) or 0.0)


def _run_episode(job: MediaJob, store: MediaStore) -> Tuple[List[MediaAsset], float]:
    """Ingest the inbox: dropped episode files become persisted episode irs."""
    _paths, episodes = store.take_inbox()
    for episode in episodes:
        if not episode.project:
            episode.project = job.project
        store.put_episode(episode)
    logger.info(
        "Ingested %d episode(s) for project %s", len(episodes), job.project
    )
    return [], 0.0


# Handler table: the single place that decides what a job kind does
HANDLERS: Dict[str, JobHandler] = {
    "episode": JobHandler(run=_run_episode),
    "cover": JobHandler(run=_run_cover, estimate=_estimate_cover),
}


# ---------------------------------------------------------------------------
# budget gate
# ---------------------------------------------------------------------------


def default_budget_gate() -> BudgetGate:
    """Process wide budget gate; unlimited (no limits) until a caller sets them."""
    global _budget_gate
    if _budget_gate is None:
        _budget_gate = BudgetGate()
    return _budget_gate


def _safe_estimate(handler: JobHandler, job: MediaJob) -> float:
    """Estimate of ``job``; a broken estimator must not reject the job."""
    try:
        return float(handler.estimate(job) or 0.0)
    except Exception as ex:  # noqa: BLE001 - an estimate is best effort
        logger.warning("Cannot estimate media job %s: %s", job.job_id, ex)
        return 0.0


# ---------------------------------------------------------------------------
# single job execution (the unit the tests drive)
# ---------------------------------------------------------------------------


def process_next(
    store: MediaStore,
    worker_id: Optional[str] = None,
    budget: Optional[BudgetGate] = None,
) -> Optional[MediaJob]:
    """Claim and run a single job; ``None`` when the queue has nothing to do."""
    claimed = store.claim_next(worker_id or WORKER_ID, ttl_seconds=LEASE_TTL_SECONDS)
    if claimed is None:
        return None

    handler = HANDLERS.get(claimed.kind)
    if handler is None:
        logger.warning(
            "Unsupported media job kind '%s' (job %s)", claimed.kind, claimed.job_id
        )
        return store.fail(claimed.job_id, f"unsupported job kind: {claimed.kind}") or claimed

    gate = budget if budget is not None else default_budget_gate()
    decision = gate.check(_safe_estimate(handler, claimed), reason=f"media {claimed.kind} job")
    if not decision.allowed:
        logger.warning("Media job %s rejected by budget: %s", claimed.job_id, decision.reason)
        return store.fail(claimed.job_id, decision.reason) or claimed

    try:
        produced, cost = handler.run(claimed, store)
    except Exception as ex:  # noqa: BLE001 - fail() owns the retry policy
        logger.exception("Media job %s failed: %s", claimed.job_id, ex)
        return store.fail(claimed.job_id, str(ex)) or claimed

    assets = list(claimed.assets or []) + list(produced or [])
    spent = float(cost or 0.0)
    if spent:
        gate.charge(spent)
    finished = store.finish(claimed.job_id, assets=assets, cost=spent)
    logger.info(
        "Media job %s (%s) done with %d asset(s)", claimed.job_id, claimed.kind, len(assets)
    )
    return finished or claimed


# ---------------------------------------------------------------------------
# one draining cycle (shared by the thread loop and the tests)
# ---------------------------------------------------------------------------


def _projects(projects: Optional[Sequence[object]] = None) -> List[object]:
    """Projects to drain; only those exposing a ``project_path``."""
    if projects is None:
        from codx.junior.project.project_discover import find_all_projects

        projects = list(find_all_projects().values())
    return [project for project in projects if getattr(project, "project_path", None)]


def run_cycle(
    worker_id: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    projects: Optional[Sequence[object]] = None,
    budget: Optional[BudgetGate] = None,
) -> int:
    """Drain the queue of every project once; returns how many jobs ran."""
    worker_id = worker_id or WORKER_ID
    processed = 0
    for project in _projects(projects):
        if stop_event is not None and stop_event.is_set():
            break
        try:
            store = MediaStore.for_project(project.project_path)
        except Exception as ex:  # noqa: BLE001 - one bad project never stops the rest
            logger.warning("Cannot open media store of %s: %s", project.project_path, ex)
            continue
        for _ in range(MAX_JOBS_PER_PROJECT_CYCLE):
            if stop_event is not None and stop_event.is_set():
                return processed
            try:
                job = process_next(store, worker_id, budget=budget)
            except Exception as ex:  # noqa: BLE001 - the cycle keeps going
                logger.exception("Media job in %s raised: %s", project.project_path, ex)
                break
            if job is None:
                break
            processed += 1
    return processed


def _worker_loop(stop_event: threading.Event, worker_id: str) -> None:
    """Drain the queues until ``stop_event`` is set (stop is immediate)."""
    logger.debug("Media worker loop started (worker_id=%s)", worker_id)
    while not stop_event.is_set():
        try:
            run_cycle(worker_id=worker_id, stop_event=stop_event)
        except Exception as ex:  # noqa: BLE001 - a cycle must never kill the worker
            logger.exception("Media worker cycle failed: %s", ex)
        if stop_event.is_set():
            break
        stop_event.wait(POLL_INTERVAL_SECONDS)
    logger.debug("Media worker loop finished (worker_id=%s)", worker_id)


# ---------------------------------------------------------------------------
# service control (wired from codx.junior.background)
# ---------------------------------------------------------------------------


def start_media_worker(stop_event: Optional[threading.Event] = None) -> None:
    """Start the media worker daemon thread.

    Idempotent: when the thread is already alive the call is a no-op.
    """
    global _thread, _stop_event

    with _THREAD_LOCK:
        if _thread is not None and _thread.is_alive():
            logger.debug("Media worker is already running")
            return
        event = stop_event if stop_event is not None else threading.Event()
        _stop_event = event
        _thread = threading.Thread(
            target=_worker_loop,
            args=(event, WORKER_ID),
            name="MediaWorker",
            daemon=True,
        )
        _thread.start()

    logger.info("*** Media worker started (worker_id=%s) ***", WORKER_ID)


def stop_media_worker() -> None:
    """Stop the worker thread; safe to call when it was never started."""
    global _thread, _stop_event

    with _THREAD_LOCK:
        thread, event = _thread, _stop_event
        _thread = None
        _stop_event = None

    if event is not None:
        event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=STOP_TIMEOUT_SECONDS)
    if thread is not None:
        logger.info("Media worker stopped")

# Made with ❤️ by codx-junior
