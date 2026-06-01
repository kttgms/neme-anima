"""REST routes for /api/projects/{slug}/sources."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from neme_anima.server.paths import normalize_input_path
from neme_anima.storage.project import Project, Segment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["sources"])


class AddSourcesBody(BaseModel):
    paths: list[str]


class ImportFolderBody(BaseModel):
    folder: str


class PatchSourceBody(BaseModel):
    excluded_refs: list[str] | None = None


class SegmentBody(BaseModel):
    start_seconds: float
    end_seconds: float


class PutSegmentsBody(BaseModel):
    segments: list[SegmentBody]


class CaptureFrameBody(BaseModel):
    """Body for the segment-editor's "grab this exact frame" button.

    ``time_seconds`` is the playhead position the user picked in the browser
    <video> element. ``character_slug`` (optional) routes the captured frame
    to that character's bucket; omitted = the project's first character, the
    same default the drag-and-drop upload route uses.
    """

    time_seconds: float
    character_slug: str | None = None


def _load(request: Request, slug: str) -> Project:
    entry = request.app.state.registry.get(slug)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown project: {slug}")
    try:
        return Project.load(Path(entry.folder))
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"project files missing for {slug!r} at {entry.folder}",
        )


@router.post("/{slug}/sources")
async def add_sources(request: Request, slug: str, body: AddSourcesBody) -> dict:
    project = _load(request, slug)
    added: list[str] = []
    skipped: list[str] = []
    for p in body.paths:
        try:
            normalized = normalize_input_path(p)
        except ValueError:
            skipped.append(p)
            continue
        try:
            s = project.add_source(normalized)
            added.append(s.path)
        except ValueError:
            skipped.append(str(normalized.resolve()))
    return {"added": added, "skipped": skipped}


@router.post("/{slug}/sources/import-folder")
async def import_folder(request: Request, slug: str, body: ImportFolderBody) -> dict:
    project = _load(request, slug)
    try:
        folder = normalize_input_path(body.folder)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {folder}")
    added, skipped = project.import_videos_from_folder(folder)
    return {
        "added": [s.path for s in added],
        "skipped": skipped,
        "source_root": project.source_root,
    }


@router.post("/{slug}/sources/reimport")
async def reimport(request: Request, slug: str) -> dict:
    project = _load(request, slug)
    if not project.source_root:
        raise HTTPException(
            status_code=400,
            detail="no source folder has been imported yet — pick a folder first",
        )
    folder = Path(project.source_root)
    if not folder.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"source folder is missing: {folder}",
        )
    added, skipped = project.import_videos_from_folder(folder)
    return {
        "added": [s.path for s in added],
        "skipped": skipped,
        "source_root": project.source_root,
    }


@router.delete("/{slug}/sources/{idx}", status_code=204)
async def remove_source(request: Request, slug: str, idx: int) -> Response:
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    project.remove_source(idx)
    return Response(status_code=204)


@router.patch("/{slug}/sources/{idx}")
async def patch_source(
    request: Request,
    slug: str,
    idx: int,
    body: PatchSourceBody,
    character_slug: str | None = None,
) -> dict:
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    if character_slug not in (None, "") and project.character_by_slug(character_slug) is None:
        raise HTTPException(status_code=404, detail=f"unknown character: {character_slug}")
    if body.excluded_refs is not None:
        project.set_excluded_refs(
            idx, body.excluded_refs, character_slug=character_slug or None,
        )
    return {"excluded_refs": project.sources[idx].excluded_refs}


@router.get("/{slug}/sources/{idx}/wipe-preview")
async def wipe_preview(request: Request, slug: str, idx: int) -> dict:
    """Return what an Extract / Re-process on this source would wipe.

    The Sources tab calls this BEFORE firing extract or rerun and shows
    a confirmation modal when ``to_wipe.total > 0`` so the user is never
    surprised by lost work. The breakdown by character lets the user
    decide whether to opt-out a character's refs (and so preserve its
    frames) before clicking through.

    Implementation note: we re-build the same per-character refs map
    that the pipeline does, then walk the metadata log to attribute
    every kept frame matching the stem. Rejected files are listed in
    ``to_wipe`` regardless of attribution because they always wipe.
    """
    from collections import Counter

    from neme_anima.pipeline import (
        _kept_frame_owners,
        _preserve_set_from_refs_by_slug,
        _refs_by_character,
    )

    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")

    video_stem = project.video_stem(idx)
    refs_by_slug = _refs_by_character(project, idx)
    preserve = _preserve_set_from_refs_by_slug(project, refs_by_slug)
    active_slugs = sorted(s for s, refs in refs_by_slug.items() if refs)

    # Walk kept_dir for files actually present and attribute each via
    # metadata. Iterating the metadata log instead would surface phantom
    # rows for frames whose physical file is gone (dedup demoted to
    # rejected/, user manually deleted, prior wipe ran without metadata
    # cleanup) — and the modal would then warn about wiping frames that
    # don't exist. The wipe itself only touches files on disk, so the
    # preview must too.
    owners = _kept_frame_owners(project, video_stem)
    to_wipe_kept: Counter = Counter()
    to_preserve: Counter = Counter()
    prefix = f"{video_stem}__"
    if project.kept_dir.exists():
        from neme_anima.storage.project import CROP_SUFFIX
        for f in project.kept_dir.iterdir():
            if not f.is_file() or not f.name.startswith(prefix):
                continue
            if f.suffix != ".png":
                continue
            # Crop derivatives ride with the original's ownership and
            # are wiped/preserved alongside it — count one per logical
            # frame so the totals match the user-facing frames view.
            if f.stem.endswith(CROP_SUFFIX):
                continue
            owner = owners.get(f.stem)
            if owner is None:
                to_preserve["__untracked__"] += 1
            elif owner in preserve:
                to_preserve[owner] += 1
            else:
                to_wipe_kept[owner] += 1

    # Rejected files always wipe — count separately so the UI can
    # surface them as "diagnostic samples" distinct from character data.
    rejected_total = 0
    if project.rejected_dir.exists():
        for f in project.rejected_dir.iterdir():
            if f.is_file() and f.name.startswith(prefix):
                rejected_total += 1

    return {
        "video_stem": video_stem,
        "active_slugs": active_slugs,
        "preserve_slugs": sorted(preserve),
        "to_wipe": {
            "by_character": dict(to_wipe_kept),
            "rejected_samples": rejected_total,
            "total": int(sum(to_wipe_kept.values()) + rejected_total),
        },
        "to_preserve": {
            "by_character": dict(to_preserve),
            "total": int(sum(to_preserve.values())),
        },
    }


@router.post("/{slug}/sources/{idx}/extract", status_code=202)
async def extract(request: Request, slug: str, idx: int) -> dict:
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    job_id = await request.app.state.queue.submit({
        "kind": "extract",
        "project_folder": str(project.root.resolve()),
        "project_slug": project.slug,
        "source_idx": idx,
    })
    return {"job_id": job_id}


@router.post("/{slug}/sources/{idx}/rerun", status_code=202)
async def rerun(request: Request, slug: str, idx: int) -> dict:
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    video_stem = project.video_stem(idx)
    job_id = await request.app.state.queue.submit({
        "kind": "rerun",
        "project_folder": str(project.root.resolve()),
        "project_slug": project.slug,
        "source_idx": idx,
        "video_stem": video_stem,
    })
    return {"job_id": job_id}


@router.get("/{slug}/sources/{idx}/thumbnail")
async def get_thumbnail(request: Request, slug: str, idx: int) -> FileResponse:
    """Return a cached JPEG thumbnail for the source's video.

    The first request grabs one frame near 10 % of the video's duration via
    OpenCV, saves it under ``<project>/.thumbs/<stem>.jpg``, and serves it.
    Subsequent requests serve the cached file directly.
    """
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    video_path = Path(project.sources[idx].path)
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"video file missing: {video_path}")

    thumbs_dir = project.root / ".thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    cache_path = thumbs_dir / f"{video_path.stem}.jpg"
    if not cache_path.exists():
        try:
            await asyncio.to_thread(_extract_thumbnail, video_path, cache_path)
        except Exception as e:
            # Surface the real error to the server log, then to the client.
            logger.exception("thumbnail extraction failed for %s", video_path)
            raise HTTPException(
                status_code=500,
                detail=f"thumbnail extraction failed: {type(e).__name__}: {e}",
            )
    return FileResponse(cache_path, media_type="image/jpeg")


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


# Tracks in-flight preview transcodes by absolute video path so concurrent
# /preview calls don't kick off duplicate ffmpeg processes for the same
# source. Module-level singleton — the API server is single-process, so a
# plain dict is sufficient; no need for a cross-process lock file.
_PREVIEW_LOCKS: dict[str, asyncio.Lock] = {}

# Per-(path, mode) conversion job state, read by /convert/status. Mutated
# from the worker thread (plain dict writes are GIL-atomic) and from the
# request handler. Background tasks are held in _CONVERT_TASKS so the event
# loop doesn't garbage-collect them mid-run.
_CONVERT_JOBS: dict[tuple[str, str], dict[str, Any]] = {}
_CONVERT_TASKS: set[asyncio.Task] = set()


@router.get("/{slug}/sources/{idx}/duration")
async def get_duration(request: Request, slug: str, idx: int) -> dict:
    """Return ``{duration_seconds, fps}`` for the source's video.

    Cached on the Source record after the first probe so the segment
    editor doesn't pay a fresh ffprobe round-trip on every open. The probe
    falls through to legacy values (0.0) if ffprobe fails — better than a
    500 for a transient ffmpeg issue when the user just wants to look at
    a video they already extracted from.
    """
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    source = project.sources[idx]
    if source.duration_seconds is not None and source.fps is not None:
        return {
            "duration_seconds": source.duration_seconds,
            "fps": source.fps,
        }
    video_path = Path(source.path)
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"video file missing: {video_path}")
    duration = await asyncio.to_thread(_probe_duration_seconds, video_path)
    fps = await asyncio.to_thread(_probe_fps, video_path)
    source.duration_seconds = duration
    source.fps = fps
    project.save()
    return {"duration_seconds": duration, "fps": fps}


@router.post("/{slug}/sources/{idx}/capture-frame", status_code=201)
async def capture_frame(
    request: Request, slug: str, idx: int, body: CaptureFrameBody,
) -> dict:
    """Grab the exact frame at ``time_seconds`` and register it as a kept frame.

    The frame is WD14-tagged, and LLM-described when LLM tagging is enabled,
    then routed to ``character_slug`` (default: the project's first character).

    It reuses the same ingest path as the drag-and-drop upload route, so a
    captured frame is indistinguishable from a manually-added one: it lands
    under the ``custom_uploads`` stem and therefore survives a later
    Extract / Re-process of this source — the user hand-picked it, so a re-scan
    must not wipe it.

    The frame is read from the *original* file at full resolution regardless of
    whether the browser was playing the original stream or the 480p preview
    fallback, so HEVC/MKV sources the browser can't decode still yield a crisp
    training frame.
    """
    import tempfile

    from neme_anima.server.api.frames import (
        _get_or_make_tagger,
        ingest_kept_image,
    )

    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    source = project.sources[idx]
    video_path = Path(source.path)
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"video file missing: {video_path}")

    if (
        body.character_slug not in (None, "")
        and project.character_by_slug(body.character_slug) is None
    ):
        raise HTTPException(
            status_code=404, detail=f"unknown character: {body.character_slug}",
        )
    target_slug = (
        body.character_slug if body.character_slug
        else (project.characters[0].slug if project.characters else "default")
    )

    # Clamp the requested time into the clip. Use the cached duration when we
    # have it, pulling back a hair from the very end so ffmpeg always has a
    # frame to decode rather than landing past the last presentation timestamp.
    t = max(0.0, float(body.time_seconds))
    if source.duration_seconds and source.duration_seconds > 0:
        t = min(t, max(0.0, source.duration_seconds - 0.05))

    project.kept_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp_png = Path(td) / "capture.png"
        try:
            await asyncio.to_thread(_extract_frame_at, video_path, tmp_png, t)
        except Exception as e:
            logger.exception("frame capture failed for %s @ %.3fs", video_path, t)
            raise HTTPException(
                status_code=500,
                detail=f"frame capture failed: {type(e).__name__}: {e}",
            )
        data = tmp_png.read_bytes()

    tagger = _get_or_make_tagger(request)
    rec_dict, llm_error = await ingest_kept_image(
        project,
        data=data,
        filename_hint=f"{video_path.stem}_capture",
        target_slug=target_slug,
        tagger=tagger,
        timestamp_seconds=t,
    )
    if rec_dict is None:
        raise HTTPException(
            status_code=500, detail="could not process captured frame",
        )
    return {"frame": rec_dict, "llm_error": llm_error}


@router.get("/{slug}/sources/{idx}/stream")
async def stream_source(
    request: Request, slug: str, idx: int,
) -> FileResponse:
    """Serve the original video file with HTTP Range support.

    Starlette's :class:`FileResponse` honours the ``Range`` header
    out-of-the-box, so we just point at the file and set a reasonable
    ``Content-Type`` derived from the suffix. The frontend tries this
    endpoint first; if the browser can't decode the container/codec
    (HEVC in MKV being the common case for anime sources) it falls back
    to :func:`get_preview` which transcodes a web-playable copy.
    """
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    video_path = Path(project.sources[idx].path)
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"video file missing: {video_path}")
    media_type = _VIDEO_MIME_BY_SUFFIX.get(
        video_path.suffix.lower(), "application/octet-stream",
    )
    return FileResponse(
        video_path, media_type=media_type, filename=video_path.name,
    )


def _preview_cache_path(project: Project, video_path: Path, mode: str) -> Path:
    return project.root / ".previews" / f"{video_path.stem}.{mode}.mp4"


@router.post("/{slug}/sources/{idx}/convert")
async def convert_source(
    request: Request, slug: str, idx: int, mode: str = "h264",
) -> dict:
    """Kick off (or no-op join) a playback conversion for the given mode.

    Returns immediately with the job state; the browser polls
    :func:`convert_status` for progress and then loads :func:`get_preview`.
    Concurrent identical calls are idempotent — a running job is left alone and
    a finished cache file short-circuits.
    """
    if mode not in ("remux", "h264"):
        raise HTTPException(status_code=400, detail="mode must be 'remux' or 'h264'")
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    video_path = Path(project.sources[idx].path)
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"video file missing: {video_path}")

    cache_path = _preview_cache_path(project, video_path, mode)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    key = (str(video_path.resolve()), mode)

    if cache_path.exists() and cache_path.stat().st_size > 0:
        _CONVERT_JOBS[key] = {"state": "ready", "pct": 100.0, "mode": mode, "error": ""}
        return _CONVERT_JOBS[key]

    job = _CONVERT_JOBS.get(key)
    if job and job["state"] == "running":
        return job

    _CONVERT_JOBS[key] = {"state": "running", "pct": 0.0, "mode": mode, "error": ""}
    duration = await asyncio.to_thread(_probe_duration_seconds, video_path)
    task = asyncio.create_task(
        asyncio.to_thread(_run_convert_job, key, video_path, cache_path, mode, duration),
    )
    _CONVERT_TASKS.add(task)
    task.add_done_callback(_CONVERT_TASKS.discard)
    return _CONVERT_JOBS[key]


@router.get("/{slug}/sources/{idx}/convert/status")
async def convert_status(
    request: Request, slug: str, idx: int, mode: str = "h264",
) -> dict:
    """Report conversion progress: {state, pct, mode, error}.

    A present cache file always reads as ``ready`` (covers a server restart
    that dropped the in-memory job state). Otherwise the live job state, or
    ``idle`` if nothing was ever started for this (source, mode).
    """
    if mode not in ("remux", "h264"):
        raise HTTPException(status_code=400, detail="mode must be 'remux' or 'h264'")
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    video_path = Path(project.sources[idx].path)
    cache_path = _preview_cache_path(project, video_path, mode)
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return {"state": "ready", "pct": 100.0, "mode": mode, "error": ""}
    key = (str(video_path.resolve()), mode)
    return _CONVERT_JOBS.get(
        key, {"state": "idle", "pct": 0.0, "mode": mode, "error": ""},
    )


@router.get("/{slug}/sources/{idx}/preview")
async def get_preview(
    request: Request, slug: str, idx: int,
) -> Response:
    """Serve a lazy-transcoded 480p H.264 MP4 of the source.

    The frontend hits this endpoint when the original stream fails to
    decode in the browser. First call kicks off ffmpeg in a worker
    thread and returns ``202`` (with ``Retry-After``) while it runs;
    subsequent calls serve the cached ``.previews/<stem>.mp4`` directly,
    with full Range support so seeking is responsive.

    The transcode is intentionally aggressive (baseline H.264, CRF 28,
    short keyframe interval, faststart) — small enough to stream over
    a local network and broad enough to play in every browser; quality
    doesn't matter because the user is only using it to pick segment
    boundaries, not to view the final dataset.
    """
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    video_path = Path(project.sources[idx].path)
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"video file missing: {video_path}")

    previews_dir = project.root / ".previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    cache_path = previews_dir / f"{video_path.stem}.mp4"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return FileResponse(cache_path, media_type="video/mp4")

    key = str(video_path.resolve())
    lock = _PREVIEW_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PREVIEW_LOCKS[key] = lock

    # If another request is already transcoding this video, tell the
    # client to come back in a couple of seconds rather than queueing on
    # the lock (which would tie up the HTTP worker for tens of seconds).
    if lock.locked():
        return Response(
            status_code=202,
            headers={"Retry-After": "2"},
            content=(
                f'{{"status":"transcoding","stem":"{video_path.stem}"}}'
            ),
            media_type="application/json",
        )

    async with lock:
        # Re-check after acquiring — another coroutine may have raced us
        # and finished while we were waiting.
        if cache_path.exists() and cache_path.stat().st_size > 0:
            return FileResponse(cache_path, media_type="video/mp4")
        try:
            await asyncio.to_thread(_transcode_preview, video_path, cache_path)
        except Exception as e:
            logger.exception("preview transcode failed for %s", video_path)
            raise HTTPException(
                status_code=500,
                detail=f"preview transcode failed: {type(e).__name__}: {e}",
            )
    return FileResponse(cache_path, media_type="video/mp4")


def _convert_cmd(
    video_path: Path, dest: Path, mode: str, *, with_audio: bool,
) -> list[str]:
    """Build the ffmpeg argv for a playback conversion.

    ``remux`` copies the (HEVC) video stream bit-for-bit into MP4 — zero
    quality loss, near-instant — for browsers that can decode HEVC. ``h264``
    re-encodes to a small 480p H.264 baseline stream (the old throwaway-preview
    recipe) for browsers that can't. Both stream progress on stdout via
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
            # h264 relies on ffmpeg's automatic stream selection (no explicit -map),
            # matching the _transcode_preview convention; remux uses explicit -map 0:a:0?.
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
    """
    import subprocess

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
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
        rc = proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
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


def _transcode_preview(video_path: Path, dest: Path) -> None:
    """Run ffmpeg to produce a small H.264 baseline MP4 with faststart.

    Tmp-then-rename so a partial output never registers as "ready" in the
    presence of a crash / kill — the next request will retry.
    """
    import shutil as _shutil
    import subprocess

    if _shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    # Use a ``.tmp.mp4`` extension (rather than the simpler ``.part``)
    # so ffmpeg's container detection picks the right muxer for the
    # output. ``-f mp4`` would also work, but a real ``.mp4`` suffix is
    # easier to spot in case a crash leaves a stray temp file behind.
    tmp = dest.with_name(dest.stem + ".tmp" + dest.suffix)
    cmd_with_audio = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(video_path),
        "-vf", "scale='min(854,iw)':-2",
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.1",
        "-preset", "veryfast",
        "-crf", "28",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        str(tmp),
    ]
    # Audio is best-effort — some sources have unusual audio streams that
    # ffmpeg's aac encoder refuses, and others (like the lavfi testsrc
    # used in tests) have no audio at all. Retry without audio if the
    # first pass fails before giving up.
    res = subprocess.run(cmd_with_audio, capture_output=True, text=True, timeout=600)
    if res.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        cmd_no_audio = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", str(video_path),
            "-vf", "scale='min(854,iw)':-2",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.1",
            "-preset", "veryfast",
            "-crf", "28",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            "-an",
            str(tmp),
        ]
        res = subprocess.run(cmd_no_audio, capture_output=True, text=True, timeout=600)

    if res.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        stderr = (res.stderr or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"ffmpeg failed (rc={res.returncode}): {stderr[0][:300]}")

    tmp.replace(dest)


@router.put("/{slug}/sources/{idx}/segments")
async def put_segments(
    request: Request, slug: str, idx: int, body: PutSegmentsBody,
) -> dict:
    """Replace the source's segment list. Validates, sorts, merges, persists.

    Defensive validation: even though the UI prevents most of these, the
    server is the source of truth.
      * ``start < end`` with both non-negative.
      * Both within the video's duration (probed if not already cached).
      * Overlapping or touching ranges are merged so the persisted shape
        is canonical (sorted, disjoint, non-empty).

    Returns ``{segments: [...]}`` so the client can confirm exactly what
    landed without a follow-up GET.
    """
    project = _load(request, slug)
    if idx < 0 or idx >= len(project.sources):
        raise HTTPException(status_code=404, detail="source index out of range")
    source = project.sources[idx]
    video_path = Path(source.path)
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail=f"video file missing: {video_path}")

    duration = source.duration_seconds
    if duration is None or duration <= 0:
        duration = await asyncio.to_thread(_probe_duration_seconds, video_path)
        if duration > 0:
            source.duration_seconds = duration
    # If duration is still unknown (probe failed), accept the segments as
    # given without an upper-bound check rather than rejecting everything.
    upper = duration if duration > 0 else None

    raw_ranges: list[tuple[float, float]] = []
    for seg in body.segments:
        start = float(seg.start_seconds)
        end = float(seg.end_seconds)
        if start < 0 or end <= start:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"invalid segment [{start}, {end}): need 0 <= start < end"
                ),
            )
        if upper is not None and end > upper + 0.05:  # 50 ms tolerance
            raise HTTPException(
                status_code=400,
                detail=(
                    f"segment end {end:.3f}s exceeds video duration "
                    f"{upper:.3f}s"
                ),
            )
        raw_ranges.append((start, min(end, upper) if upper is not None else end))

    merged = _merge_ranges(raw_ranges)
    source.segments = [
        Segment(start_seconds=round(a, 3), end_seconds=round(b, 3))
        for (a, b) in merged
    ]
    project.save()
    return {
        "segments": [
            {"start_seconds": s.start_seconds, "end_seconds": s.end_seconds}
            for s in source.segments
        ],
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
