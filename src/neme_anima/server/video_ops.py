"""ffprobe/ffmpeg probing + playback-conversion machinery for the sources API.

Extracted from ``api/sources.py`` so the router keeps HTTP concerns only. These
are plain helpers (no FastAPI types); the conversion job state (``_CONVERT_JOBS``
/ ``_CONVERT_TASKS``) lives here too, mutated from the worker thread and read by
the ``/convert`` routes in ``api/sources.py``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from neme_anima.storage.project import Project

logger = logging.getLogger(__name__)


def _extract_thumbnail(video_path: Path, dest: Path, *, max_side: int = 320) -> None:
    """Grab one frame near 10 % of the video and save it as a JPEG via ffmpeg.

    We shell out to ffmpeg/ffprobe rather than using cv2 or decord because:
      * ffmpeg handles every container/codec combination the user is likely to
        have and is already installed wherever decord works;
      * a clean subprocess avoids CPython/opencv install fragility (we hit a
        broken cv2 install in development);
      * a single fast seek (`-ss` before `-i`) is essentially instant even on
        large files.
    """
    import shutil as _shutil
    import subprocess

    if _shutil.which("ffmpeg") is None or _shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe not found on PATH")

    duration = _probe_duration_seconds(video_path)
    seek = max(0.0, duration * 0.10) if duration > 10 else 0.0

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{seek:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", f"scale='min({max_side},iw)':-2",
        "-q:v", "4",
        str(dest),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    # Some files don't honour the fast seek (e.g. very short clips, broken
    # indexes); retry from frame 0 if the first attempt produced nothing.
    if res.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        cmd_retry = cmd.copy()
        cmd_retry[cmd_retry.index("-ss") + 1] = "0"
        res = subprocess.run(cmd_retry, capture_output=True, text=True, timeout=30)

    if res.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        stderr = (res.stderr or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"ffmpeg failed (rc={res.returncode}): {stderr[0][:300]}")


def _extract_frame_at(video_path: Path, dest: Path, t_seconds: float) -> None:
    """Grab the single frame at ``t_seconds`` as a full-resolution PNG via ffmpeg.

    Unlike :func:`_extract_thumbnail`, no scaling is applied — a captured frame
    goes straight into the training set, so it's kept at the source's native
    resolution (the shared upload ingest path caps the longest side afterwards
    if a source is unusually large).

    Frame fidelity: a modern ffmpeg performs an *accurate* seek even with
    ``-ss`` before ``-i`` — it fast-seeks to the keyframe before the target then
    decodes forward to the exact timestamp — so this matches the frame the
    browser shows at the same ``currentTime`` while staying fast on large files.
    Stubborn files (broken indexes, or ``t`` past the last keyframe) get a
    second pass with an output-side ``-ss``, which is slower but exact.
    """
    import shutil as _shutil
    import subprocess

    if _shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    t = max(0.0, float(t_seconds))

    def _run(input_seek: bool) -> subprocess.CompletedProcess:
        seek = ["-ss", f"{t:.3f}"]
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if input_seek:
            cmd += [*seek, "-i", str(video_path)]
        else:
            cmd += ["-i", str(video_path), *seek]
        cmd += ["-frames:v", "1", str(dest)]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    res = _run(input_seek=True)
    if res.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        res = _run(input_seek=False)

    if res.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        stderr = (res.stderr or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"ffmpeg failed (rc={res.returncode}): {stderr[0][:300]}")


_FRAME_INDEX_SEEK_PREROLL_SECONDS = 1.0


def _frame_index_seek_plan(
    frame_idx: int,
    *,
    fps: float | None,
    t_seconds: float | None,
) -> tuple[float, int]:
    """Return ``(seek_seconds, local_frame_idx)`` for fast frame-index capture."""
    if frame_idx < 0:
        raise ValueError("frame_idx must be non-negative")

    fps_value = float(fps or 0.0)
    if fps_value <= 0 and t_seconds is not None and t_seconds > 0 and frame_idx > 0:
        fps_value = frame_idx / float(t_seconds)
    if fps_value <= 0:
        return 0.0, frame_idx

    preroll_frames = max(1, int(round(fps_value * _FRAME_INDEX_SEEK_PREROLL_SECONDS)))
    seek_frame = max(0, frame_idx - preroll_frames)
    return seek_frame / fps_value, frame_idx - seek_frame


def _extract_frame_by_index(
    video_path: Path,
    dest: Path,
    frame_idx: int,
    *,
    fps: float | None = None,
    t_seconds: float | None = None,
) -> None:
    """Grab a single decoded video frame by zero-based frame index."""
    import shutil as _shutil
    import subprocess

    if _shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    if frame_idx < 0:
        raise ValueError("frame_idx must be non-negative")

    seek_seconds, local_frame_idx = _frame_index_seek_plan(
        frame_idx, fps=fps, t_seconds=t_seconds,
    )
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if seek_seconds > 0:
        cmd += ["-ss", f"{seek_seconds:.3f}"]
    cmd += [
        "-i", str(video_path),
        "-vf", f"select=eq(n\\,{local_frame_idx})",
        "-vsync", "0",
        "-frames:v", "1",
        str(dest),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        stderr = (res.stderr or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"ffmpeg failed (rc={res.returncode}): {stderr[0][:300]}")


def _probe_duration_seconds(video_path: Path) -> float:
    """Return the video duration in seconds via ffprobe, or 0.0 if unknown."""
    import subprocess

    res = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=nw=1:nokey=1",
         str(video_path)],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return float(res.stdout.strip())
    except (TypeError, ValueError):
        return 0.0


def _probe_fps(video_path: Path) -> float:
    """Return the video's average framerate via ffprobe, or 0.0 if unknown.

    Uses ``r_frame_rate`` (the stream's nominal rate, expressed as the
    rational ``num/den``) — matches what decord's ``get_avg_fps`` returns
    closely enough for the segment-editor UI (the UI only needs fps to
    display tooltips and compute scrub steps; the pipeline re-reads it
    from decord at extraction time anyway).
    """
    import subprocess

    res = subprocess.run(
        ["ffprobe", "-v", "error",
         "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=nw=1:nokey=1",
         str(video_path)],
        capture_output=True, text=True, timeout=15,
    )
    raw = (res.stdout or "").strip()
    if not raw or raw == "0/0":
        return 0.0
    if "/" in raw:
        try:
            num, den = raw.split("/", 1)
            num_f, den_f = float(num), float(den)
        except ValueError:
            return 0.0
        return num_f / den_f if den_f else 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _probe_vcodec(video_path: Path) -> str:
    """Return the source's primary video codec name via ffprobe, lowercased
    (e.g. ``"hevc"``, ``"h264"``, ``"av1"``), or ``""`` if unknown.

    The segment editor uses this to decide up-front whether the browser can
    decode the original: a codec the browser can't decode (HEVC on most
    Chrome/Firefox) plays as a black frame *with* audio and never fires a
    ``<video>`` error event, so we can't rely on ``onerror`` to surface the
    Convert button — we check the codec instead.
    """
    import subprocess

    res = subprocess.run(
        ["ffprobe", "-v", "error",
         "-select_streams", "v:0",
         "-show_entries", "stream=codec_name",
         "-of", "default=nw=1:nokey=1",
         str(video_path)],
        capture_output=True, text=True, timeout=15,
    )
    lines = (res.stdout or "").strip().splitlines()
    return lines[0].strip().lower() if lines else ""


_VIDEO_MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
    ".wmv": "video/x-ms-wmv",
}


# Per-(path, mode) conversion job state, read by /convert/status. Mutated
# from the worker thread (plain dict writes are GIL-atomic) and from the
# request handler. Background tasks are held in _CONVERT_TASKS so the event
# loop doesn't garbage-collect them mid-run.
_CONVERT_JOBS: dict[tuple[str, str], dict[str, Any]] = {}
_CONVERT_TASKS: set[asyncio.Task] = set()


def _preview_cache_path(project: Project, video_path: Path, mode: str) -> Path:
    return project.root / ".previews" / f"{video_path.stem}.{mode}.mp4"


def _convert_cmd(
    video_path: Path, dest: Path, mode: str, *, with_audio: bool,
) -> list[str]:
    """Build the ffmpeg argv for a playback conversion.

    ``remux`` copies the (HEVC) video stream bit-for-bit into MP4 — zero
    quality loss, near-instant — for browsers that can decode HEVC. ``h264``
    re-encodes to a small 480p H.264 baseline stream for browsers that can't
    decode HEVC. Both stream progress on stdout via
    ``-progress pipe:1`` so the caller can compute a percentage.
    """
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(video_path),
    ]
    if mode == "remux":
        cmd += ["-map", "0:v:0", "-c:v", "copy", "-tag:v", "hvc1"]
    else:  # h264
        cmd += [
            "-vf", "scale='min(854,iw)':-2",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.1",
            "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        ]
    if with_audio:
        if mode == "remux":
            cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"]
        else:
            # h264 relies on ffmpeg's automatic stream selection (no explicit -map);
            # remux uses explicit -map 0:a:0?.
            cmd += ["-c:a", "aac", "-b:a", "96k", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(dest)]
    return cmd


def _run_one_ffmpeg(
    cmd: list[str], key: tuple[str, str], duration: float, *, timeout: float = 900.0,
) -> tuple[int, str]:
    """Run one ffmpeg invocation, streaming -progress to update job pct.

    Returns (returncode, last_stderr). Parses ``out_time_us`` lines against the
    probed duration to produce a 0..100 percentage; a duration of 0 (unprobeable
    source) just leaves pct at 0 and the bar reads as indeterminate.

    A wall-clock kill-timer bounds a hung encode: reading ``proc.stdout`` to EOF
    blocks until ffmpeg exits, so a plain ``wait(timeout=)`` can never fire while
    the loop is stuck. The timer kills the process at the deadline, which closes
    stdout and lets the loop (then ``wait``) return.
    """
    import subprocess
    import threading

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    killer = threading.Timer(timeout, proc.kill)
    killer.start()
    try:
        if proc.stdout is not None:
            for raw in proc.stdout:
                line = raw.strip()
                if line.startswith("out_time_us=") and duration > 0:
                    try:
                        us = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    pct = min(100.0, max(0.0, us / 1_000_000.0 / duration * 100.0))
                    job = _CONVERT_JOBS.get(key)
                    if job is not None:
                        job["pct"] = pct
        rc = proc.wait()
    finally:
        killer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    return rc, stderr


def _transcode_for_playback(
    video_path: Path, dest: Path, mode: str, duration: float, key: tuple[str, str],
) -> None:
    """Convert ``video_path`` to ``dest`` for the given mode, tmp-then-rename.

    Audio is best-effort: some sources have audio streams ffmpeg's aac encoder
    refuses (and lavfi test sources have none), so a failed first pass retries
    with ``-an`` before giving up.
    """
    import shutil as _shutil

    if _shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    tmp = dest.with_name(dest.stem + ".tmp" + dest.suffix)
    try:
        rc, stderr = _run_one_ffmpeg(
            _convert_cmd(video_path, tmp, mode, with_audio=True), key, duration,
        )
        if rc != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            rc, stderr = _run_one_ffmpeg(
                _convert_cmd(video_path, tmp, mode, with_audio=False), key, duration,
            )
        if rc != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            last = (stderr or "").strip().splitlines()[-1:] or [""]
            raise RuntimeError(f"ffmpeg failed (rc={rc}): {last[0][:300]}")
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _run_convert_job(
    key: tuple[str, str], video_path: Path, dest: Path, mode: str, duration: float,
) -> None:
    """Worker-thread entrypoint: run the transcode and record terminal state."""
    try:
        _transcode_for_playback(video_path, dest, mode, duration, key)
        _CONVERT_JOBS[key] = {"state": "ready", "pct": 100.0, "mode": mode, "error": ""}
    except Exception as e:  # noqa: BLE001 — terminal state must always be recorded
        logger.exception("convert failed for %s (mode=%s)", video_path, mode)
        prev = _CONVERT_JOBS.get(key) or {}
        _CONVERT_JOBS[key] = {
            "state": "failed", "pct": prev.get("pct", 0.0),
            "mode": mode, "error": f"{type(e).__name__}: {e}",
        }


def _merge_ranges(
    ranges: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Sort ascending and merge overlapping or touching intervals.

    Two intervals are considered touching when ``b1 >= a2`` (the second's
    start is at or before the first's end). Tolerance is implicit in the
    callers' rounding — we don't apply an epsilon here because the
    persisted shape is rounded to 1 ms before save, which gives clean
    equality for everything the UI can produce.
    """
    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda r: r[0])
    out: list[tuple[float, float]] = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        prev_start, prev_end = out[-1]
        if start <= prev_end:
            out[-1] = (prev_start, max(prev_end, end))
        else:
            out.append((start, end))
    return out
