"""Typer CLI: project subcommand group + extract/rerun."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from neme_anima.storage.project import Project

app = typer.Typer(
    name="neme-anima",
    help="Extract anime character crops from video for LoRA training.",
    no_args_is_help=True,
    add_completion=False,
)
project_app = typer.Typer(name="project", help="Manage projects (sources, refs, runs).")
app.add_typer(project_app, name="project")
character_app = typer.Typer(
    name="character", help="Per-character operations.",
)
app.add_typer(character_app, name="character")
tags_app = typer.Typer(name="tags", help="Manage the danbooru tag list used for autocomplete.")
app.add_typer(tags_app, name="tags")
cache_app = typer.Typer(name="cache", help="Manage the global scan cache.")
app.add_typer(cache_app, name="cache")
console = Console()


@character_app.command("copy")
def character_copy(
    src_folder: Path = typer.Argument(..., exists=True, file_okay=False,
                                      help="Source project folder."),
    character_slug: str = typer.Argument(..., help="Slug to copy."),
    dst_folder: Path = typer.Argument(..., exists=True, file_okay=False,
                                      help="Destination project folder."),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Don't write; just report."),
) -> None:
    """Copy a character (refs, sources, frames, sidecars, crops, identity)
    from one project to another. On per-object collisions in the destination,
    the imported object is dropped — see CopyReport for what was added vs
    skipped. Output is JSON on stdout."""
    import json as _json

    from neme_anima.storage.character_copy import copy_character_to_project

    src = Project.load(src_folder)
    dst = Project.load(dst_folder)
    try:
        report = copy_character_to_project(
            src=src, src_character_slug=character_slug,
            dst=dst, dry_run=dry_run,
        )
    except KeyError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=2) from e
    except ValueError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=3) from e
    print(_json.dumps(report.to_dict(), indent=2))


@project_app.command("create")
def project_create(
    folder: Path = typer.Argument(..., help="Path of the project folder to create."),
    name: str = typer.Option(..., "--name", "-n", help="Display name."),
) -> None:
    """Create a new project folder."""
    try:
        p = Project.create(folder, name=name)
    except FileExistsError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1)
    console.print(f"[green]created[/green] {p.root}  name={p.name}  slug={p.slug}")


@project_app.command("add-video")
def project_add_video(
    project_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    video: Path = typer.Argument(..., exists=True, dir_okay=False),
) -> None:
    """Append a video to the project's sources."""
    p = Project.load(project_dir)
    s = p.add_source(video)
    console.print(f"[green]+ source[/green] {Path(s.path).name}")


@project_app.command("add-ref")
def project_add_ref(
    project_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    image: Path = typer.Argument(..., exists=True, dir_okay=False),
) -> None:
    """Append a reference image to the project."""
    p = Project.load(project_dir)
    r = p.add_ref(image)
    console.print(f"[green]+ ref[/green] {Path(r.path).name}")


@project_app.command("extract")
def project_extract(
    project_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    video: str | None = typer.Option(None, "--video", "-v",
                                     help="Video stem to extract; default = all sources sequentially."),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of parallel workers for character processing."),
    parallel_gpu: bool = typer.Option(False, "--parallel-gpu",
                                      help="Allow CCIP inference to run concurrently across characters. "
                                           "Requires enough VRAM for multiple concurrent CCIP sessions."),
    no_global_cache: bool = typer.Option(False, "--no-global-cache",
                                         help="Disable read/write of the shared global scan cache."),
) -> None:
    """Run extraction on one or all sources in this project."""
    from neme_anima.config import PipelineConfig
    from neme_anima.isolated_runner import run_extract_isolated

    p = Project.load(project_dir)
    indices = (
        [i for i, s in enumerate(p.sources) if Path(s.path).stem == video]
        if video else list(range(len(p.sources)))
    )
    if not indices:
        console.print(f"[red]error:[/red] no matching source")
        raise typer.Exit(code=1)

    cfg = PipelineConfig(
        parallel_workers=workers,
        parallel_gpu=parallel_gpu,
        use_global_cache=not no_global_cache,
    )
    for i in indices:
        run_extract_isolated(project=p, source_idx=i, pipeline_cfg=cfg)


@project_app.command("rerun")
def project_rerun(
    project_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    video: str = typer.Option(..., "--video", "-v", help="Video stem to rerun."),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of parallel workers for character processing."),
    parallel_gpu: bool = typer.Option(False, "--parallel-gpu",
                                      help="Allow CCIP inference to run concurrently across characters."),
    no_global_cache: bool = typer.Option(False, "--no-global-cache",
                                         help="Disable read/write of the shared global scan cache."),
) -> None:
    """Re-run with cached detections + current thresholds."""
    from neme_anima.config import PipelineConfig
    from neme_anima.isolated_runner import run_rerun_isolated

    p = Project.load(project_dir)
    cfg = PipelineConfig(
        parallel_workers=workers,
        parallel_gpu=parallel_gpu,
        use_global_cache=not no_global_cache,
    )
    run_rerun_isolated(project=p, video_stem=video, pipeline_cfg=cfg)


@project_app.command("scan")
def project_scan(
    project_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    video: str | None = typer.Option(None, "--video", "-v",
                                     help="Video stem to scan; default = all sources sequentially."),
    no_global_cache: bool = typer.Option(False, "--no-global-cache",
                                         help="Disable read/write of the shared global scan cache."),
) -> None:
    """Pre-scan video sources to generate scene/detect/track cache."""
    from neme_anima.config import PipelineConfig
    from neme_anima.isolated_runner import run_scan_isolated

    p = Project.load(project_dir)
    indices = (
        [i for i, s in enumerate(p.sources) if Path(s.path).stem == video]
        if video else list(range(len(p.sources)))
    )
    if not indices:
        console.print(f"[red]error:[/red] no matching source")
        raise typer.Exit(code=1)

    cfg = PipelineConfig(use_global_cache=not no_global_cache)
    for i in indices:
        run_scan_isolated(project=p, source_idx=i, pipeline_cfg=cfg)


@cache_app.command("info")
def cache_info() -> None:
    """Show global scan cache status and size."""
    from neme_anima.extraction_cache import list_caches

    caches = list_caches()
    if not caches:
        console.print("Global cache is empty.")
        return

    total_size = sum(c.size_bytes for c in caches)
    
    from rich.table import Table
    table = Table(title=f"Global Scan Cache ({len(caches)} videos, {total_size / 1024 / 1024:.1f} MB)")
    table.add_column("Video Hash (Prefix)")
    table.add_column("Date Cached")
    table.add_column("Size (MB)", justify="right")
    table.add_column("Current", justify="center")

    for c in caches:
        table.add_row(
            c.video_hash[:12] + "...",
            str(c.mtime.date()),
            f"{c.size_bytes / 1024 / 1024:.1f}",
            "✅" if c.is_current else "❌"
        )
    console.print(table)


@cache_app.command("clear")
def cache_clear() -> None:
    """Clear all entries from the global scan cache."""
    from neme_anima.extraction_cache import purge_cache

    freed = purge_cache(delete_all=True)
    console.print(f"[green]Cleared global cache[/green] (freed {freed / 1024 / 1024:.1f} MB).")



@project_app.command("tag-loras")
def project_tag_loras(
    project_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    run_name: str | None = typer.Option(
        None, "--run", help="Training run name to tag; default = all runs.",
    ),
) -> None:
    """Embed neme-anima provenance metadata into saved LoRA safetensors."""
    import json as _json

    from neme_anima import training as training_lib

    p = Project.load(project_dir)
    if run_name is not None:
        run_dirs = [p.training_runs_dir / run_name]
        if not run_dirs[0].is_dir():
            console.print(f"[red]error:[/red] unknown run: {run_name}")
            raise typer.Exit(code=1)
    elif p.training_runs_dir.is_dir():
        run_dirs = sorted(d for d in p.training_runs_dir.iterdir() if d.is_dir())
    else:
        run_dirs = []

    tagged: list[str] = []
    try:
        for run_dir in run_dirs:
            tagged.extend(training_lib.tag_run_safetensors(p, run_dir))
    except training_lib.SafetensorsMetadataError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=2) from e
    print(_json.dumps({
        "project": p.slug,
        "runs_checked": [d.name for d in run_dirs],
        "tagged_count": len(tagged),
        "tagged_files": tagged,
    }, indent=2))


@tags_app.command("fetch")
def tags_fetch(
    force: bool = typer.Option(False, "--force", help="Re-download even if present."),
    url: str = typer.Option(None, "--url", help="Override the source URL."),
) -> None:
    """Download the danbooru tag list (for tag autocomplete) into the state dir."""
    from neme_anima.tag_vocabulary import (
        DANBOORU_TAGS_URL,
        default_tag_vocabulary_path,
        fetch_tag_vocabulary,
    )

    dest = default_tag_vocabulary_path()
    if dest.exists() and dest.stat().st_size > 0 and not force:
        console.print(
            f"[green]present[/green] {dest} "
            f"({dest.stat().st_size // 1024} KiB) — use --force to re-download"
        )
        return
    console.print(f"downloading danbooru tag list → {dest} …")
    try:
        fetch_tag_vocabulary(dest, url=url or DANBOORU_TAGS_URL, force=force)
    except Exception as e:  # noqa: BLE001 — surface any network/parse error to the user
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e
    console.print(f"[green]done[/green] {dest} ({dest.stat().st_size // 1024} KiB)")


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(9999, help="Port to bind; pass 0 to pick a free one."),
    no_browser: bool = typer.Option(False, "--no-browser",
                                    help="Don't auto-open the browser."),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Construct the app and exit (for tests)."),
) -> None:
    """Start the local web UI server."""
    import os
    import socket
    import threading
    import webbrowser

    import uvicorn

    from neme_anima.server.app import create_app

    state_dir = os.environ.get("NEME_STATE_DIR")
    create_kwargs = {"state_dir": Path(state_dir)} if state_dir else {}
    fastapi_app = create_app(**create_kwargs)

    if dry_run:
        return

    # Pick a free port if 0.
    bind_port = port
    if bind_port == 0:
        with socket.socket() as s:
            s.bind((host, 0))
            bind_port = s.getsockname()[1]

    url = f"http://{host}:{bind_port}"
    console.print(f"[bold green]neme-anima[/bold green] :: serving on {url}")

    if not no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(fastapi_app, host=host, port=bind_port, log_level="info")


if __name__ == "__main__":
    app()
