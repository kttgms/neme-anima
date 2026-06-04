"""Download + locate the danbooru tag list used for tag autocomplete.

Single source of truth for the asset: the CLI (`neme-anima tags fetch`), the
install script, and the FastAPI route that serves it all resolve the path and
URL through here. The file is the a1111 `tagcomplete` format (no header):

    name,category,post_count,"alias1,alias2,..."

where category is 0=general 1=artist 3=copyright 4=character 5=meta. Names use
underscores; normalization to the on-disk space form happens client-side.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# danbooru's numeric category column -> human label. Matches the contract in
# the module docstring and the frontend's tagSearch.ts.
TAG_CATEGORY = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}

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


def _norm(s: str) -> str:
    """Normalize a tag for matching: underscores->spaces, trimmed, lowercased.

    This is the on-disk contract — danbooru stores ``long_hair`` but WD14 and
    our sidecars use ``long hair`` (``TagConfig.no_underline=True``). Matching
    underscore/space/case-insensitively keeps suggestions aligned with what is
    actually stored on disk.
    """
    return s.replace("_", " ").strip().lower()


@dataclass
class DanbooruIndex:
    """In-memory, searchable view of ``danbooru-tags.csv``.

    Used server-side by the LLM tag-review tool to (a) confirm a proposed tag
    is a real danbooru tag and (b) resolve it to its canonical, space-form name
    (e.g. an alias like ``yellow_hair`` -> ``blonde hair``). Tag names are
    returned in space form to match the sidecar contract.
    """

    # (name_space, category_int, post_count) sorted by post_count descending,
    # so "most popular substring match" is a prefix of a single linear scan.
    entries: list[tuple[str, int, int]] = field(default_factory=list)
    # normalized name OR alias -> canonical name in space form.
    _canon: dict[str, str] = field(default_factory=dict)
    # parallel to entries: precomputed haystack (name + aliases, normalized).
    _haystacks: list[str] = field(default_factory=list)

    @classmethod
    def from_csv(cls, path: Path) -> DanbooruIndex:
        rows: list[tuple[str, int, int, str]] = []
        canon: dict[str, str] = {}
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.reader(f):
                if len(row) < 3:
                    continue
                name, cat_s, count_s = row[0], row[1], row[2]
                try:
                    cat_i, count_i = int(cat_s), int(count_s)
                except ValueError:
                    continue
                aliases = row[3] if len(row) > 3 else ""
                name_space = name.replace("_", " ")
                rows.append((name_space, cat_i, count_i, _norm(name + " " + aliases)))
                canon.setdefault(_norm(name), name_space)
                for al in aliases.split(","):
                    if al.strip():
                        canon.setdefault(_norm(al), name_space)
        rows.sort(key=lambda r: -r[2])
        idx = cls()
        idx.entries = [(n, c, p) for (n, c, p, _h) in rows]
        idx._haystacks = [r[3] for r in rows]
        idx._canon = canon
        return idx

    def __len__(self) -> int:
        return len(self.entries)

    def search(self, query: str, limit: int = 8) -> list[dict]:
        """Substring search over names+aliases, ranked by post count (desc)."""
        q = _norm(query)
        if not q:
            return []
        out: list[dict] = []
        for (name_space, cat, count), hay in zip(self.entries, self._haystacks, strict=True):
            if q in hay:
                out.append({
                    "tag": name_space,
                    "category": TAG_CATEGORY.get(cat, str(cat)),
                    "post_count": count,
                })
                if len(out) >= max(1, limit):
                    break
        return out

    def canonicalize(self, tag: str) -> tuple[str, bool]:
        """Resolve ``tag`` to its canonical danbooru name in space form.

        Returns ``(canonical_name, is_real)``. ``is_real`` is False when the
        tag (or any alias of it) isn't in the vocabulary at all.
        """
        canon = self._canon.get(_norm(tag))
        return (canon, True) if canon else (tag, False)


# Cache parsed indexes by (path, mtime, size) so a re-download invalidates the
# cache but repeated reviews don't re-parse the ~200k-row CSV every request.
_INDEX_CACHE: dict[tuple[str, float, int], DanbooruIndex] = {}


def load_index(path: Path) -> DanbooruIndex:
    """Load (and cache) the danbooru search index for ``path``."""
    path = Path(path)
    st = path.stat()
    key = (str(path), st.st_mtime, st.st_size)
    cached = _INDEX_CACHE.get(key)
    if cached is None:
        cached = DanbooruIndex.from_csv(path)
        _INDEX_CACHE.clear()  # only the latest version is worth keeping
        _INDEX_CACHE[key] = cached
    return cached
