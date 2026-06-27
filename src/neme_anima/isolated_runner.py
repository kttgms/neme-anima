"""Subprocess-isolated pipeline runner.

Each extract / rerun / scan job runs in a freshly-spawned child process.
When the child exits (success *or* error), the OS reclaims every byte of
RSS it ever touched — ONNX arenas, decord frame buffers, glibc wilderness
pools, all of it.  No amount of in-process ``gc.collect()`` or
``malloc_trim()`` can match a plain ``wait4()``.

Architecture
------------
::

    parent process
      ├── asyncio event loop (FastAPI / server)
      │     └── asyncio.to_thread(_drain_queue_thread)
      │           └── blocks on mp.Queue.get()
      │                 │  ProgressEvent objects
      │                 ▼
      │           calls PipelineProgress methods
      │
      └── child process  (spawned via multiprocessing)
            └── run_extract / run_rerun / run_scan
                  └── _SubprocessProgress → mp.Queue

The ``pause / resume`` flow is preserved: the child sends a
``PauseEvent`` and then blocks on a ``multiprocessing.Event`` that the
parent sets when the user clicks Resume.

Public entry points
-------------------
* :func:`run_extract_isolated`   — subprocess wrapper for :func:`~neme_anima.pipeline.run_extract`
* :func:`run_rerun_isolated`     — subprocess wrapper for :func:`~neme_anima.pipeline.run_rerun`
* :func:`run_scan_isolated`      — subprocess wrapper for :func:`~neme_anima.pipeline.run_scan`
"""

from __future__ import annotations

import multiprocessing
import multiprocessing.queues
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neme_anima.config import PipelineConfig
from neme_anima.pipeline_progress import NULL_PROGRESS, PipelineProgress
from neme_anima.storage.project import Project


# ---------------------------------------------------------------------------
# Events sent child → parent over the Queue
# ---------------------------------------------------------------------------

@dataclass
class _StageStart:
    key: str
    label: str
    total: int
    message: str

@dataclass
class _StageAdvance:
    key: str
    n: int

@dataclass
class _StageMessage:
    key: str
    message: str

@dataclass
class _StageDone:
    key: str
    message: str

@dataclass
class _StageFail:
    key: str
    error: str

@dataclass
class _Finish:
    summary: dict[str, Any] | None

@dataclass
class _PauseRequest:
    message: str

@dataclass
class _ResumeAck:
    pass  # child sends this after unblocking from pause

@dataclass
class _WorkerError:
    exc_type: str
    exc_message: str
    traceback: str

@dataclass
class _WorkerDone:
    pass


# ---------------------------------------------------------------------------
# Child-side progress adapter
# ---------------------------------------------------------------------------

class _SubprocessProgress(PipelineProgress):
    """Serialises PipelineProgress calls into the inter-process queue.

    Runs entirely in the child process.  ``wait_for_resume`` blocks the child
    until the parent writes ``True`` to ``_resume_event``.
    """

    def __init__(
        self,
        queue: multiprocessing.Queue,
        resume_event: "multiprocessing.Event",  # type: ignore[type-arg]
    ) -> None:
        self._q = queue
        self._resume = resume_event

    def stage_start(self, key: str, label: str, *, total: int = 0, message: str = "") -> None:
        self._q.put(_StageStart(key=key, label=label, total=total, message=message))

    def stage_advance(self, key: str, n: int = 1) -> None:
        self._q.put(_StageAdvance(key=key, n=n))

    def stage_message(self, key: str, message: str) -> None:
        self._q.put(_StageMessage(key=key, message=message))

    def stage_done(self, key: str, *, message: str = "") -> None:
        self._q.put(_StageDone(key=key, message=message))

    def stage_fail(self, key: str, error: str) -> None:
        self._q.put(_StageFail(key=key, error=error))

    def finish(self, summary: dict | None = None) -> None:
        self._q.put(_Finish(summary=summary))

    def wait_for_resume(self, *, message: str = "") -> None:
        self._q.put(_PauseRequest(message=message))
        self._resume.wait()   # blocks until parent sets the event
        self._resume.clear()
        self._q.put(_ResumeAck())


# ---------------------------------------------------------------------------
# Child worker entry points (run inside the spawned process)
# ---------------------------------------------------------------------------

def _worker_extract(
    project_root: str,
    source_idx: int,
    pipeline_cfg: PipelineConfig,
    q: multiprocessing.Queue,
    resume_event: "multiprocessing.Event",  # type: ignore[type-arg]
) -> None:
    """Child-process entry point for extract."""
    _run_in_child(
        "extract",
        project_root=project_root,
        source_idx=source_idx,
        pipeline_cfg=pipeline_cfg,
        q=q,
        resume_event=resume_event,
    )


def _worker_rerun(
    project_root: str,
    video_stem: str,
    pipeline_cfg: PipelineConfig,
    q: multiprocessing.Queue,
    resume_event: "multiprocessing.Event",  # type: ignore[type-arg]
) -> None:
    """Child-process entry point for rerun."""
    _run_in_child(
        "rerun",
        project_root=project_root,
        video_stem=video_stem,
        pipeline_cfg=pipeline_cfg,
        q=q,
        resume_event=resume_event,
    )


def _worker_scan(
    project_root: str,
    source_idx: int,
    pipeline_cfg: PipelineConfig,
    q: multiprocessing.Queue,
    resume_event: "multiprocessing.Event",  # type: ignore[type-arg]
) -> None:
    """Child-process entry point for scan."""
    _run_in_child(
        "scan",
        project_root=project_root,
        source_idx=source_idx,
        pipeline_cfg=pipeline_cfg,
        q=q,
        resume_event=resume_event,
    )


def _run_in_child(
    kind: str,
    *,
    project_root: str,
    pipeline_cfg: PipelineConfig,
    q: multiprocessing.Queue,
    resume_event: "multiprocessing.Event",  # type: ignore[type-arg]
    source_idx: int | None = None,
    video_stem: str | None = None,
) -> None:
    """Common runner body executed inside the child process."""
    import traceback as _tb

    progress = _SubprocessProgress(q, resume_event)
    try:
        project = Project.load(Path(project_root))
        from neme_anima.pipeline import run_extract, run_rerun, run_scan
        if kind == "extract":
            run_extract(
                project=project,
                source_idx=int(source_idx),  # type: ignore[arg-type]
                progress=progress,
                release_models=True,
                pipeline_cfg=pipeline_cfg,
            )
        elif kind == "rerun":
            run_rerun(
                project=project,
                video_stem=str(video_stem),
                progress=progress,
                release_models=True,
                pipeline_cfg=pipeline_cfg,
            )
        elif kind == "scan":
            run_scan(
                project=project,
                source_idx=int(source_idx),  # type: ignore[arg-type]
                progress=progress,
                pipeline_cfg=pipeline_cfg,
            )
        q.put(_WorkerDone())
    except Exception as exc:  # noqa: BLE001
        q.put(_WorkerError(
            exc_type=type(exc).__name__,
            exc_message=str(exc),
            traceback=_tb.format_exc(),
        ))


# ---------------------------------------------------------------------------
# Parent-side queue drain (runs in a thread inside the parent process)
# ---------------------------------------------------------------------------

def _drain_queue(
    q: multiprocessing.Queue,
    proc: multiprocessing.Process,
    progress: PipelineProgress,
    resume_event: "multiprocessing.Event",  # type: ignore[type-arg]
    *,
    cancel_poll_seconds: float = 0.5,
) -> None:
    """Block until the child finishes, forwarding progress events.

    Runs on a dedicated thread in the parent so it can block on ``q.get()``
    without stalling the asyncio event loop.

    Raises :class:`RuntimeError` if the child exits with a non-zero code
    without sending a ``_WorkerDone`` sentinel (e.g. killed by OOM or SIGKILL).
    Re-raises the child's exception (as a ``RuntimeError``) when the child
    sends a ``_WorkerError``.
    """
    import logging
    logger = logging.getLogger(__name__)

    done = False
    error: _WorkerError | None = None

    while True:
        # Poll with a timeout so we notice if the process was killed and
        # the queue is empty (child never got to send _WorkerDone).
        try:
            event = q.get(timeout=cancel_poll_seconds)
        except Exception:  # queue.Empty or interrupted
            if not proc.is_alive():
                # Process died without sending a sentinel.
                break
            continue

        if isinstance(event, _StageStart):
            progress.stage_start(event.key, event.label, total=event.total, message=event.message)
        elif isinstance(event, _StageAdvance):
            progress.stage_advance(event.key, event.n)
        elif isinstance(event, _StageMessage):
            progress.stage_message(event.key, event.message)
        elif isinstance(event, _StageDone):
            progress.stage_done(event.key, message=event.message)
        elif isinstance(event, _StageFail):
            progress.stage_fail(event.key, event.error)
        elif isinstance(event, _Finish):
            progress.finish(event.summary)
        elif isinstance(event, _PauseRequest):
            progress.wait_for_resume(message=event.message)
            # Unblock the child's _SubprocessProgress.wait_for_resume().
            resume_event.set()
        elif isinstance(event, _ResumeAck):
            pass  # child has resumed; nothing to do on parent side
        elif isinstance(event, _WorkerError):
            error = event
            done = True
            break
        elif isinstance(event, _WorkerDone):
            done = True
            break
        else:
            logger.warning("isolated_runner: unknown queue event %r", event)

    proc.join()

    if error is not None:
        import logging
        logging.getLogger(__name__).error(
            "isolated worker failed (%s): %s\n%s",
            error.exc_type, error.exc_message, error.traceback,
        )
        raise RuntimeError(
            f"isolated pipeline worker raised {error.exc_type}: {error.exc_message}"
        )

    if not done:
        exit_code = proc.exitcode
        raise RuntimeError(
            f"isolated pipeline worker exited unexpectedly "
            f"(exit code {exit_code})"
        )


# ---------------------------------------------------------------------------
# Public API — drop-in replacements for run_extract / run_rerun / run_scan
# ---------------------------------------------------------------------------

def _get_mp_context():
    """Return a 'spawn' multiprocessing context.

    'spawn' is the only start method that works correctly on all platforms
    (macOS no longer allows 'fork' after Py3.12; Windows only has 'spawn').
    It also gives the child a clean address space without inheriting the
    parent's asyncio loop or file descriptors.
    """
    return multiprocessing.get_context("spawn")


def run_extract_isolated(
    *,
    project: Project,
    source_idx: int,
    progress: PipelineProgress | None = None,
    pipeline_cfg: PipelineConfig | None = None,
) -> None:
    """Run :func:`~neme_anima.pipeline.run_extract` in an isolated subprocess.

    The child process exits (and the OS reclaims its entire RSS) before this
    function returns, so repeated calls never accumulate RSS in the parent.
    """
    progress = progress or NULL_PROGRESS
    pipeline_cfg = pipeline_cfg or PipelineConfig()
    ctx = _get_mp_context()
    q: multiprocessing.Queue = ctx.Queue()
    resume_event = ctx.Event()
    proc = ctx.Process(
        target=_worker_extract,
        args=(str(project.root), source_idx, pipeline_cfg, q, resume_event),
        daemon=False,
    )
    proc.start()
    try:
        _drain_queue(q, proc, progress, resume_event)
    except Exception:
        # Ensure the child is dead before re-raising so we don't leave
        # orphaned GPU-using processes.
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
        raise
    finally:
        q.close()
        q.join_thread()


def run_rerun_isolated(
    *,
    project: Project,
    video_stem: str,
    progress: PipelineProgress | None = None,
    pipeline_cfg: PipelineConfig | None = None,
) -> None:
    """Run :func:`~neme_anima.pipeline.run_rerun` in an isolated subprocess."""
    progress = progress or NULL_PROGRESS
    pipeline_cfg = pipeline_cfg or PipelineConfig()
    ctx = _get_mp_context()
    q: multiprocessing.Queue = ctx.Queue()
    resume_event = ctx.Event()
    proc = ctx.Process(
        target=_worker_rerun,
        args=(str(project.root), video_stem, pipeline_cfg, q, resume_event),
        daemon=False,
    )
    proc.start()
    try:
        _drain_queue(q, proc, progress, resume_event)
    except Exception:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
        raise
    finally:
        q.close()
        q.join_thread()


def run_scan_isolated(
    *,
    project: Project,
    source_idx: int,
    progress: PipelineProgress | None = None,
    pipeline_cfg: PipelineConfig | None = None,
) -> None:
    """Run :func:`~neme_anima.pipeline.run_scan` in an isolated subprocess."""
    progress = progress or NULL_PROGRESS
    pipeline_cfg = pipeline_cfg or PipelineConfig()
    ctx = _get_mp_context()
    q: multiprocessing.Queue = ctx.Queue()
    resume_event = ctx.Event()
    proc = ctx.Process(
        target=_worker_scan,
        args=(str(project.root), source_idx, pipeline_cfg, q, resume_event),
        daemon=False,
    )
    proc.start()
    try:
        _drain_queue(q, proc, progress, resume_event)
    except Exception:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
        raise
    finally:
        q.close()
        q.join_thread()
