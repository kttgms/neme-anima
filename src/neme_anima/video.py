"""Video I/O and scene detection.

Wraps decord for fast batched frame reads and scenedetect for shot boundaries.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from decord import VideoReader, cpu
from scenedetect import ContentDetector, FrameTimecode, SceneManager, open_video


@dataclass(frozen=True)
class Scene:
    """A continuous shot in the video, half-open frame range [start_frame, end_frame)."""
    index: int
    start_frame: int
    end_frame: int

    @property
    def num_frames(self) -> int:
        return self.end_frame - self.start_frame

    def duration_seconds(self, fps: float) -> float:
        return self.num_frames / fps


class Video:
    """Lazy random-access video reader returning RGB uint8 ndarrays."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._vr = VideoReader(str(self.path), ctx=cpu(0))
        self.fps = float(self._vr.get_avg_fps())
        self.num_frames = len(self._vr)

    # ------------------------------------------------------------------
    # Context-manager support — allows ``with Video(path) as vid:``
    # which guarantees the underlying VideoReader file handle is released
    # even if an exception occurs mid-pipeline.
    # ------------------------------------------------------------------

    def __enter__(self) -> "Video":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Explicitly release the underlying VideoReader file handle.

        decord's VideoReader keeps an open file descriptor for the life of
        the object. When the pipeline creates a new ``Video`` instance per
        ``run_extract`` call, the old instance is not collected immediately
        by CPython's GC (reference cycles inside the C extension can delay
        finalization). Calling ``close()`` (or using ``Video`` as a context
        manager) ensures the descriptor is returned promptly.
        """
        vr = getattr(self, "_vr", None)
        if vr is not None:
            # decord does not expose an explicit close/release API, but
            # deleting the reference lets the C extension destructor run
            # immediately in CPython's reference-counting GC.
            del self._vr
            self._vr = None  # type: ignore[assignment]

    @property
    def duration_seconds(self) -> float:
        return self.num_frames / self.fps if self.fps > 0 else 0.0

    def __len__(self) -> int:
        return self.num_frames

    def get(self, idx: int) -> np.ndarray:
        """Return a single frame as HxWx3 uint8 RGB."""
        return self._vr[idx].asnumpy()

    def get_batch(self, indices: list[int]) -> np.ndarray:
        """Return frames as NxHxWx3 uint8 RGB."""
        if not indices:
            return np.empty((0, 0, 0, 3), dtype=np.uint8)
        return self._vr.get_batch(indices).asnumpy()

    def iter_frames(
        self,
        start: int = 0,
        end: int | None = None,
        stride: int = 1,
        batch_size: int = 32,
    ) -> Iterator[tuple[int, np.ndarray]]:
        """Yield (frame_idx, frame_rgb) for frame_idx in range(start, end, stride),
        reading in batches of ``batch_size`` for throughput.
        """
        if end is None:
            end = self.num_frames
        end = min(end, self.num_frames)
        if stride < 1:
            raise ValueError("stride must be >= 1")
        idxs = list(range(start, end, stride))
        for batch_start in range(0, len(idxs), batch_size):
            batch_idxs = idxs[batch_start: batch_start + batch_size]
            frames = self.get_batch(batch_idxs)
            for fi, frame in zip(batch_idxs, frames):
                yield fi, frame


def detect_scenes(
    video_path: Path,
    *,
    content_threshold: float = 27.0,
    min_scene_len_frames: int = 8,
    time_ranges: list[tuple[int, int]] | None = None,
) -> list[Scene]:
    """Detect shot boundaries with PySceneDetect's ContentDetector.

    Returns scenes as half-open frame ranges. Always returns at least one
    scene spanning the requested area, even if no cuts are detected.

    ``time_ranges`` (half-open ``[start_frame, end_frame)`` per entry, in
    absolute video frames) restricts detection to those windows: each
    range is scanned independently via seek + duration. Indices are
    assigned sequentially in start-frame order. ``None`` runs the full
    video — byte-identical to the legacy path.

    Quality: ContentDetector is a purely local HSV-delta-between-
    consecutive-frames algorithm, so per-window scans match a whole-video
    scan clipped to the same windows, modulo cuts within
    ``min_scene_len_frames`` of a window start (the per-window cold start
    suppresses an immediate cut that the whole-video pass would emit).
    The user-chosen window boundary is already a scene boundary in the
    output regardless, so the tradeoff is invisible downstream.
    """
    if time_ranges is None:
        return _detect_scenes_full(
            video_path,
            content_threshold=content_threshold,
            min_scene_len_frames=min_scene_len_frames,
        )
    out: list[Scene] = []
    next_idx = 0
    for rs, re_ in sorted(time_ranges):
        if re_ <= rs:
            continue
        out.extend(
            _detect_scenes_window(
                video_path,
                start_frame=rs,
                end_frame=re_,
                content_threshold=content_threshold,
                min_scene_len_frames=min_scene_len_frames,
                start_index=next_idx,
            )
        )
        next_idx = len(out)
    return out


def _detect_scenes_full(
    video_path: Path,
    *,
    content_threshold: float,
    min_scene_len_frames: int,
) -> list[Scene]:
    pys_video = open_video(str(video_path))
    try:
        sm = SceneManager()
        sm.add_detector(
            ContentDetector(threshold=content_threshold, min_scene_len=min_scene_len_frames)
        )
        sm.detect_scenes(pys_video, show_progress=False)
        raw = sm.get_scene_list()
    finally:
        # open_video() returns a VideoStream that holds a file handle.
        # Release it immediately after detect_scenes() so it is not held
        # for the rest of the pipeline run.
        try:
            pys_video.release()
        except Exception:  # noqa: BLE001
            pass
    scenes: list[Scene] = []
    if not raw:
        # No cuts found — entire video is one scene.
        with Video(video_path) as v:
            scenes.append(Scene(index=0, start_frame=0, end_frame=v.num_frames))
        return scenes
    for i, (start_tc, end_tc) in enumerate(raw):
        scenes.append(
            Scene(
                index=i,
                start_frame=int(start_tc.get_frames()),
                end_frame=int(end_tc.get_frames()),
            )
        )
    return scenes


def _detect_scenes_window(
    video_path: Path,
    *,
    start_frame: int,
    end_frame: int,
    content_threshold: float,
    min_scene_len_frames: int,
    start_index: int,
) -> list[Scene]:
    """Scan a single ``[start_frame, end_frame)`` window for cuts.

    A fresh ``VideoStream`` + ``SceneManager`` per window keeps state
    isolated (detector deltas across window gaps would be meaningless).
    Falls back to a single scene covering the window when seek fails or
    scenedetect finds no cuts — never returns an empty list for a non-
    empty input range so the caller can rely on coverage being total.
    """
    pys_video = open_video(str(video_path))
    fps = pys_video.frame_rate
    try:
        try:
            pys_video.seek(FrameTimecode(start_frame, fps))
        except Exception:
            # Out-of-range seek: synthesize the window as a single scene so
            # the caller's coverage stays total. Downstream stages will read
            # back nothing if the range is truly past the video end, but the
            # pipeline raises a clearer error on empty results elsewhere.
            return [Scene(index=start_index, start_frame=start_frame, end_frame=end_frame)]
        sm = SceneManager()
        sm.add_detector(
            ContentDetector(threshold=content_threshold, min_scene_len=min_scene_len_frames)
        )
        sm.detect_scenes(
            pys_video,
            duration=FrameTimecode(end_frame - start_frame, fps),
            show_progress=False,
        )
        raw = sm.get_scene_list()
    finally:
        # Release the VideoStream file handle after each window scan.
        try:
            pys_video.release()
        except Exception:  # noqa: BLE001
            pass
    if not raw:
        return [Scene(index=start_index, start_frame=start_frame, end_frame=end_frame)]
    out: list[Scene] = []
    idx = start_index
    for s_tc, e_tc in raw:
        # Clamp to the requested window — scenedetect's duration ceiling
        # can land a frame past end_frame on rounding, and a seek that
        # landed early on a non-keyframe can put start before start_frame.
        s_f = max(int(s_tc.get_frames()), start_frame)
        e_f = min(int(e_tc.get_frames()), end_frame)
        if e_f > s_f:
            out.append(Scene(index=idx, start_frame=s_f, end_frame=e_f))
            idx += 1
    if not out:
        return [Scene(index=start_index, start_frame=start_frame, end_frame=end_frame)]
    return out
