"""REST route serving the danbooru tag-vocabulary CSV for autocomplete."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import FileResponse

from neme_anima.tag_vocabulary import tag_vocabulary_path

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("/vocabulary")
async def get_tag_vocabulary(request: Request) -> FileResponse:
    """Serve the downloaded danbooru tag CSV. 404 if it hasn't been fetched."""
    path = tag_vocabulary_path(request.app.state.state_dir)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="tag vocabulary not downloaded; run `neme-anima tags fetch`",
        )
    return FileResponse(path, media_type="text/csv")
