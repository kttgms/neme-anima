"""Download + locate the danbooru tag list used for tag autocomplete.

Single source of truth for the asset: the CLI (`neme-anima tags fetch`), the
install script, and the FastAPI route that serves it all resolve the path and
URL through here. The file is the a1111 `tagcomplete` format (no header):

    name,category,post_count,"alias1,alias2,..."

where category is 0=general 1=artist 3=copyright 4=character 5=meta. Names use
underscores; normalization to the on-disk space form happens client-side.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

# danbooru tags as of 2026-04-01 (post counts + aliases). Verified live.
DANBOORU_TAGS_URL = (
    "https://raw.githubusercontent.com/DraconicDragon/dbr-e621-lists-archive/"
    "main/tag-lists/danbooru/danbooru_2026-04-01_pt20-ia-dd.csv"
)
# Fallback if the primary archive is unreachable (older, but same format).
DANBOORU_TAGS_URL_FALLBACK = (
    "https://raw.githubusercontent.com/DominikDoom/a1111-sd-webui-tagcomplete/"
    "main/tags/danbooru.csv"
)


def tag_vocabulary_path(state_dir: Path) -> Path:
    """Path to the shared tag CSV inside a state dir."""
    return Path(state_dir) / "danbooru-tags.csv"


def default_tag_vocabulary_path() -> Path:
    """Resolve the tag CSV path the same way the server resolves its state dir:
    ``$NEME_STATE_DIR`` if set, else ``~/.neme-anima``."""
    sd = os.environ.get("NEME_STATE_DIR")
    if sd:
        base = Path(sd)
    else:
        from neme_anima.server.app import default_state_dir

        base = default_state_dir()
    return tag_vocabulary_path(base)


def _looks_like_tag_csv(path: Path) -> bool:
    """Cheap sanity check: first non-empty line is ``name,<int>,<count>,...``."""
    with open(path, encoding="utf-8", errors="replace") as f:
        first = f.readline().strip()
    parts = first.split(",")
    if len(parts) < 3:
        return False
    try:
        int(parts[1])
    except ValueError:
        return False
    return True


def fetch_tag_vocabulary(
    dest: Path,
    *,
    url: str = DANBOORU_TAGS_URL,
    force: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Download the danbooru tag CSV to ``dest``. Idempotent: skips an existing
    non-empty file unless ``force``. Validates the download looks like the
    expected CSV before committing it into place. ``transport`` is for tests."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with httpx.Client(transport=transport, follow_redirects=True, timeout=60.0) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
    if not _looks_like_tag_csv(tmp):
        tmp.unlink(missing_ok=True)
        raise ValueError(f"downloaded file from {url} does not look like a danbooru tag CSV")
    tmp.replace(dest)
    return dest
