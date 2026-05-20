"""Tests for /api/projects/{slug}/sources routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from neme_anima.server.app import create_app
from neme_anima.storage.project import Project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.create(tmp_path / "p", name="p")


@pytest.fixture
def app(tmp_path: Path, project: Project):
    a = create_app(state_dir=tmp_path / "state")
    a.state.registry.register(project)
    return a


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_add_source(client, project: Project, tmp_path: Path):
    vid = tmp_path / "ep01.mkv"
    vid.write_bytes(b"")
    resp = await client.post(
        f"/api/projects/{project.slug}/sources",
        json={"paths": [str(vid)]},
    )
    assert resp.status_code == 200
    reloaded = Project.load(project.root)
    assert len(reloaded.sources) == 1


async def test_add_source_skips_duplicates(client, project: Project, tmp_path: Path):
    vid = tmp_path / "ep01.mkv"
    vid.write_bytes(b"")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    resp = await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    assert resp.status_code == 200
    body = resp.json()
    # Endpoint reports skipped, not error.
    assert "skipped" in body


async def test_remove_source(client, project: Project, tmp_path: Path):
    vid = tmp_path / "ep01.mkv"
    vid.write_bytes(b"")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    resp = await client.delete(f"/api/projects/{project.slug}/sources/0")
    assert resp.status_code == 204
    reloaded = Project.load(project.root)
    assert reloaded.sources == []


async def test_patch_excluded_refs(client, project: Project, tmp_path: Path):
    vid = tmp_path / "ep01.mkv"; vid.write_bytes(b"")
    img = tmp_path / "ref.png"; img.write_bytes(b"")
    project.add_ref(img)
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    resp = await client.patch(
        f"/api/projects/{project.slug}/sources/0",
        json={"excluded_refs": [str(img.resolve())]},
    )
    assert resp.status_code == 200
    reloaded = Project.load(project.root)
    # excluded_refs is now a per-character map; this PATCH (no character_slug)
    # targets the default character.
    assert reloaded.sources[0].excluded_refs == {"default": [str(img.resolve())]}


async def test_add_source_accepts_file_uri(client, project: Project, tmp_path: Path):
    vid = tmp_path / "Show E01.mkv"
    vid.write_bytes(b"")
    uri = f"file://{vid.as_posix().replace(' ', '%20')}"
    resp = await client.post(
        f"/api/projects/{project.slug}/sources",
        json={"paths": [uri]},
    )
    assert resp.status_code == 200
    reloaded = Project.load(project.root)
    assert len(reloaded.sources) == 1
    assert Path(reloaded.sources[0].path) == vid.resolve()


async def test_add_source_skips_browser_vfs_sentinel(client, project: Project):
    # When the browser hides the path, the frontend may still send the vfs:// fallback;
    # the server should reject it cleanly rather than try to open a junk path.
    resp = await client.post(
        f"/api/projects/{project.slug}/sources",
        json={"paths": ["vfs://Classroom of the Elite - S03E11 [1080p].mkv"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == []
    assert "vfs://" in body["skipped"][0]


async def test_import_folder_adds_all_videos(client, project: Project, tmp_path: Path):
    folder = tmp_path / "season3"
    folder.mkdir()
    (folder / "ep01.mkv").write_bytes(b"")
    (folder / "ep02.mp4").write_bytes(b"")
    (folder / "notes.txt").write_text("ignore")
    resp = await client.post(
        f"/api/projects/{project.slug}/sources/import-folder",
        json={"folder": str(folder)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["added"]) == 2
    assert body["source_root"] == str(folder.resolve())
    reloaded = Project.load(project.root)
    assert reloaded.source_root == str(folder.resolve())


async def test_import_folder_rejects_missing_dir(client, project: Project, tmp_path: Path):
    resp = await client.post(
        f"/api/projects/{project.slug}/sources/import-folder",
        json={"folder": str(tmp_path / "nope")},
    )
    assert resp.status_code == 400


async def test_reimport_brings_back_deleted_rows(client, project: Project, tmp_path: Path):
    folder = tmp_path / "vids"; folder.mkdir()
    (folder / "ep01.mkv").write_bytes(b"")
    (folder / "ep02.mkv").write_bytes(b"")
    await client.post(
        f"/api/projects/{project.slug}/sources/import-folder",
        json={"folder": str(folder)},
    )
    # Delete one row.
    await client.delete(f"/api/projects/{project.slug}/sources/0")
    # Reimport — the deleted row should come back.
    resp = await client.post(f"/api/projects/{project.slug}/sources/reimport")
    assert resp.status_code == 200
    reloaded = Project.load(project.root)
    assert len(reloaded.sources) == 2


async def test_reimport_400_when_no_source_root(client, project: Project):
    resp = await client.post(f"/api/projects/{project.slug}/sources/reimport")
    assert resp.status_code == 400


async def test_thumbnail_extracts_from_real_video(
    client, project: Project, tmp_path: Path,
):
    """End-to-end: ffmpeg generates a sample mp4, the endpoint produces a JPEG."""
    import shutil as _shutil
    import subprocess

    if _shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    vid = tmp_path / "ep.mp4"
    res = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=4:size=160x120:rate=24",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)],
        capture_output=True, text=True,
    )
    if res.returncode != 0 or not vid.exists():
        pytest.skip(f"ffmpeg sample generation failed: {res.stderr}")

    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    resp = await client.get(f"/api/projects/{project.slug}/sources/0/thumbnail")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content.startswith(b"\xff\xd8")
    # Cached file lives where we expect.
    assert (project.root / ".thumbs" / "ep.jpg").is_file()


async def test_thumbnail_serves_cached_jpeg(client, project: Project, tmp_path: Path):
    vid = tmp_path / "ep01.mkv"; vid.write_bytes(b"")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    # Pre-populate the thumbnail cache so we don't have to actually decode video.
    thumbs_dir = project.root / ".thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    (thumbs_dir / "ep01.jpg").write_bytes(b"\xff\xd8\xff\xe0FAKEJPEG")
    resp = await client.get(f"/api/projects/{project.slug}/sources/0/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content.startswith(b"\xff\xd8")


async def test_thumbnail_404_when_video_missing(client, project: Project, tmp_path: Path):
    vid = tmp_path / "gone.mkv"; vid.write_bytes(b"")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    vid.unlink()
    resp = await client.get(f"/api/projects/{project.slug}/sources/0/thumbnail")
    assert resp.status_code == 404


async def test_thumbnail_404_when_idx_out_of_range(client, project: Project):
    resp = await client.get(f"/api/projects/{project.slug}/sources/99/thumbnail")
    assert resp.status_code == 404


async def test_extract_enqueues_job(client, project: Project, tmp_path: Path):
    vid = tmp_path / "ep01.mkv"; vid.write_bytes(b"")
    img = tmp_path / "ref.png"; img.write_bytes(b"")
    project.add_ref(img)
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    resp = await client.post(f"/api/projects/{project.slug}/sources/0/extract")
    assert resp.status_code == 202
    assert "job_id" in resp.json()


# ---------------- segments + duration + preview/stream ----------------


def _make_synth_video(dest: Path, duration: int = 4) -> bool:
    """Generate a tiny H.264 MP4 via ffmpeg for tests that need a real video.
    Returns False so callers can ``pytest.skip`` cleanly if the local
    ffmpeg isn't available or fails."""
    import shutil as _shutil
    import subprocess

    if _shutil.which("ffmpeg") is None:
        return False
    res = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate=24",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)],
        capture_output=True, text=True,
    )
    return res.returncode == 0 and dest.exists()


async def test_duration_returns_and_caches(
    client, project: Project, tmp_path: Path,
):
    """``/duration`` probes ffprobe on first call and caches on the Source."""
    vid = tmp_path / "ep.mp4"
    if not _make_synth_video(vid):
        pytest.skip("ffmpeg unavailable for sample generation")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})

    resp = await client.get(f"/api/projects/{project.slug}/sources/0/duration")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["duration_seconds"] > 0
    assert body["fps"] > 0

    # Cached on the source.
    reloaded = Project.load(project.root)
    assert reloaded.sources[0].duration_seconds == body["duration_seconds"]
    assert reloaded.sources[0].fps == body["fps"]


async def test_put_segments_persists_and_merges(
    client, project: Project, tmp_path: Path,
):
    """Server merges overlapping ranges and sorts them ascending."""
    vid = tmp_path / "ep.mp4"
    if not _make_synth_video(vid, duration=10):
        pytest.skip("ffmpeg unavailable for sample generation")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})

    resp = await client.put(
        f"/api/projects/{project.slug}/sources/0/segments",
        json={"segments": [
            {"start_seconds": 5.0, "end_seconds": 7.0},
            {"start_seconds": 1.0, "end_seconds": 4.0},
            {"start_seconds": 3.0, "end_seconds": 6.0},  # overlaps both
        ]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Three ranges merge into one continuous [1, 7].
    assert body["segments"] == [{"start_seconds": 1.0, "end_seconds": 7.0}]

    reloaded = Project.load(project.root)
    assert len(reloaded.sources[0].segments) == 1
    assert reloaded.sources[0].segments[0].start_seconds == 1.0
    assert reloaded.sources[0].segments[0].end_seconds == 7.0


async def test_put_segments_rejects_invalid(
    client, project: Project, tmp_path: Path,
):
    """end <= start and end > duration both return 400."""
    vid = tmp_path / "ep.mp4"
    if not _make_synth_video(vid, duration=4):
        pytest.skip("ffmpeg unavailable for sample generation")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})

    # Inverted range.
    bad = await client.put(
        f"/api/projects/{project.slug}/sources/0/segments",
        json={"segments": [{"start_seconds": 3.0, "end_seconds": 1.0}]},
    )
    assert bad.status_code == 400

    # Past the end (4s clip, asking for 30s).
    past = await client.put(
        f"/api/projects/{project.slug}/sources/0/segments",
        json={"segments": [{"start_seconds": 1.0, "end_seconds": 30.0}]},
    )
    assert past.status_code == 400


async def test_put_segments_clears_with_empty_list(
    client, project: Project, tmp_path: Path,
):
    """An empty segments list reverts to whole-video behaviour."""
    vid = tmp_path / "ep.mp4"
    if not _make_synth_video(vid):
        pytest.skip("ffmpeg unavailable for sample generation")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    await client.put(
        f"/api/projects/{project.slug}/sources/0/segments",
        json={"segments": [{"start_seconds": 1.0, "end_seconds": 2.0}]},
    )
    resp = await client.put(
        f"/api/projects/{project.slug}/sources/0/segments",
        json={"segments": []},
    )
    assert resp.status_code == 200
    assert resp.json()["segments"] == []
    reloaded = Project.load(project.root)
    assert reloaded.sources[0].segments == []


async def test_segments_flip_extraction_cache_to_stale(
    client, project: Project, tmp_path: Path,
):
    """Stamping a fresh cache then editing segments should mark cache stale."""
    from neme_anima.config import Thresholds
    from neme_anima.extraction_cache import (
        cache_state_for_source,
        stamp_meta,
    )

    vid = tmp_path / "ep.mp4"
    if not _make_synth_video(vid):
        pytest.skip("ffmpeg unavailable for sample generation")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})

    # Manually seed the cache state to "current" by writing a parquet stub
    # + stamping the meta. cache_state() needs tracklets.parquet to exist
    # before it even consults the meta — without it the result is "none".
    cache_dir = project.cache_dir_for("ep")
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "tracklets.parquet").write_bytes(b"stub")
    project_for_stamp = Project.load(project.root)
    stamp_meta(project_for_stamp, "ep", Thresholds())

    fresh = Project.load(project.root)
    assert cache_state_for_source(fresh, 0, Thresholds()) == "current"

    # Edit segments via the API and re-check.
    await client.put(
        f"/api/projects/{project.slug}/sources/0/segments",
        json={"segments": [{"start_seconds": 1.0, "end_seconds": 2.0}]},
    )
    after = Project.load(project.root)
    assert cache_state_for_source(after, 0, Thresholds()) == "stale"


async def test_stream_serves_video_with_range_support(
    client, project: Project, tmp_path: Path,
):
    """``/stream`` returns the raw file with Range support."""
    vid = tmp_path / "ep.mp4"
    if not _make_synth_video(vid):
        pytest.skip("ffmpeg unavailable for sample generation")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})

    full = await client.get(f"/api/projects/{project.slug}/sources/0/stream")
    assert full.status_code == 200
    assert full.headers["content-type"] == "video/mp4"
    assert len(full.content) > 0

    ranged = await client.get(
        f"/api/projects/{project.slug}/sources/0/stream",
        headers={"Range": "bytes=0-15"},
    )
    assert ranged.status_code == 206
    assert len(ranged.content) == 16


async def test_preview_lazy_transcodes_and_caches(
    client, project: Project, tmp_path: Path,
):
    """First /preview call kicks off ffmpeg; the result lands in .previews/."""
    vid = tmp_path / "ep.mp4"
    if not _make_synth_video(vid):
        pytest.skip("ffmpeg unavailable for sample generation")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})

    resp = await client.get(f"/api/projects/{project.slug}/sources/0/preview")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "video/mp4"
    assert (project.root / ".previews" / "ep.mp4").is_file()

    # Second call serves the cached file (no transcode latency).
    resp2 = await client.get(f"/api/projects/{project.slug}/sources/0/preview")
    assert resp2.status_code == 200


async def test_segments_404_for_bad_idx(client, project: Project):
    resp = await client.put(
        f"/api/projects/{project.slug}/sources/99/segments",
        json={"segments": []},
    )
    assert resp.status_code == 404


async def test_segments_404_for_missing_video(
    client, project: Project, tmp_path: Path,
):
    vid = tmp_path / "gone.mp4"
    vid.write_bytes(b"x")
    await client.post(f"/api/projects/{project.slug}/sources", json={"paths": [str(vid)]})
    vid.unlink()
    resp = await client.put(
        f"/api/projects/{project.slug}/sources/0/segments",
        json={"segments": [{"start_seconds": 1.0, "end_seconds": 2.0}]},
    )
    assert resp.status_code == 404
