"""Per-video extraction cache metadata + freshness comparison.

The detection pipeline (scenes → detect → track) is the expensive phase of
``run_extract``: it scales with video length and is dominated by ONNX/YOLO
inference. The output (scenes.parquet + tracklets.parquet) lives in
``<project>/output/cache/<video_stem>/`` and lets ``run_rerun`` skip those
stages and re-evaluate identification + selection + tagging only.

The catch: the cache is only valid as long as the scene / detect / track
thresholds that produced it match the *current* thresholds. If the user
tightens ``track_thresh`` and reruns, they'll get the old tracks. The UI
hides that footgun by disabling Re-process when the cache is stale and
disabling Extract when there's nothing scan-affecting to redo.

This module is the bookkeeping for that:

  * :class:`ExtractionCacheMeta` is the snapshot stamped at extract time.
  * :func:`stamp_meta` writes it to ``cache/<video_stem>/extraction_meta.json``.
  * :func:`cache_state` returns one of "none" / "current" / "stale" given
    the current thresholds — what the UI consumes to decide button states.
  * :class:`CacheInfo` is a richer descriptor for the UI that includes
    scene/tracklet counts, disk size, stale reason, and the threshold
    snapshot that produced the cache.
  * :func:`cache_info` returns a :class:`CacheInfo` or ``None``.
  * :func:`purge_cache` deletes the local cache for a single video.
  * :func:`list_caches` returns :class:`CacheInfo` for every cached video.

Global cache:
  * Detection/tracking results can optionally be stored in a shared global
    cache under ``~/.neme-anima/scan_cache/`` so multiple projects that use
    the same video don’t re-run the expensive scan. The key is derived from
    the video file’s resolved path + mtime + size + threshold snapshot.
  * :func:`stamp_global` writes to the global cache.
  * :func:`lookup_global` reads from the global cache.
  * :func:`purge_global` deletes a specific global-cache entry.

Only the scene / detect / track sections are tracked. Identification,
frame selection, cropping, dedup, and tagging are all re-applied by
Re-process, so changes to those don't invalidate the cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from neme_anima.config import DetectConfig, SceneConfig, Thresholds, TrackConfig
from neme_anima.storage.project import Project

logger = logging.getLogger(__name__)

# Bumped whenever the meta schema changes incompatibly. Old metas without
# this field (or with a different number) read as "stale" so the user is
# guided into a fresh extract that re-stamps the file.
_META_VERSION = 1

CacheState = Literal["none", "current", "stale"]


@dataclass(frozen=True)
class ExtractionCacheMeta:
    """Threshold snapshot taken at the end of a successful Extract run.

    Stamped in ``cache/<video_stem>/extraction_meta.json``. Re-read at UI
    refresh time to compute :func:`cache_state`. ``stamped_at`` is purely
    informational — the freshness comparison is content-based, not time-
    based, so a quick edit-and-revert cycle correctly reads as "current"
    even if minutes pass.

    ``segments`` is the list of ``{start_seconds, end_seconds}`` dicts the
    user had configured on the source at extract time. Empty list = the
    whole video was processed (legacy / default). Editing segments after
    extraction therefore flips the cache to ``stale``.
    """
    version: int
    scene: dict
    detect: dict
    track: dict
    segments: list[dict]
    stamped_at: str  # ISO-8601 UTC

    @classmethod
    def from_thresholds(
        cls, t: Thresholds, *, segments: list[dict] | None = None,
    ) -> ExtractionCacheMeta:
        # Store as plain dicts (not the normalized tuple form) so the on-disk
        # JSON shape matches the ``list[dict]`` field type and round-trips
        # through ``_normalize_segments`` on reload. Older snapshots written
        # before this fix landed contain ``[start, end]`` lists; the loader
        # tolerates both via ``_normalize_segments``.
        normalized = _normalize_segments(segments or [])
        return cls(
            version=_META_VERSION,
            scene=asdict(t.scene),
            detect=asdict(t.detect),
            track=asdict(t.track),
            segments=[
                {"start_seconds": start, "end_seconds": end}
                for start, end in normalized
            ],
            stamped_at=datetime.now(UTC).isoformat(),
        )

    def matches(
        self, t: Thresholds, *, segments: list[dict] | None = None,
    ) -> bool:
        """True iff every scan-affecting threshold (and the segment list)
        matches what's in ``t`` / ``segments``.

        Equality is by-field rather than full-dataclass so a future
        non-cache-invalidating field added to one of the dataclasses
        won't be required to round-trip through old meta files. Old
        fields removed from the dataclass will quietly drop here too —
        that's fine; they couldn't have affected the current run.
        """
        if self.version != _META_VERSION:
            return False
        if _normalize_segments(segments or []) != _normalize_segments(self.segments):
            return False
        return (
            _section_matches(self.scene, asdict(t.scene), SceneConfig)
            and _section_matches(self.detect, asdict(t.detect), DetectConfig)
            and _section_matches(self.track, asdict(t.track), TrackConfig)
        )


def _normalize_segments(segs: list) -> list[tuple[float, float]]:
    """Canonical segment representation for equality comparison.

    Rounded to 1 ms to avoid float-jitter false positives, then sorted so
    list order can't matter. Accepts either the live dict shape
    (``{"start_seconds": ..., "end_seconds": ...}``) or the legacy
    ``[start, end]`` list/tuple shape that older meta files on disk persist
    — the JSON round-trip used to turn the normalized tuples back into
    lists, so any pre-fix snapshot reads as a list-of-lists.
    """
    norm: list[tuple[float, float]] = []
    for s in segs:
        try:
            if isinstance(s, dict):
                start = round(float(s.get("start_seconds", 0.0)), 3)
                end = round(float(s.get("end_seconds", 0.0)), 3)
            elif isinstance(s, (list, tuple)) and len(s) >= 2:
                start = round(float(s[0]), 3)
                end = round(float(s[1]), 3)
            else:
                continue
        except (TypeError, ValueError):
            continue
        norm.append((start, end))
    norm.sort()
    return norm


def _section_matches(stamped: dict, current: dict, dc_cls: type) -> bool:
    """Compare ``stamped`` and ``current`` only for fields the dataclass
    currently declares — older snapshot keys outside the schema are
    ignored, missing keys count as mismatches.
    """
    for f in fields(dc_cls()):
        if f.name not in current:
            # Defensive — current Thresholds always carries every field,
            # but a future schema change could drop one. Tolerate.
            continue
        if stamped.get(f.name) != current[f.name]:
            return False
    return True


def _meta_path_for(project: Project, video_stem: str) -> Path:
    return project.cache_dir_for(video_stem) / "extraction_meta.json"


def stamp_meta(project: Project, video_stem: str, thresholds: Thresholds) -> Path:
    """Write the freshness snapshot for ``video_stem``.

    Creates the cache dir if needed (it should already exist after
    write_scenes / write_tracklets, but defensive). Atomic via tmp+rename
    so a partial write never poisons cache_state into reading garbage.
    The current source's segments are folded into the snapshot so that
    later edits to segments correctly invalidate the cache.
    """
    cache_dir = project.cache_dir_for(video_stem)
    cache_dir.mkdir(parents=True, exist_ok=True)
    segments = _segments_for_stem(project, video_stem)
    meta = ExtractionCacheMeta.from_thresholds(thresholds, segments=segments)
    target = _meta_path_for(project, video_stem)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def _segments_for_stem(project: Project, video_stem: str) -> list[dict]:
    """Return the source's segments as plain dicts for snapshotting.

    Looked up by video_stem (same key the cache directory uses). Returns
    ``[]`` when no source matches (e.g. the source was deleted but its
    cache still exists), which is harmless: the next freshness check
    against an empty current-segment list will still match itself.
    """
    for s in project.sources:
        if Path(s.path).stem == video_stem:
            return [
                {"start_seconds": seg.start_seconds, "end_seconds": seg.end_seconds}
                for seg in s.segments
            ]
    return []


def _read_meta(project: Project, video_stem: str) -> ExtractionCacheMeta | None:
    path = _meta_path_for(project, video_stem)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # Field-set load so an older meta missing one of the keys reads as
    # "no version" → handled as stale, and a newer meta with extra keys
    # silently drops them.
    return ExtractionCacheMeta(
        version=int(raw.get("version", 0)),
        scene=dict(raw.get("scene") or {}),
        detect=dict(raw.get("detect") or {}),
        track=dict(raw.get("track") or {}),
        segments=list(raw.get("segments") or []),
        stamped_at=str(raw.get("stamped_at") or ""),
    )


def cache_state(
    *, project: Project, video_stem: str, current_thresholds: Thresholds,
) -> CacheState:
    """Three-way state used by the UI to enable/disable Extract vs Re-process.

      * ``"none"``: no usable cache yet. Extract is the only option.
      * ``"current"``: cache exists and the scan-affecting thresholds
        haven't changed since it was stamped — Re-process is sufficient
        and Extract becomes a no-op the UI hides.
      * ``"stale"``: cache exists but at least one scan-affecting
        threshold differs. Re-process would silently use the old
        detections; the UI surfaces a warning and steers the user to
        Extract.

    A ``tracklets.parquet`` without a paired ``extraction_meta.json`` is
    treated as stale — it's almost certainly from a pre-stamping version
    of the pipeline, and we'd rather prompt a fresh extract that produces
    the snapshot than reuse a cache we can't validate.
    """
    cache_dir = project.cache_dir_for(video_stem)
    parquet = cache_dir / "tracklets.parquet"
    if not parquet.is_file():
        return "none"
    meta = _read_meta(project, video_stem)
    if meta is None:
        return "stale"
    current_segments = _segments_for_stem(project, video_stem)
    if meta.matches(current_thresholds, segments=current_segments):
        return "current"
    return "stale"


def cache_state_for_source(
    project: Project, source_idx: int, current_thresholds: Thresholds,
) -> CacheState:
    """Same as :func:`cache_state` keyed by source index — the shape the
    project view uses when rendering one row per source."""
    if source_idx < 0 or source_idx >= len(project.sources):
        return "none"
    video_stem = project.video_stem(source_idx)
    return cache_state(
        project=project,
        video_stem=video_stem,
        current_thresholds=current_thresholds,
    )


# ---------------------------------------------------------------------------
# Stale-reason diffing
# ---------------------------------------------------------------------------

def _diff_sections(stamped: dict, current: dict, dc_cls: type) -> list[str]:
    """Return the list of field names where ``stamped`` and ``current`` disagree.

    Used by :func:`cache_info` to build a human-readable stale reason.
    """
    diffs: list[str] = []
    for f in fields(dc_cls()):
        if f.name not in current:
            continue
        if stamped.get(f.name) != current[f.name]:
            diffs.append(f.name)
    return diffs


def _stale_reason(
    meta: ExtractionCacheMeta, t: Thresholds,
    *, segments: list[dict] | None = None,
) -> str | None:
    """Human-readable explanation of why the cache is stale, or ``None``."""
    if meta.version != _META_VERSION:
        return f"cache version mismatch (cached {meta.version}, expected {_META_VERSION})"
    if _normalize_segments(segments or []) != _normalize_segments(meta.segments):
        return "segments changed"
    reasons: list[str] = []
    for section_name, dc_cls in [
        ("scene", SceneConfig), ("detect", DetectConfig), ("track", TrackConfig),
    ]:
        stamped = getattr(meta, section_name, {})
        current = asdict(getattr(t, section_name))
        diffs = _diff_sections(stamped, current, dc_cls)
        for d in diffs:
            reasons.append(f"{section_name}.{d}")
    return ", ".join(reasons) if reasons else None


# ---------------------------------------------------------------------------
# CacheInfo — rich descriptor for the UI
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CacheInfo:
    """Detailed cache information for a single video.

    Exposed by the ``GET /cache`` endpoint so the UI can render badges,
    popovers, and purge buttons.
    """
    video_stem: str
    state: CacheState
    stamped_at: str | None
    num_scenes: int
    num_tracklets: int
    size_bytes: int
    stale_reason: str | None
    thresholds_snapshot: dict | None


def _parquet_stats(cache_dir: Path) -> tuple[int, int, int]:
    """Return ``(num_scenes, num_tracklets, total_bytes)`` from parquet files.

    Falls back to zeros if files are unreadable — this is a best-effort
    information helper, not a correctness-critical path.
    """
    num_scenes = 0
    num_tracklets = 0
    total_bytes = 0
    scenes_pq = cache_dir / "scenes.parquet"
    tracklets_pq = cache_dir / "tracklets.parquet"
    if scenes_pq.is_file():
        total_bytes += scenes_pq.stat().st_size
        try:
            import pandas as pd
            df = pd.read_parquet(scenes_pq)
            num_scenes = len(df)
        except Exception:  # noqa: BLE001
            pass
    if tracklets_pq.is_file():
        total_bytes += tracklets_pq.stat().st_size
        try:
            import pandas as pd
            df = pd.read_parquet(tracklets_pq)
            num_tracklets = len(df.groupby(["scene_idx", "tracklet_id"])) if not df.empty else 0
        except Exception:  # noqa: BLE001
            pass
    meta_json = cache_dir / "extraction_meta.json"
    if meta_json.is_file():
        total_bytes += meta_json.stat().st_size
    return num_scenes, num_tracklets, total_bytes


def cache_info(
    project: Project, video_stem: str,
    current_thresholds: Thresholds | None = None,
) -> CacheInfo | None:
    """Return a :class:`CacheInfo` for ``video_stem``, or ``None`` if no
    cache files exist at all (not even a directory)."""
    cache_dir = project.cache_dir_for(video_stem)
    if not cache_dir.is_dir():
        return None
    parquet = cache_dir / "tracklets.parquet"
    if not parquet.is_file():
        return None
    meta = _read_meta(project, video_stem)
    thresholds = current_thresholds or Thresholds()
    state = cache_state(
        project=project, video_stem=video_stem,
        current_thresholds=thresholds,
    )
    stale_reason = None
    if state == "stale" and meta is not None:
        current_segments = _segments_for_stem(project, video_stem)
        stale_reason = _stale_reason(meta, thresholds, segments=current_segments)
    num_scenes, num_tracklets, size_bytes = _parquet_stats(cache_dir)
    return CacheInfo(
        video_stem=video_stem,
        state=state,
        stamped_at=meta.stamped_at if meta else None,
        num_scenes=num_scenes,
        num_tracklets=num_tracklets,
        size_bytes=size_bytes,
        stale_reason=stale_reason,
        thresholds_snapshot={
            "scene": meta.scene, "detect": meta.detect, "track": meta.track,
        } if meta else None,
    )


def purge_cache(project: Project, video_stem: str) -> bool:
    """Delete the local cache for ``video_stem``. Returns True if deleted."""
    cache_dir = project.cache_dir_for(video_stem)
    if not cache_dir.is_dir():
        return False
    shutil.rmtree(cache_dir, ignore_errors=True)
    return True


def list_caches(
    project: Project, current_thresholds: Thresholds | None = None,
) -> list[CacheInfo]:
    """Return :class:`CacheInfo` for every video that has cache files."""
    cache_root = project.root / "output" / "cache"
    if not cache_root.is_dir():
        return []
    infos: list[CacheInfo] = []
    for d in sorted(cache_root.iterdir()):
        if not d.is_dir():
            continue
        info = cache_info(project, d.name, current_thresholds)
        if info is not None:
            infos.append(info)
    return infos


# ---------------------------------------------------------------------------
# Global scan cache — shared across projects
# ---------------------------------------------------------------------------

def _default_global_cache_dir() -> Path:
    """``~/.neme-anima/scan_cache/``."""
    d = Path(os.environ.get("NEME_SCAN_CACHE_DIR", ""))
    if d and d != Path():
        return d
    return Path.home() / ".neme-anima" / "scan_cache"


def _global_cache_key(
    video_path: Path, thresholds: Thresholds,
    *, segments: list[dict] | None = None,
) -> str:
    """Derive a deterministic directory name from video identity + thresholds.

    Identity is ``resolved_path + mtime + size`` — cheap to compute and
    accurate as long as the file isn’t replaced in-place (which would be
    user error for a multi-GB anime source). Thresholds are the scan-
    affecting subset (scene/detect/track + segments). The digest is
    truncated to 24 hex chars — collision-free in any realistic corpus.
    """
    video_path = Path(video_path).resolve()
    try:
        st = video_path.stat()
        mtime = st.st_mtime
        size = st.st_size
    except OSError:
        mtime = 0.0
        size = 0
    identity = {
        "path": str(video_path),
        "mtime": mtime,
        "size": size,
        "scene": asdict(thresholds.scene),
        "detect": asdict(thresholds.detect),
        "track": asdict(thresholds.track),
        "segments": _normalize_segments(segments or []),
    }
    blob = json.dumps(identity, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def stamp_global(
    video_path: Path, thresholds: Thresholds,
    *, local_cache_dir: Path,
    segments: list[dict] | None = None,
    global_cache_dir: Path | None = None,
) -> Path | None:
    """Copy the local cache to the global scan cache.

    Returns the global directory on success, ``None`` if writing fails
    (disk full, permissions, etc.) — the pipeline must never fail because
    of a global-cache issue.
    """
    gcdir = global_cache_dir or _default_global_cache_dir()
    key = _global_cache_key(video_path, thresholds, segments=segments)
    target = gcdir / key
    try:
        target.mkdir(parents=True, exist_ok=True)
        for name in ("scenes.parquet", "tracklets.parquet", "extraction_meta.json"):
            src = local_cache_dir / name
            if src.is_file():
                shutil.copy2(src, target / name)
        # Write a small provenance file so humans can inspect what’s cached.
        prov = {
            "video_path": str(Path(video_path).resolve()),
            "stamped_at": datetime.now(UTC).isoformat(),
            "key": key,
        }
        (target / "provenance.json").write_text(
            json.dumps(prov, indent=2), encoding="utf-8",
        )
        logger.info("global cache stamped: %s -> %s", video_path.name, key)
        return target
    except Exception:  # noqa: BLE001
        logger.warning("failed to stamp global cache for %s", video_path.name, exc_info=True)
        return None


def lookup_global(
    video_path: Path, thresholds: Thresholds,
    *, segments: list[dict] | None = None,
    global_cache_dir: Path | None = None,
) -> Path | None:
    """Look up a global-cache hit for the given video + thresholds.

    Returns the global directory containing ``scenes.parquet`` and
    ``tracklets.parquet`` on hit, ``None`` on miss.
    """
    gcdir = global_cache_dir or _default_global_cache_dir()
    key = _global_cache_key(video_path, thresholds, segments=segments)
    target = gcdir / key
    if (
        target.is_dir()
        and (target / "tracklets.parquet").is_file()
        and (target / "extraction_meta.json").is_file()
    ):
        return target
    return None


def restore_from_global(
    video_path: Path, thresholds: Thresholds,
    *, local_cache_dir: Path,
    segments: list[dict] | None = None,
    global_cache_dir: Path | None = None,
) -> bool:
    """Copy a global-cache hit into the project’s local cache.

    Returns True if the restore succeeded. Silently returns False on any
    failure so the pipeline can fall through to a fresh scan.
    """
    hit = lookup_global(
        video_path, thresholds,
        segments=segments,
        global_cache_dir=global_cache_dir,
    )
    if hit is None:
        return False
    try:
        local_cache_dir.mkdir(parents=True, exist_ok=True)
        for name in ("scenes.parquet", "tracklets.parquet", "extraction_meta.json"):
            src = hit / name
            if src.is_file():
                shutil.copy2(src, local_cache_dir / name)
        logger.info(
            "restored global cache for %s into %s",
            video_path.name, local_cache_dir,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning(
            "failed to restore global cache for %s", video_path.name, exc_info=True,
        )
        return False


def purge_global(
    video_path: Path, thresholds: Thresholds,
    *, segments: list[dict] | None = None,
    global_cache_dir: Path | None = None,
) -> bool:
    """Delete a specific global-cache entry. Returns True if deleted."""
    gcdir = global_cache_dir or _default_global_cache_dir()
    key = _global_cache_key(video_path, thresholds, segments=segments)
    target = gcdir / key
    if not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return True


def list_global_caches(
    global_cache_dir: Path | None = None,
) -> list[dict]:
    """Return provenance info for every entry in the global scan cache."""
    gcdir = global_cache_dir or _default_global_cache_dir()
    if not gcdir.is_dir():
        return []
    entries: list[dict] = []
    for d in sorted(gcdir.iterdir()):
        if not d.is_dir():
            continue
        prov_path = d / "provenance.json"
        if prov_path.is_file():
            try:
                prov = json.loads(prov_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prov = {"key": d.name}
        else:
            prov = {"key": d.name}
        prov["size_bytes"] = sum(
            f.stat().st_size for f in d.iterdir() if f.is_file()
        )
        entries.append(prov)
    return entries


# Public for tests that want to inspect the snapshot without re-reading
# the file.
__all__ = [
    "CacheInfo",
    "CacheState",
    "ExtractionCacheMeta",
    "cache_info",
    "cache_state",
    "cache_state_for_source",
    "list_caches",
    "lookup_global",
    "purge_cache",
    "purge_global",
    "restore_from_global",
    "stamp_global",
    "stamp_meta",
]
